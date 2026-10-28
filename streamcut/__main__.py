import locale
import multiprocessing
import sys


def main():
    multiprocessing.freeze_support()
    if "--worker" in sys.argv:
        sys.argv.remove("--worker")
        from .worker import main as worker_main
        return worker_main()
    locale.setlocale(locale.LC_NUMERIC, "C")
    from PySide6.QtWidgets import QApplication

    from .ui import MainWindow
    from .widgets import THEME

    app = QApplication(sys.argv)
    app.setOrganizationName("StreamCut")
    app.setApplicationName("StreamCut")
    from PySide6.QtCore import QLibraryInfo, QTranslator
    app.translator = QTranslator(app)
    if app.translator.load("qtbase_ru", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(app.translator)
    app.setStyle("Fusion")
    app.setStyleSheet(THEME)
    window = MainWindow()
    window.show()
    if len(sys.argv) > 1:
        from PySide6.QtCore import QTimer
        if sys.argv[1] == "--project" and len(sys.argv) > 2:
            QTimer.singleShot(100, lambda: window.open_project(sys.argv[2]))
        else:
            QTimer.singleShot(100, lambda: window.open_source(sys.argv[1]))
    if "--smoke-output" in sys.argv:
        import json
        from pathlib import Path

        from PySide6.QtCore import QTimer
        output = Path(sys.argv[sys.argv.index("--smoke-output")+1])
        output.mkdir(parents=True, exist_ok=True)
        errors = []
        window.error = lambda error: errors.append(str(error))
        QTimer.singleShot(250, window.preview_timer.stop)
        def report():
            result = {"window_visible": window.isVisible(), "player_loaded": bool(window.player),
                      "source_valid": window.source_valid, "position": window.position,
                      "duration": window.project.duration, "errors": errors}
            try:
                assert window.isVisible() and window.player and window.source_valid and not errors
                assert window.player.time_pos is not None
                if window.current():
                    assert abs(window.player.time_pos-window.current().start) < 0.2
                window.player.command("screenshot-to-file", str((output/"frame.jpg").resolve()), "video")
                result["passed"] = True
            except Exception as exc:
                result["passed"] = False
                result["error"] = str(exc)
            (output/"gui.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            window.close()
            app.exit(0 if result["passed"] else 1)
        QTimer.singleShot(4000, report)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
