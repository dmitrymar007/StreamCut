from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPolygonF

root = Path(__file__).resolve().parents[1]
(root/"assets").mkdir(exist_ok=True)
canvas = QImage(128, 128, QImage.Format.Format_ARGB32)
canvas.fill(Qt.GlobalColor.transparent)
painter = QPainter(canvas)
painter.setRenderHint(QPainter.RenderHint.Antialiasing)
painter.setPen(Qt.PenStyle.NoPen)
painter.setBrush(QColor("#3678ee"))
painter.drawRoundedRect(4, 4, 120, 120, 27, 27)
painter.setBrush(QColor("#ffffff"))
painter.drawPolygon(QPolygonF([QPointF(43, 29), QPointF(100, 64), QPointF(43, 99)]))
painter.setPen(QColor("#111b31"))
painter.drawLine(21, 101, 107, 15)
painter.drawLine(22, 102, 108, 16)
painter.end()
assert canvas.save(str(root/"assets"/"streamcut.ico"))
assert canvas.save(str(root/"assets"/"streamcut.png"))
