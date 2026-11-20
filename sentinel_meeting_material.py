"""Shared white glass paint for the overlay surfaces and graph cards.

The desktop compositor provides real blur. The window additionally uses the
approved reference crops as a static skin; graph cards keep vector glass paint.
"""
from functools import lru_cache
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from sentinel_paths import ASSETS_DIR


SHADOW = 6


@lru_cache(maxsize=2)
def reference_skin(name):
    """Load once; missing bundle assets retain the vector fallback."""
    if name not in ('toolbar', 'panel'):
        raise ValueError('Unknown meeting glass skin')
    return QImage(str(ASSETS_DIR / 'meeting-glass' / (name+'.png')))


def paint_reference_skin(painter, rect, name, opacity):
    image = reference_skin(name)
    if image.isNull():
        return
    painter.save()
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    painter.setOpacity(min(1.0, opacity/98))
    # Nine slices preserve the photographed rim when the window is resized.
    border = 20
    source_border = 48
    sx = (0, source_border, image.width()-source_border, image.width())
    sy = (0, source_border, image.height()-source_border, image.height())
    dx = (rect.left(), rect.left()+border, rect.right()-border, rect.right())
    dy = (rect.top(), rect.top()+border, rect.bottom()-border, rect.bottom())
    for row in range(3):
        for col in range(3):
            painter.drawImage(QRectF(dx[col], dy[row], dx[col+1]-dx[col], dy[row+1]-dy[row]),
                              image, QRectF(sx[col], sy[row], sx[col+1]-sx[col], sy[row+1]-sy[row]))
    painter.restore()


def rounded_path(rect, radius):
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def paint_glass(painter, rect, radius, *, opacity=74, accent=None, dashed=False):
    """Draw glass in local coordinates; callers leave room for its shadow."""
    painter.save()
    painter.setBrush(Qt.NoBrush)
    # Broad, low-opacity rings soften outwards. There is no dark inner bevel.
    for spread in range(SHADOW, 0, -1):
        painter.setPen(QPen(QColor(60, 65, 72, 2 + (SHADOW-spread)), 1))
        shadow = rect.adjusted(-spread, -spread+2, spread, spread+2)
        painter.drawRoundedRect(shadow, radius+spread, radius+spread)
    fill = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    tint = QColor(accent) if accent else QColor(248, 249, 250)
    fill.setColorAt(0, QColor(255, 255, 255, min(255, opacity+34)))
    fill.setColorAt(1, QColor(tint.red(), tint.green(), tint.blue(), opacity))
    painter.setBrush(fill)
    edge = QColor(accent) if accent else QColor(255, 255, 255, 220)
    if accent:
        edge.setAlpha(155)
    pen = QPen(edge, 1)
    if dashed:
        pen.setStyle(Qt.DashLine)
    painter.setPen(pen)
    painter.drawRoundedRect(rect, radius, radius)
    painter.restore()
