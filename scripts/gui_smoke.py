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
export_started = False
ticks = 0
heartbeat = QTimer()
def beat():
    global ticks
    ticks += 1
heartbeat.timeout.connect(beat)
heartbeat.start(50)


def step():
    global index, export_started
    try:
        if index < 3:
            window.seek(positions[index])
            window.preview_timer.stop()
            index += 1
            QTimer.singleShot(2200, verify)
        else:
            if not export_started:
                export_started = True
                window.project.settings["export_dir"] = str(Path("local/smoke/exports").resolve())
                for clip in window.project.clips:
                    assert window.enqueue(clip=clip)
                window.seek(window.project.duration/3)
                window.player.pause = False
                window.preview_timer.stop()
                QTimer.singleShot(300, finish_exports)
                return
            window.save()
            folder = window.store.folder
            originals = [(c.id, c.start, c.end) for c in window.project.clips]
            window.open_project(folder)
            window.preview_timer.stop()
            assert [(c.id, c.start, c.end) for c in window.project.clips] == originals
            assert len(originals) == 3
            report.update(restored=True, clips=originals, project=str(folder))
            report.update(ui_heartbeat_ticks=ticks, export_jobs=[j.model_dump(include={"state", "progress", "destination"}) for j in window.project.jobs])
            Path("local").mkdir(exist_ok=True)
            window.grab().save("local/gui-smoke.png")
            Path("local/gui-smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False), flush=True)
            window.close()
            app.quit()
    except Exception as exc:
        print("ERROR:", exc, flush=True)
        app.exit(1)


def finish_exports():
    if any(j.state in ("ожидает", "выполняется") for j in window.project.jobs):
        QTimer.singleShot(300, finish_exports)
        return
    try:
        assert all(j.state == "экспортирован" for j in window.project.jobs), [j.error for j in window.project.jobs]
        assert window.player.time_pos > window.project.duration/3
        assert ticks > 50
        window.player.pause = True
        report["playback_during_exports"] = True
        QTimer.singleShot(300, step)
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
