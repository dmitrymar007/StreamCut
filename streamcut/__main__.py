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
    app.setStyle("Fusion")
    app.setStyleSheet(THEME)
    window = MainWindow()
    window.show()
    if len(sys.argv) > 1:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(100, lambda: window.open_source(sys.argv[1]))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
