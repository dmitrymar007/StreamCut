import json
import sys
import threading
import wave
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QThread, Signal

from .media import ROOT, export_clip, extract_audio, extract_frame


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
        self.process.errorOccurred.connect(lambda e: self.event.emit(
            {"kind": "error", "text": self.process.errorString()}) if e == QProcess.ProcessError.FailedToStart else None)
        self.buffer = b""
        self.stderr = ""
        self.had_terminal = False

    def start(self, args):
        self.buffer = b""
        self.stderr = ""
        self.had_terminal = False
        if getattr(sys, "frozen", False):
            self.process.start(sys.executable, ["--worker", *args])
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


class PreviewThread(QThread):
    ready = Signal(list, list)

    def __init__(self, source, audio, start, end, folder):
        super().__init__()
        self.source, self.audio = source, audio
        self.start_at, self.end_at, self.folder = start, end, Path(folder)

    def run(self):
        try:
            import numpy as np
            duration = self.end_at-self.start_at
            audio = self.folder / "preview.wav"
            peaks = []
            if self.audio >= 0:
                extract_audio(self.source, self.audio, self.start_at, duration, audio)
                with wave.open(str(audio)) as f:
                    data = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
                groups = np.array_split(data, min(700, len(data)))
                peaks = [(self.start_at+i*duration/len(groups), float(np.max(np.abs(g.astype(float))))/32768)
                         for i, g in enumerate(groups) if len(g)]
                audio.unlink(missing_ok=True)
            frames = []
            for i in range(6):
                stamp = self.start_at+duration*i/6
                path = self.folder / f"preview-{i}.jpg"
                extract_frame(self.source, stamp, path)
                frames.append((stamp, path.read_bytes()))
            self.ready.emit(peaks, frames)
        except Exception:
            # Preview failure never blocks playback or exports.
            self.ready.emit([], [])
