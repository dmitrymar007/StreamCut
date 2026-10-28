"""Native editor acceptance: edits, gestures, live framing, progressive cache."""
import json
import locale
import sys
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from streamcut.domain import output_dimensions
from streamcut.preview_cache import PreviewCache
from streamcut.ui import MainWindow
from streamcut.widgets import THEME

locale.setlocale(locale.LC_NUMERIC, "C")
app = QApplication(sys.argv)
app.setOrganizationName("StreamCutTest")
app.setApplicationName("Editor02Smoke")
app.setStyle("Fusion")
app.setStyleSheet(THEME)
folder = Path("local/editor-smoke").resolve()
folder.mkdir(parents=True, exist_ok=True)
window = MainWindow()
errors = []
window.error = lambda error: errors.append(str(error))
window.project.settings["models_dir"] = str(folder/"models")
window.show()
window.open_source(sys.argv[1])
window.preview_timer.stop()
report = {}


def wait_until(condition, timeout=10):
    started = time.monotonic()
    while time.monotonic()-started < timeout:
        app.processEvents()
        if condition():
            return
        QTest.qWait(30)
    raise AssertionError("Timeout waiting for editor state")


try:
    wait_until(lambda: window.player and window.player.time_pos is not None)
    window.edit_half(True)
    half = window.current()
    assert abs(half.duration-window.project.duration/2) < 0.001
    window.project.settings["queue_paused"] = True
    destination = folder/"queued-snapshot.mp4"
    assert window.enqueue(destination=str(destination))
    snapshot = window.project.jobs[-1]
    assert snapshot.options.height == 0 and snapshot.options.mode == "быстрый"
    identity = half.id
    window.delete_clip()
    assert not any(c.id == identity for c in window.project.clips)
    assert snapshot.clip.id == identity and snapshot.clip.duration > 100
    window.undo()
    assert window.current().id == identity
    window.redo()
    assert not any(c.id == identity for c in window.project.clips)
    window.undo()
    assert window.current().id == identity
    report["delete_undo_redo_queue_snapshot"] = True
    assert not destination.exists()

    window.edit_full_video()
    window.seek(window.project.duration/4)
    window.trim_at_cursor(True)
    assert abs(window.current().duration-window.project.duration/4) < 0.01
    report["whole_source_trim"] = True
    window.set_output_layout("9:16")
    assert output_dimensions(window.export_options(), window.project.width, window.project.height) == (1080, 1920)
    assert window.export_options().mode == "точный"
    QTest.qWait(1000)
    output = window.player.video_out_params or {}
    assert output.get("w", 10000) < output.get("h", 0), output
    report["live_shorts"] = output
    window.player.command("screenshot-to-file", str(folder/"shorts-preview.jpg"), "video")
    window.set_output_layout("исходный")
    QTest.qWait(300)
    assert window.export_options().mode == "быстрый"
    assert window.export_options().height == 0

    window.source_range(10, 20, "Проверка курсора")
    window.apply_bounds(10, 25)
    assert "00:00:15" in window.output_info.text()
    window.apply_bounds(10, 20)
    window.timeline.window = 120
    window.timeline.view_start = 400
    window.seek(450)
    window.stop_preview(wait=True)
    QTest.qWait(100)
    cache = PreviewCache(window.store.folder/"cache", window.project.source,
                         window.project.fingerprint, window.project.audio, window.project.duration)
    import threading
    started = time.monotonic()
    frame = cache.frame(450, threading.Event())
    report["cold_frame_seconds"] = time.monotonic()-started
    started = time.monotonic()
    assert cache.frame(450, threading.Event()) == frame
    report["warm_frame_seconds"] = time.monotonic()-started
    window.preview_frame(window._preview_generation, 450, 10, frame)
    peaks = cache.waveform(15, threading.Event())
    window.preview_wave(window._preview_generation, 15, peaks)
    before = window.timeline.limits()
    widget = window.timeline
    x1, x2 = round(widget.width()*0.3), round(widget.width()*0.65)
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=QPoint(x1, 12))
    QTest.mouseMove(widget, QPoint(x2, 12), 40)
    window.tick()
    assert widget.limits() == before
    expected = widget.seconds(x2)
    assert abs(window.position-expected) < 0.01
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=QPoint(x2, 12))
    assert 450 in widget._frames and 15 in widget._waves
    report["ruler_scrub_stable_viewport"] = True

    QTest.qWait(200)
    playhead_x = widget.x(window.position)
    origin = widget.limits()[0]
    x = round(widget.width()*0.85)
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=QPoint(x, 60))
    QTest.mouseMove(widget, QPoint(x-100, 60), 40)
    assert widget.limits()[0] > origin
    assert abs(widget.x(window.position)-playhead_x) < 2
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=QPoint(x-100, 60))
    report["drag_track_under_cursor"] = True

    anchor = QPointF(widget.width()*0.6, 70)
    anchor_time = widget.seconds(anchor.x())
    old_window = widget.window
    event = QWheelEvent(anchor, QPointF(widget.mapToGlobal(anchor.toPoint())), QPoint(), QPoint(0, 120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    app.sendEvent(widget, event)
    assert widget.window < old_window
    assert abs(widget.seconds(anchor.x())-anchor_time) < 0.001
    report["zoom_anchor"] = True

    window.timeline.pan_to(12000)
    window.timeline_view_changed()
    wait_until(lambda: any(12000 <= stamp < 12200 for stamp in widget._frames), 12)
    report["progressive_first_frame_while_loading"] = bool(window.preview_thread)
    assert report["progressive_first_frame_while_loading"]
    assert 450 in widget._frames
    window.timeline.pan_to(3000)
    window.timeline.pan_to(6000)
    window.timeline.pan_to(15000)
    wait_until(lambda: window.preview_thread and abs(window.preview_thread.start_at-15000) < 1, 8)
    report["obsolete_request_cancelled"] = True
    wait_until(lambda: any(15000 <= stamp < 15100 for stamp in widget._frames), 10)
    wait_until(lambda: any(15000 <= chunk*30 < 15100 for chunk in widget._waves), 10)
    wait_until(lambda: window.preview_thread is None and not window.preview_timer.isActive(), 35)
    report["visible_range_finished"] = True
    window.seek(15045)
    wait_until(lambda: abs((window.player.time_pos or 0)-15045) < 0.15, 5)
    window.player.command("screenshot-to-file", str(folder/"original-preview.jpg"), "video")
    window.stop_preview(wait=True)
    QTest.qWait(100)
    window.grab().save(str(folder/"editor.png"))
    assert not errors, errors
    report["passed"] = True
except Exception as exc:
    report.update(passed=False, error=str(exc), errors=errors)
finally:
    window.stop_preview(wait=True)
    window.close()
    app.processEvents()
    (folder/"report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)
sys.exit(0 if report.get("passed") else 1)
