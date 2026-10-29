import bisect
import math
from collections import OrderedDict

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QStyledItemDelegate, QWidget

from .domain import timecode
from .preview_cache import WAVE_SECONDS, WAVE_STEP, thumbnail_step

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
QScrollBar:horizontal { background: #131c29; height: 12px; border: 0; }
QScrollBar::handle:horizontal { background: #476387; border-radius: 5px; min-width: 35px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
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
    boundaries = Signal(float, float)
    viewChanged = Signal()
    scrubStarted = Signal()
    scrubbed = Signal(float)
    scrubFinished = Signal(float)

    def __init__(self, overview=False):
        super().__init__()
        self.overview = overview
        self.duration, self.fps, self.position = 1.0, 25.0, 0.0
        self.start, self.end, self.window, self.view_start = 0.0, 1.0, 120.0, 0.0
        self.clips, self.selection_spans, self.coverage = [], [], []
        self.viewport = (0.0, 1.0)
        self.drag = None
        self.loading = False
        self._frames = OrderedDict()
        self._waves = OrderedDict()
        self._coarse_waves = {}
        self._background = None
        self._background_key = None
        self._revision = 0
        self.setMinimumHeight(46 if overview else 150)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setToolTip("Потяните ленту — прокрутка под курсором. Потяните белый курсор или линейку — перемотка. "
                        "Синие ручки — обрезка. Колесо — масштаб под мышью.")

    def reset(self, duration, fps=25):
        self.duration, self.fps = max(0.001, duration), fps
        self.loading = False
        self.position, self.view_start = 0.0, 0.0
        self.window = min(120, self.duration)
        self.start, self.end = 0, min(30, self.duration)
        self.selection_spans = []
        self._frames.clear()
        self._waves.clear()
        self._coarse_waves.clear()
        self.invalidate()

    def invalidate(self):
        self._revision += 1
        self.update()

    def limits(self):
        if self.overview:
            return 0.0, self.duration
        width = min(self.window, self.duration)
        left = max(0.0, min(self.duration-width, self.view_start))
        return left, left+width

    def x(self, stamp):
        a, b = self.limits()
        return 12+(stamp-a)/max(0.001, b-a)*max(1, self.width()-24)

    def seconds(self, x):
        a, b = self.limits()
        return max(0.0, min(self.duration, a+(x-12)/max(1, self.width()-24)*(b-a)))

    def pan_to(self, start):
        previous = self.limits()[0]
        self.view_start = max(0.0, min(max(0, self.duration-self.window), start))
        if abs(previous-self.view_start) > 0.00001:
            self.viewChanged.emit()
            self.update()

    def ensure_visible(self, position, center=False):
        if self.overview or self.drag:
            return
        a, b = self.limits()
        if center or position < a or position > b:
            self.pan_to(position-self.window/2)

    def zoom_by(self, factor, anchor_x=None):
        if self.overview:
            return
        anchor_x = self.width()/2 if anchor_x is None else anchor_x
        anchor = self.seconds(anchor_x)
        ratio = max(0, min(1, (anchor_x-12)/max(1, self.width()-24)))
        self.window = min(self.duration, max(min(0.5, self.duration), self.window*factor))
        self.view_start = max(0, min(self.duration-self.window, anchor-ratio*self.window))
        self.viewChanged.emit()
        self.update()

    def add_frame(self, stamp, step, pixmap):
        if pixmap.isNull():
            return
        self._frames[stamp] = (step, pixmap)
        self._frames.move_to_end(stamp)
        while len(self._frames) > 256:
            self._frames.popitem(last=False)
        self.invalidate()

    def add_wave(self, chunk, peaks):
        levels = [(WAVE_STEP, peaks)]
        for size in (4, 16, 64, 256, 600):
            levels.append((WAVE_STEP*size, [max(peaks[i:i+size]) for i in range(0, len(peaks), size)]))
        self._waves[chunk] = levels
        self._waves.move_to_end(chunk)
        self._coarse_waves[chunk] = levels[3:]
        while len(self._waves) > 128:
            self._waves.popitem(last=False)
        self.invalidate()

    def _draw_base(self, p):
        a, b = self.limits()
        p.fillRect(self.rect(), QColor("#121925"))
        p.setPen(QColor("#71839d"))
        p.setFont(QFont("Segoe UI", 8))
        tick = thumbnail_step(b-a, max(1, self.width()*0.75))
        first = math.ceil(a/tick)
        for n in range(first, math.floor(b/tick)+1):
            stamp = n*tick
            x = self.x(stamp)
            p.setPen(QColor("#263548"))
            p.drawLine(int(x), 24, int(x), self.height())
            p.setPen(QColor("#93a3bd"))
            text = timecode(stamp)[:-1] if b-a < 12 else timecode(stamp)[:-4]
            label_x = max(2, min(self.width()-89, x-40))
            p.drawText(QRectF(label_x, 1, 88, 19), Qt.AlignmentFlag.AlignCenter, text)
        if not self.overview:
            step = thumbnail_step(b-a, self.width())
            frame_stamps = sorted(self._frames)
            for n in range(math.floor(a/step), math.ceil(b/step)):
                stamp = n*step
                left, right = self.x(stamp), self.x(stamp+step)
                cell = QRectF(left+1, 38, max(1, right-left-2), 58)
                p.fillRect(cell, QColor("#263347"))
                candidates = []
                i = bisect.bisect_left(frame_stamps, stamp)
                for j in (i-1, i):
                    if 0 <= j < len(frame_stamps):
                        candidates.append(frame_stamps[j])
                nearest = min(candidates, key=lambda v: abs(v-stamp)) if candidates else None
                if nearest is not None and abs(nearest-stamp) <= step*2:
                    _, image = self._frames[nearest]
                    p.save()
                    p.setClipRect(cell)
                    if abs(nearest-stamp) > step*0.15:
                        p.setOpacity(0.45)
                    scaled = image.scaled(round(cell.width()), 58, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                          Qt.TransformationMode.SmoothTransformation)
                    p.drawPixmap(int(cell.center().x()-scaled.width()/2), 38, scaled)
                    p.restore()
                else:
                    p.setPen(QColor("#34465f"))
                    for hatch in range(int(left)-60, int(right)+60, 16):
                        p.save()
                        p.setClipRect(cell)
                        p.drawLine(hatch, 96, hatch+58, 38)
                        p.restore()
                p.fillRect(QRectF(left+2, 78, max(1, right-left-4), 17), QColor(8, 13, 22, 175))
                p.setPen(QColor("#b3c2d8"))
                p.drawText(QRectF(left+5, 78, max(1, right-left-8), 16), Qt.AlignmentFlag.AlignLeft,
                           "≈"+timecode(stamp)[:-4])
            p.fillRect(QRectF(12, 105, self.width()-24, 36), QColor("#182536"))
            p.setPen(QColor("#263e55"))
            p.drawLine(12, 123, self.width()-12, 123)
            columns = {}
            pixel_seconds = (b-a)/max(1, self.width()-24)
            for chunk in set(self._coarse_waves) | set(self._waves):
                begin = chunk*WAVE_SECONDS
                if begin > b or begin+WAVE_SECONDS < a:
                    continue
                levels = self._waves.get(chunk, self._coarse_waves[chunk])
                step, data = levels[0]
                for candidate_step, candidate in levels:
                    if candidate_step <= pixel_seconds:
                        step, data = candidate_step, candidate
                for i, peak in enumerate(data):
                    stamp = begin+(i+0.5)*step
                    if a <= stamp <= b:
                        x = int(self.x(stamp))
                        columns[x] = max(columns.get(x, 0), peak)
            p.setPen(QPen(QColor("#6ab5cc"), 1))
            for x, peak in columns.items():
                amplitude = max(1, min(16, peak*32))
                p.drawLine(x, int(123-amplitude), x, int(123+amplitude))
        for clip in self.clips:
            for span in clip.spans:
                left, right = max(12, self.x(span.start)), min(self.width()-12, self.x(span.end))
                if right > left:
                    p.fillRect(QRectF(left, 27, max(3, right-left), 4), QColor("#607ca8"))
        for span in self.coverage:
            x = self.x(span.start)
            if 12 <= x <= self.width()-12:
                p.fillRect(QRectF(x, 33, 2, 3), QColor("#5bbaad"))

    def paintEvent(self, event):
        ratio = self.devicePixelRatioF()
        key = (self.size().width(), self.size().height(), self.limits(), self._revision, ratio)
        if key != self._background_key:
            self._background = QPixmap(round(self.width()*ratio), round(self.height()*ratio))
            self._background.setDevicePixelRatio(ratio)
            base = QPainter(self._background)
            self._draw_base(base)
            base.end()
            self._background_key = key
        p = QPainter(self)
        p.drawPixmap(0, 0, self._background)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        selections = [(self.start, self.end)] if len(self.selection_spans) <= 1 else [
            (self.start if i == 0 else s.start, self.end if i == len(self.selection_spans)-1 else s.end)
            for i, s in enumerate(self.selection_spans)]
        for start, end in selections:
            left, right = max(12, self.x(start)), min(self.width()-12, self.x(end))
            if right > left:
                p.fillRect(QRectF(left, 37 if not self.overview else 25, right-left, self.height()-38),
                           QColor(65, 127, 240, 42))
        if self.overview:
            left, right = self.x(self.viewport[0]), self.x(self.viewport[1])
            p.setBrush(QColor(78, 135, 220, 30))
            p.setPen(QPen(QColor("#648fcf"), 1))
            p.drawRoundedRect(QRectF(left, 23, max(4, right-left), 20), 3, 3)
        else:
            for x in (self.x(self.start), self.x(self.end)):
                if 8 <= x <= self.width()-8:
                    p.fillRect(QRectF(x-4, 37, 8, self.height()-41), QColor("#568eff"))
                    p.setPen(QPen(QColor("#dceaff"), 1))
                    p.drawLine(int(x), 65, int(x), 80)
            if self.loading:
                p.fillRect(QRectF(self.width()-177, 2, 169, 20), QColor("#223249"))
                p.setPen(QColor("#b1c8ed"))
                p.setFont(QFont("Segoe UI", 8))
                p.drawText(QRectF(self.width()-173, 2, 160, 20), Qt.AlignmentFlag.AlignCenter, "Подгрузка таймлайна…")
        x = self.x(self.position)
        if 8 <= x <= self.width()-8:
            p.setPen(QPen(QColor("#f6f8ff"), 2))
            p.drawLine(int(x), 19, int(x), self.height())
            p.setBrush(QColor("#f6f8ff"))
            p.drawRoundedRect(QRectF(x-5, 18, 10, 10), 2, 2)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.setFocus()
        x, y = event.position().x(), event.position().y()
        self._press_x, self._origin_view, self._origin_position = x, self.limits()[0], self.position
        if not self.limits()[0] <= self.position <= self.limits()[1]:
            self._origin_position = sum(self.limits())/2
        self._moved = False
        if not self.overview and y > 28 and abs(x-self.x(self.start)) < 10:
            self.drag = "start"
        elif not self.overview and y > 28 and abs(x-self.x(self.end)) < 10:
            self.drag = "end"
        else:
            self.drag = "seek" if self.overview or y < 30 or abs(x-self.x(self.position)) < 9 else "pan"
            self.scrubStarted.emit()
        self.setCursor(Qt.CursorShape.ClosedHandCursor if self.drag == "pan" else Qt.CursorShape.SizeHorCursor)
        if self.drag == "seek":
            self._move(x)

    def mouseMoveEvent(self, event):
        if self.drag:
            self._moved |= abs(event.position().x()-self._press_x) > 3
            self._move(event.position().x())
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor if event.position().y() > 30 and not self.overview
                           else Qt.CursorShape.SizeHorCursor)

    def _move(self, x):
        if self.drag == "pan":
            if not self._moved:
                return
            delta = (self._press_x-x)*min(self.window, self.duration)/max(1, self.width()-24)
            self.pan_to(self._origin_view+delta)
            stamp = max(0, min(self.duration, self._origin_position+delta))
            self.position = stamp
            self.scrubbed.emit(stamp)
        elif self.drag == "seek":
            self.position = self.seconds(x)
            self.scrubbed.emit(self.position)
        elif self.drag == "start":
            last = self.selection_spans[0].end if len(self.selection_spans) > 1 else self.end
            self.start = max(0, min(self.seconds(x), last-1/self.fps))
        elif self.drag == "end":
            first = self.selection_spans[-1].start if len(self.selection_spans) > 1 else self.start
            self.end = min(self.duration, max(self.seconds(x), first+1/self.fps))
        self.update()

    def mouseReleaseEvent(self, event):
        if not self.drag:
            return
        mode = self.drag
        self.drag = None
        if mode in ("start", "end"):
            self.boundaries.emit(self.start, self.end)
        else:
            self.scrubFinished.emit(self.position)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def wheelEvent(self, event):
        if not self.overview:
            self.zoom_by(0.8 if event.angleDelta().y() > 0 else 1.25, event.position().x())
        event.accept()
