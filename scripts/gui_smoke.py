"""Real libmpv + Qt smoke test against a supplied source; never modifies it."""
import json
import locale
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from streamcut.ui import MainWindow
from streamcut.widgets import THEME

locale.setlocale(locale.LC_NUMERIC, "C")
app = QApplication(sys.argv)
app.setOrganizationName("StreamCutTest")
app.setApplicationName("StreamCutTest")
app.setStyleSheet(THEME)
window = MainWindow()
window.show()
window.project.settings["models_dir"] = str(Path("local/smoke/models").resolve())
window.open_source(sys.argv[1])
window.preview_timer.stop()
report = {"source": window.project.source, "duration": window.project.duration, "player": bool(window.player)}
positions = [1, window.project.duration/2, window.project.duration-5]
index = 0


def step():
    global index
    try:
        if index < 3:
            window.seek(positions[index])
            window.preview_timer.stop()
            index += 1
            QTimer.singleShot(2200, verify)
        else:
            window.save()
            folder = window.store.folder
            originals = [(c.id, c.start, c.end) for c in window.project.clips]
            window.open_project(folder)
            window.preview_timer.stop()
            assert [(c.id, c.start, c.end) for c in window.project.clips] == originals
            assert len(originals) == 3
            report.update(restored=True, clips=originals, project=str(folder))
            Path("local").mkdir(exist_ok=True)
            window.grab().save("local/gui-smoke.png")
            Path("local/gui-smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False), flush=True)
            window.close()
            app.quit()
    except Exception as exc:
        print("ERROR:", exc, flush=True)
        app.exit(1)


def verify():
    try:
        actual = window.player.time_pos
        assert actual is not None, window.player_error
        assert abs(actual-positions[index-1]) < 0.5, (actual, positions[index-1])
        report.setdefault("seek", []).append({"requested": positions[index-1], "actual": actual})
        window.add_clip()
        window.apply_bounds(max(0, positions[index-1]-0.5), min(window.project.duration, positions[index-1]+2))
        QTimer.singleShot(100, step)
    except Exception as exc:
        print("ERROR:", exc, flush=True)
        app.exit(1)


QTimer.singleShot(1600, step)
sys.exit(app.exec())
