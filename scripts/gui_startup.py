import locale
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from streamcut.domain import Clip, Project, Span
from streamcut.media import probe
from streamcut.storage import ProjectStore
from streamcut.ui import MainWindow
from streamcut.widgets import THEME

locale.setlocale(locale.LC_NUMERIC, "C")
app = QApplication(sys.argv)
app.setOrganizationName("StreamCutTest")
app.setApplicationName("StreamCutStartupTest")
path = sys.argv[1]
project = Project(source=path, **probe(path))
project.clips.append(Clip(title="Сохранённый момент", spans=[Span(start=211.93, end=222.29)]))
store = ProjectStore(Path("local/startup").resolve())
store.save(project)
window = MainWindow()
app.setStyleSheet(THEME)
errors = []
window.error = lambda error: errors.append(str(error))
window.show()
window.open_project(store.folder)
window.preview_timer.stop()


def verify():
    try:
        assert not errors, errors
        assert abs(window.player.time_pos-211.93) < 0.1, window.player.time_pos
        print("Startup project seek passed:", window.player.time_pos, flush=True)
        window.close()
        app.quit()
    except Exception as exc:
        print("ERROR:", exc, flush=True)
        app.exit(1)


QTimer.singleShot(2500, verify)
sys.exit(app.exec())
