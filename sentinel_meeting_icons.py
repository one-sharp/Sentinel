"""Small vector icons rendered at the current Qt device pixel ratio."""
from PySide6.QtCore import Qt, QSize, QPointF
from PySide6.QtGui import QPixmap, QPainter, QPen, QColor, QIcon, QPolygonF
from PySide6.QtWidgets import QToolButton


def icon(name, ratio=1):
    pix = QPixmap(QSize(30, 30) * ratio)
    pix.setDevicePixelRatio(ratio)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    color = QColor('#A64B27' if name == 'play' else '#34404D')
    p.setPen(QPen(color, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

    def line(*points):
        p.drawPolyline(QPolygonF([QPointF(*point) for point in points]))

    if name == 'transcript':
        for x, y in ((7, 12), (11, 8), (15, 5), (19, 10), (23, 13)):
            line((x, y), (x, 30-y))
    elif name == 'graph':
        line((15, 11), (15, 16), (8, 21)); line((15, 16), (22, 21))
        for x, y in ((15, 8), (7, 23), (23, 23)):
            p.drawEllipse(QPointF(x, y), 3, 3)
    elif name == 'background':
        line((5, 11), (15, 6), (25, 11), (15, 16), (5, 11))
        line((5, 16), (15, 21), (25, 16)); line((5, 21), (15, 26), (25, 21))
    elif name == 'expand':
        line((17, 6), (24, 6), (24, 13)); line((24, 6), (14, 16))
        line((13, 8), (7, 8), (7, 24), (23, 24), (23, 18))
    elif name == 'hide':
        line((9, 9), (21, 21)); line((21, 9), (9, 21))
    elif name == 'play':
        p.setBrush(color)
        p.drawPolygon(QPolygonF([QPointF(11, 7), QPointF(23, 15), QPointF(11, 23)]))
    elif name == 'stop':
        p.fillRect(9, 9, 12, 12, QColor('#B44739'))
    elif name == 'more':
        p.setBrush(color)
        for x in (8, 15, 22):
            p.drawEllipse(QPointF(x, 15), 1, 1)
    p.end()
    return QIcon(pix)


class IconButton(QToolButton):
    def __init__(self, name, hint, command, parent=None):
        super().__init__(parent)
        self.setFixedSize(26, 28)
        self.setIconSize(QSize(24, 24))
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.set_icon(name, hint)
        self.clicked.connect(command)

    def set_icon(self, name, hint):
        self.icon_name = name
        self.setIcon(icon(name, self.devicePixelRatioF()))
        self.setToolTip(hint)
        self.setAccessibleName(hint)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.click()
        else:
            super().keyPressEvent(event)
