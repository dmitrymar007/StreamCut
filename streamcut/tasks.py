import json
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QThread, Signal

from .media import ROOT, export_clip
from .preview_cache import PreviewCache, preview_plan


class WorkerProcess(QObject):
    event = Signal(dict)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(ROOT))
        self.process.readyReadStandardOutput.connect(self.read)
        self.process.readyReadStandardError.connect(self.read_error)
        self.process.finished.connect(self.end)
        self.process.errorOccurred.connect(self.process_error)
        self.buffer = b""
        self.stderr = ""
        self.had_terminal = False

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.had_terminal = True
            self.event.emit({"kind": "error", "text": self.process.errorString()})
            self.finished.emit()

    def start(self, args):
        self.buffer = b""
        self.stderr = ""
        self.had_terminal = False
        if getattr(sys, "frozen", False):
            helper = str(Path(sys.executable).with_name("StreamCut-worker.exe"))
            self.process.start(helper, ["--worker", *args])
        else:
            self.process.start(sys.executable, ["-u", "-m", "streamcut.worker", *args])

    def read(self):
        self.buffer += bytes(self.process.readAllStandardOutput())
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            try:
                event = json.loads(line.decode("utf-8"))
                if event.get("kind") in ("done", "error"):
                    self.had_terminal = True
                self.event.emit(event)
            except (UnicodeDecodeError, ValueError):
                continue

    def read_error(self):
        self.stderr = (self.stderr + bytes(self.process.readAllStandardError()).decode("utf-8", "replace"))[-5000:]

    def end(self, code, status):
        self.read()
        self.read_error()
        if code and not self.had_terminal:
            self.event.emit({"kind": "error", "text": self.stderr or "Задача прервана; завершённые блоки сохранены"})
        self.finished.emit()

    def running(self):
        return self.process.state() != QProcess.ProcessState.NotRunning

    def stop(self):
        self.process.kill()
        self.process.waitForFinished(3000)


class ExportThread(QThread):
    progress = Signal(str, float)
    result = Signal(str, str, str)

    def __init__(self, job):
        super().__init__()
        self.job = job.model_copy(deep=True)
        self.cancel = threading.Event()

    def run(self):
        try:
            export_clip(self.job, self.cancel, lambda p: self.progress.emit(self.job.id, p))
            self.result.emit(self.job.id, "экспортирован", "")
        except InterruptedError as exc:
            self.result.emit(self.job.id, "отменён", str(exc))
        except Exception as exc:
            self.result.emit(self.job.id, "ошибка экспорта", str(exc))


class FunctionThread(QThread):
    result = Signal(object)
    error = Signal(str)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function

    def run(self):
        try:
            self.result.emit(self.function())
        except Exception as exc:
            self.error.emit(str(exc))


class PreviewThread(QThread):
    frameReady = Signal(float, float, bytes)
    waveReady = Signal(int, list)
    failed = Signal(str)

    def __init__(self, source, audio, start, end, folder, identity, duration, pixels, position):
        super().__init__()
        self.source, self.audio = source, audio
        self.start_at, self.end_at, self.folder = start, end, Path(folder)
        self.identity, self.duration, self.pixels, self.position = identity, duration, pixels, position
        self.cancel = threading.Event()

    def run(self):
        try:
            cache = PreviewCache(self.folder, self.source, self.identity, self.audio, self.duration)
            step, frames, chunks = preview_plan(self.start_at, self.end_at, self.duration, self.pixels, self.position)
            for index in range(max(len(frames), len(chunks) if self.audio >= 0 else 0)):
                if self.cancel.is_set():
                    return
                if index < len(frames):
                    stamp = frames[index]
                    self.frameReady.emit(stamp, step, cache.frame(stamp, self.cancel))
                if self.audio >= 0 and index < len(chunks):
                    chunk = chunks[index]
                    self.waveReady.emit(chunk, cache.waveform(chunk, self.cancel))
        except InterruptedError:
            pass
        except Exception as exc:
            # Keep already displayed data on extraction failure.
            self.failed.emit(str(exc))
