"""Exercise the real first-run dialog and optionally the packaged setup worker."""
import json
import locale
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from streamcut.dialogs import default_settings
from streamcut.setup_dialog import SetupDialog
from streamcut.widgets import THEME

locale.setlocale(locale.LC_NUMERIC, "C")
folder = Path("local/setup-smoke").resolve()
folder.mkdir(parents=True, exist_ok=True)
os.environ["STREAMCUT_DATA_DIR"] = str(folder/"data")
app = QApplication(sys.argv)
app.setOrganizationName("StreamCutTest")
app.setApplicationName("FirstRunSmoke")
app.setStyle("Fusion")
app.setStyleSheet(THEME)
settings = default_settings()
settings["models_dir"] = str(Path(sys.argv[2] if len(sys.argv) > 2 else "models").resolve())
dialog = SetupDialog(settings)
events = []
dialog.worker.event.connect(events.append)
if len(sys.argv) > 1:
    helper = str(Path(sys.argv[1]).resolve())
    dialog.worker.start = lambda args: dialog.worker.process.start(helper, ["--worker", *args])
dialog.show()
QTest.qWait(100)
QTest.mouseClick(dialog.start, Qt.MouseButton.LeftButton)
started = time.monotonic()
while time.monotonic()-started < 300 and (dialog.worker.running() or not dialog.finish.isEnabled()):
    app.processEvents()
    QTest.qWait(30)
try:
    assert dialog.success, events
    assert any(e["kind"] == "hardware" for e in events), events
    result = next(e["result"] for e in reversed(events) if e["kind"] == "done")
    if not result["cuda_devices"]:
        assert result["whisper_device"] == "cpu", result
    assert settings["whisper_device"] == "auto"
    # The bounded log must remain usable after thousands of download updates.
    for i in range(300):
        dialog.on_event({"kind": "status", "text": f"Проверка прогресса {i}"})
    assert dialog.output.document().blockCount() <= 250
    dialog.on_event(events[-1])
    dialog.grab().save(str(folder/"setup.png"))
    (folder/"report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("First-run setup passed:", json.dumps(result, ensure_ascii=False), flush=True)
finally:
    dialog.worker.stop()
    dialog.close()
    app.processEvents()
