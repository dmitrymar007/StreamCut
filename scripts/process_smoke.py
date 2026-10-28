"""Exercise Qt's UTF-8 pipe with the packaged helper and a real local ASR model."""
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QTimer

from streamcut.tasks import WorkerProcess

app = QCoreApplication(sys.argv)
worker = WorkerProcess()
events = []
stage = 0
worker.event.connect(events.append)
helper = str(Path("dist/StreamCut/StreamCut-worker.exe").resolve())


def finished():
    global stage
    try:
        if stage == 0:
            assert any(e["kind"] == "error" and "ConnectError" in e.get("text", "") for e in events), events
            stage = 1
            events.clear()
            worker.had_terminal = False
            worker.stderr = ""
            QTimer.singleShot(0, lambda: worker.process.start(helper,
                ["--worker", "test-asr", sys.argv[1], "--model", "small", "--folder", str(Path("models").resolve())]))
        else:
            assert any(e["kind"] == "done" and e.get("text", "").startswith("Первые 30 секунд:") for e in events), events
            print("Packaged helper: offline error and Russian UTF-8 ASR result passed", flush=True)
            app.quit()
    except Exception as exc:
        print("ERROR:", exc, flush=True)
        app.exit(1)


worker.finished.connect(finished)
worker.process.start(helper, ["--worker", "test-text", "test-model", "--url", "http://127.0.0.1:1"])
QTimer.singleShot(90000, lambda: (worker.stop(), app.exit(1)))
sys.exit(app.exec())
