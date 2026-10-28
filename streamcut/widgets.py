from PySide6.QtCore import QAbstractListModel, QModelIndex, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QStyledItemDelegate, QWidget

from .domain import timecode

THEME = """
* { font-family: 'Segoe UI'; font-size: 12px; color: #d9dfeb; }
QMainWindow, QDialog { background: #11141b; }
QWidget#panel { background: #191e28; border: 1px solid #292f3d; border-radius: 10px; }
QLabel#muted { color: #8793a9; }
QLabel#heading { font-size: 16px; font-weight: 600; color: #f0f3fa; }
QLabel#brand { font-size: 21px; font-weight: 700; color: #f5f7ff; }
QPushButton { background: #262e3d; border: 1px solid #354157; border-radius: 6px; padding: 7px 11px; }
QPushButton:hover { background: #34435c; border-color: #5d91ef; }
QPushButton:disabled { color: #657086; background: #1d2330; }
QPushButton#primary { background: #3678ee; color: white; border-color: #4d8bff; font-weight: 600; }
QPushButton#primary:hover { background: #4a8aff; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
 background: #111722; border: 1px solid #303c50; border-radius: 5px; padding: 5px; selection-background-color: #326bd1; }
QLineEdit:focus, QTextEdit:focus { border-color: #568ef1; }
QListView, QListWidget { background: #191e28; border: 0; outline: 0; }
QListView::item { padding: 7px; border-radius: 5px; }
QListView::item:selected { background: #263c61; }
QSplitter::handle { background: #11141b; width: 7px; height: 7px; }
QProgressBar { background: #202839; border: 0; border-radius: 3px; height: 6px; }
QProgressBar::chunk { background: #528cff; border-radius: 3px; }
QTabWidget::pane { border: 1px solid #303a4b; }
QTabBar::tab { background: #222b3c; padding: 8px 12px; }
QTabBar::tab:selected { background: #34568c; }
QScrollBar:vertical { background: #191e28; width: 9px; }
QScrollBar::handle:vertical { background: #3e4c64; border-radius: 4px; min-height: 25px; }
QMenuBar, QMenu { background: #1c2330; }
QStatusBar { background: #11141b; color: #8f9db5; }
QToolTip { background: #263752; border: 1px solid #4f76ad; padding: 6px; }
"""


class ClipListModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.items = []

    def rowCount(self, parent=QModelIndex()):
        return len(self.items)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.items):
            return None
        clip = self.items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return clip.title
        if role == Qt.ItemDataRole.UserRole:
            return clip
        if role == Qt.ItemDataRole.ToolTipRole:
            return clip.reason + "\n" + clip.note

    def replace(self, items):
        self.beginResetModel()
        self.items = items
        self.endResetModel()


class ClipDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(220, 108)

    def paint(self, painter, option, index):
        clip = index.data(Qt.ItemDataRole.UserRole)
        if clip is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect.adjusted(3, 3, -3, -3)
        from PySide6.QtWidgets import QStyle
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.setPen(QPen(QColor("#4a80cf" if selected else "#2a3445")))
        painter.setBrush(QColor("#243a5d" if selected else "#1d2532"))
        painter.drawRoundedRect(r, 7, 7)
        text = r.adjusted(11, 8, -10, -5)
        painter.setPen(QColor("#80acff"))
        painter.setFont(QFont("Segoe UI", 9))
        painter.drawText(text, Qt.AlignmentFlag.AlignTop,
                         f"{timecode(clip.start)[:-4]}  ·  {clip.duration:.0f} с")
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        painter.setPen(QColor("#eef3ff"))
        text.moveTop(text.top()+22)
        painter.drawText(text, Qt.AlignmentFlag.AlignTop,
                         painter.fontMetrics().elidedText(clip.title, Qt.TextElideMode.ElideRight, text.width()))
        painter.setFont(QFont("Segoe UI", 9))
        painter.setPen(QColor("#9caac1"))
        text.moveTop(text.top()+23)
        painter.drawText(text, Qt.AlignmentFlag.AlignTop,
                         painter.fontMetrics().elidedText(clip.reason, Qt.TextElideMode.ElideRight, text.width()))
        text.moveTop(text.top()+22)
        painter.setPen(QColor("#97b7da"))
        score = f" · оценка {clip.score:.0f}" if clip.score is not None else ""
        painter.drawText(text, Qt.AlignmentFlag.AlignTop, f"{clip.status} · {', '.join(clip.sources)}{score}")
        painter.restore()


class Timeline(QWidget):
    seek = Signal(float)
    boundaries = Signal(float, float)
    viewChanged = Signal()

    def __init__(self, overview=False):
        super().__init__()
        self.overview = overview
        self.duration = 1.0
        self.position = 0.0
        self.start = 0.0
        self.end = 1.0
        self.window = 120.0
        self.clips = []
        self.coverage = []
        self.peaks = []
        self.thumbnails = []
        self.drag = None
        self.setMinimumHeight(48 if overview else 110)
        self.setMouseTracking(True)
        self.setToolTip("Нажмите для перемотки. Потяните синие границы. Колесо — масштаб таймлайна.")

    def limits(self):
        if self.overview or self.duration <= self.window:
            return 0.0, self.duration
        left = max(0, min(self.duration-self.window, self.position-self.window/2))
        return left, left+self.window

    def x(self, seconds):
        a, b = self.limits()
        return 10+(seconds-a)/max(0.001, b-a)*(self.width()-20)

    def seconds(self, x):
        a, b = self.limits()
        return max(0, min(self.duration, a+(x-10)/max(1, self.width()-20)*(b-a)))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#151c28"))
        a, b = self.limits()
        for clip in self.clips:
            x1, x2 = max(10, self.x(clip.start)), min(self.width()-10, self.x(clip.end))
            if x2 >= x1:
                p.fillRect(QRectF(x1, 22, max(3, x2-x1), 5), QColor("#809dce"))
        for span in self.coverage:
            # Each mark is a sampled frame, never a claim of continuous coverage.
            x = self.x(span.start)
            if 10 <= x <= self.width()-10:
                p.fillRect(QRectF(x, 29, 2, 4), QColor("#5bbaad"))
        if not self.overview:
            for stamp, pixmap in self.thumbnails:
                x = self.x(stamp)
                if -100 < x < self.width():
                    p.drawPixmap(int(x), 34, 86, 46, pixmap)
            p.setPen(QPen(QColor("#5f8fb8"), 1))
            for stamp, peak in self.peaks:
                x = self.x(stamp)
                p.drawLine(int(x), 96-int(peak*11), int(x), 96+int(peak*11))
        x1, x2 = self.x(self.start), self.x(self.end)
        p.fillRect(QRectF(max(10, x1), 20, max(0, min(self.width()-10, x2)-max(10, x1)),
                         self.height()-22), QColor(64, 128, 255, 35))
        for x in (x1, x2):
            if 0 <= x <= self.width():
                p.fillRect(QRectF(x-3, 19, 6, self.height()-21), QColor("#568eff"))
        p.setFont(QFont("Segoe UI", 8))
        for i in range(7):
            stamp = a+(b-a)*i/6
            x = self.x(stamp)
            p.setPen(QColor("#7d8da6"))
            p.drawText(QRectF(x-38, 1, 76, 18), Qt.AlignmentFlag.AlignCenter, timecode(stamp)[:-4])
        p.setPen(QPen(QColor("#f4f7ff"), 2))
        p.drawLine(int(self.x(self.position)), 16, int(self.x(self.position)), self.height())

    def mousePressEvent(self, event):
        x = event.position().x()
        self.drag = "start" if abs(x-self.x(self.start)) < 9 else "end" if abs(x-self.x(self.end)) < 9 else "seek"
        self._move(x)

    def mouseMoveEvent(self, event):
        if self.drag:
            self._move(event.position().x())

    def mouseReleaseEvent(self, event):
        if self.drag in ("start", "end"):
            self.boundaries.emit(self.start, self.end)
        self.drag = None

    def _move(self, x):
        stamp = self.seconds(x)
        if self.drag == "seek":
            self.seek.emit(stamp)
        elif self.drag == "start":
            self.start = min(stamp, self.end-0.04)
        elif self.drag == "end":
            self.end = max(stamp, self.start+0.04)
        self.update()

    def wheelEvent(self, event):
        if not self.overview:
            self.window = min(self.duration, max(5, self.window*(0.8 if event.angleDelta().y() > 0 else 1.25)))
            self.update()
            self.viewChanged.emit()
