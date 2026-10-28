import sys

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from streamcut.domain import Clip, Span
from streamcut.ui import MainWindow

app = QApplication(sys.argv)
app.setOrganizationName("StreamCutTest")
app.setApplicationName("KeyboardSmoke")
window = MainWindow()
window.project.duration = 100
clip = Clip(spans=[Span(start=10, end=30)])
window.project.clips = [clip]
window.show()
window.select_clip(clip)
window.title.setFocus()
window.title.selectAll()
QTest.keyClicks(window.title, "i o text with spaces")
assert window.title.text() == "i o text with spaces"
assert clip.start == 10 and clip.end == 30
window.note.setFocus()
QTest.keyClicks(window.note, "i o note")
assert window.note.toPlainText() == "i o note"
QTest.keyClick(window.note, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
assert window.note.toPlainText() != "i o note"
assert clip.start == 10 and clip.end == 30
window.settings_before_selection = {"visual": (True, "выключен")}
window.selection_overrides = {"visual": "подробный"}
window.project.settings.update(visual="подробный", export_dir="user-changed-folder")
window.analysis_finished()
assert window.project.settings["visual"] == "выключен"
assert window.project.settings["export_dir"] == "user-changed-folder"
window.close()
print("Keyboard input and native text undo passed", flush=True)
