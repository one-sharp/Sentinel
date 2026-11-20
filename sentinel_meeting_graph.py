"""Qt graph presentation of the shared grounded document; no analysis worker."""
from PySide6.QtCore import Qt, QRectF, QPointF, QTimer, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPainterPathStroker, QPen, QColor, QBrush, QFont, QFontMetrics, QPolygonF
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QGraphicsView, QGraphicsScene, QGraphicsPathItem, QGraphicsTextItem,
    QPlainTextEdit, QToolButton)
from sentinel_graph_layout import (layout_graph, display_nodes, NODE_HEIGHT,
    ROW_STEP as LAYOUT_ROW_STEP, COLUMN_STEP as LAYOUT_COLUMN_STEP, TOP as LAYOUT_TOP)
from sentinel_logic import KINDS
from sentinel_meeting_material import paint_glass, SHADOW

# Compact presentation coordinates; shared layout keeps its stable logical
# grid for both renderers. Font sizes and card height remain readable at 100%.
NODE_WIDTH = 208
COLUMN_STEP = 232
ROW_STEP = 96

class GraphView(QGraphicsView):
    viewportResized = Signal()
    cameraChanged = Signal()
    focusRequested = Signal(str)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.viewportResized.emit()

    def zoom_by(self, factor):
        current = self.transform().m11()
        target = max(0.05, min(2.0, current*factor))
        self.scale(target/current, target/current)
        self.cameraChanged.emit()

    def mousePressEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        while item is not None and not isinstance(item, Node):
            item = item.parentItem()
        # The first click opens evidence and changes viewport geometry. Keep
        # the pressed identity so a double click still focuses that same card.
        self.pressed_node = item.node['id'] if item is not None else None
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        ident = getattr(self, 'pressed_node', None)
        if ident is not None and event.button() == Qt.LeftButton:
            self.focusRequested.emit(ident)
            self.pressed_node = None
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.zoom_by(factor)
            event.accept()
        elif event.modifiers() & Qt.ShiftModifier:
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - event.angleDelta().y())
            event.accept()
        else:
            super().wheelEvent(event)

class Node(QGraphicsPathItem):

    def boundingRect(self):
        return super().boundingRect().adjusted(-SHADOW, -SHADOW, SHADOW, SHADOW+2)

    def paint(self, painter, option, widget=None):
        kind = self.node['kind']
        accent = '#ED8F83' if kind == 'blocker' else '#AA87DF' if kind == 'action' else None
        painter.setRenderHint(QPainter.Antialiasing)
        paint_glass(painter, self.path().boundingRect(), 13, opacity=98,
                    accent=accent, dashed=self.node['basis'] != 'stated')

    def __init__(self, node, x, y, inspect):
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, y, NODE_WIDTH, NODE_HEIGHT), 13, 13)
        super().__init__(path)
        self.node, self.inspect = (node, inspect)
        color = '#A64B27' if node['kind'] == 'blocker' else '#72509D' if node['kind'] == 'action' else '#647180'
        pen = QPen(QColor(color), 1.2)
        if node['basis'] != 'stated':
            pen.setStyle(Qt.DashLine)
        self.setPen(pen)
        self.setBrush(QColor(255, 255, 255, 45))
        self.setCursor(Qt.PointingHandCursor)
        self.setFlag(QGraphicsPathItem.ItemClipsChildrenToShape)
        self.setToolTip(node['text'])
        role = {'我方': '我', '对方': '对方', '未绑定': '身份未确认'}.get(node['role'], node['role'])
        badge = '下一步建议 · 尚未推进' if node['kind'] == 'action' else KINDS.get(node['kind'], '原话') + ' · ' + role
        if node['basis'] == 'uncertain':
            badge += ' · 待核实'
        label = QGraphicsTextItem(badge, self)
        label.setFont(QFont('Microsoft YaHei UI', 9))
        label.setDefaultTextColor(QColor(color))
        label.setPos(x + 9, y + 4)
        self.label = label
        font = QFont('Microsoft YaHei UI', 11)
        font.setBold(True)
        metrics = QFontMetrics(font)
        remaining = ' '.join(node['title'].split())
        lines = []
        for index in range(2):
            if metrics.horizontalAdvance(remaining) <= NODE_WIDTH - 28:
                lines.append(remaining)
                break
            if index == 1:
                lines.append(metrics.elidedText(remaining, Qt.ElideRight, NODE_WIDTH - 28))
                break
            length = 1
            while length < len(remaining) and metrics.horizontalAdvance(remaining[:length + 1]) <= NODE_WIDTH - 28:
                length += 1
            lines.append(remaining[:length])
            remaining = remaining[length:]
        title = QGraphicsTextItem('\n'.join(lines), self)
        title.setFont(font)
        self.title = title
        title.setDefaultTextColor(QColor('#29333F'))
        title.setTextWidth(NODE_WIDTH - 20)
        title.setPos(x + 9, y + 24)
        for child in (label, title):
            child.setAcceptedMouseButtons(Qt.NoButton)

    def mousePressEvent(self, event):
        self.inspect(self.node)
        event.accept()

class Edge(QGraphicsPathItem):
    """Wide invisible hit region keeps fine relationship lines easy to inspect."""

    def __init__(self, path, edge, inspect):
        super().__init__(path)
        self.edge, self.inspect = (edge, inspect)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(edge['relation'] + ' · 点击查看原话依据')

    def shape(self):
        stroker = QPainterPathStroker()
        stroker.setWidth(12)
        return stroker.createStroke(self.path())

    def boundingRect(self):
        return self.shape().controlPointRect()

    def mousePressEvent(self, event):
        self.inspect(dict(text='关系：' + self.edge['relation'], evidence=self.edge['evidence']))
        event.accept()

class EdgeLabel(QGraphicsTextItem):

    def __init__(self, edge, parent):
        super().__init__(edge['relation'], parent)
        self.document().setDocumentMargin(1)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        self.parentItem().mousePressEvent(event)

    def paint(self, painter, option, widget=None):
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 190))
        painter.drawRoundedRect(self.boundingRect(), 3, 3)
        super().paint(painter, option, widget)

class MeetingGraph(QWidget):

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self.panel = panel
        self.signature = None
        self.doc = None
        self.positions = {}
        self.layout_columns = None
        self.camera_mode = 'read'
        self.reset_read_position = True
        self.camera_timer = QTimer(self)
        self.camera_timer.setSingleShot(True)
        self.camera_timer.timeout.connect(self.update_camera)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 8)
        toolbar = QHBoxLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet('color:#536273;font-size:8pt')
        toolbar.addWidget(self.status, 1)
        self.retry = QToolButton()
        self.retry.setText('↻')
        self.retry.setToolTip('重试逻辑图分析')
        self.retry.setAccessibleName('重试逻辑图分析')
        self.retry.clicked.connect(lambda: panel.enable_logic_analysis())
        toolbar.addWidget(self.retry)
        self.overview = QToolButton()
        self.overview.setText('⊞')
        self.overview.setToolTip('全图：显示所有分支；双击节点放大阅读')
        self.overview.setAccessibleName('显示完整逻辑图')
        self.overview.clicked.connect(self.fit)
        toolbar.addWidget(self.overview)
        self.read_size = QToolButton()
        self.read_size.setText('1:1')
        self.read_size.setToolTip('按原字号从顶部阅读')
        self.read_size.setAccessibleName('原字号阅读逻辑图')
        self.read_size.clicked.connect(self.read_from_top)
        toolbar.addWidget(self.read_size)
        for attr, text, hint, factor in (
                ('zoom_out', '−', '缩小逻辑图', 1/1.2),
                ('zoom_in', '+', '放大逻辑图', 1.2)):
            button = QToolButton()
            button.setText(text)
            button.setToolTip(hint)
            button.setAccessibleName(hint)
            button.clicked.connect(lambda checked=False, f=factor: self.view.zoom_by(f))
            setattr(self, attr, button)
            toolbar.addWidget(button)
        self.zoom_level = QLabel('100%')
        self.zoom_level.setFixedWidth(42)
        self.zoom_level.setAlignment(Qt.AlignCenter)
        self.zoom_level.setStyleSheet('color:#647180;font-size:8pt')
        toolbar.addWidget(self.zoom_level)
        column.addLayout(toolbar)
        row = QHBoxLayout()
        column.addLayout(row, 1)
        self.view = GraphView()
        self.view.setFrameShape(QGraphicsView.NoFrame)
        self.view.setStyleSheet('background:transparent;border:0')
        self.view.viewport().setAutoFillBackground(False)
        self.view.setRenderHint(QPainter.Antialiasing)
        self.view.setDragMode(QGraphicsView.ScrollHandDrag)
        self.view.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.view.setResizeAnchor(QGraphicsView.NoAnchor)
        self.view.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.view.viewportResized.connect(self.schedule_camera)
        self.view.cameraChanged.connect(self.manual_camera)
        self.view.focusRequested.connect(self.focus_node)
        self.scene = QGraphicsScene(self)
        self.view.setScene(self.scene)
        row.addWidget(self.view, 1)
        self.detail = QWidget()
        self.detail.setFixedWidth(270)
        col = QVBoxLayout(self.detail)
        col.setContentsMargins(8, 0, 0, 0)
        close = QToolButton()
        close.setText('×')
        close.setToolTip('收起依据')
        close.clicked.connect(self.detail.hide)
        col.addWidget(close, 0, Qt.AlignRight)
        self.evidence = QPlainTextEdit()
        self.evidence.setReadOnly(True)
        col.addWidget(self.evidence, 1)
        row.addWidget(self.detail)
        self.detail.hide()

    def columns_for_view(self):
        if self.detail.isVisible() and self.layout_columns is not None:
            return self.layout_columns
        return max(1, min(4, (self.view.width()-32+24)//COLUMN_STEP))

    def showEvent(self, event):
        super().showEvent(event)
        self.schedule_camera()

    def refresh(self, force=False):
        doc = self.panel.logic_document
        if doc is None:
            self.scene.clear()
            self.detail.hide()
            self.doc = None
            self.signature = None
            self.evidence.clear()
            self.status.setText('等待已确认的对话内容。')
            return
        with doc.lock:
            signature = (id(doc), doc.revision)
            status = doc.status
        self.status.setText(status or 'AI 归纳 · 点击节点或连线查看原话依据')
        if signature == self.signature and not force:
            return
        changed = doc is not self.doc
        center = self.view.mapToScene(self.view.viewport().rect().center())
        self.signature, self.doc = (signature, doc)
        snap = doc.snapshot()
        nodes = display_nodes(snap)
        columns = self.columns_for_view()
        previous = None if changed or columns != self.layout_columns else self.positions
        geometry = layout_graph(nodes, snap['edges'], previous, columns=columns)
        self.layout_columns = columns
        self.positions = geometry.positions
        positions = {ident: (16+(x-30)*COLUMN_STEP/LAYOUT_COLUMN_STEP,
                             56+(y-LAYOUT_TOP)*ROW_STEP/LAYOUT_ROW_STEP)
                     for ident, (x, y) in geometry.positions.items()}
        width = max((x+NODE_WIDTH+16 for x, _ in positions.values()), default=240)
        self.scene.clear()
        # Invalidate visible quotes whenever their source revision changes.
        self.detail.hide()
        self.evidence.clear()
        by_id = {node['id']: node for node in nodes}
        goal = self.scene.addText('本场目标（用户设定） · ' + (snap['goal'] or '尚未设置'), QFont('Microsoft YaHei UI', 10))
        goal.setDefaultTextColor(QColor('#536273'))
        goal.setToolTip(goal.toPlainText())
        goal.setPlainText(QFontMetrics(goal.font()).elidedText(
            goal.toPlainText(), Qt.ElideRight, max(NODE_WIDTH, width-40)))
        goal.setTextWidth(max(NODE_WIDTH, width-32))
        goal.setPos(16, 16)
        for index, edge in enumerate(snap['edges']):
            if edge['source'] not in positions or edge['target'] not in positions:
                continue
            x1, y1 = positions[edge['source']]
            x2, y2 = positions[edge['target']]
            a, b = (x1 + NODE_WIDTH / 2, x2 + NODE_WIDTH / 2)
            path = QPainterPath(QPointF(a, y1 + NODE_HEIGHT))
            if (edge['source'], edge['target']) in geometry.cyclic_edges or y2 - y1 > ROW_STEP:
                side = width - 12 + index % 3 * 9
                points = ((a, y1 + NODE_HEIGHT + 15), (side, y1 + NODE_HEIGHT + 15), (side, y2 - 15), (b, y2 - 15), (b, y2))
            else:
                mid = (y1 + NODE_HEIGHT + y2) / 2
                points = ((a, mid), (b, mid), (b, y2))
            for x, y in points:
                path.lineTo(x, y)
            color = QColor('#B86642' if edge['relation'] in ('卡住', '反驳') else '#99A5B5')
            pen = QPen(color, 1.3)
            if any((by_id[edge[key]]['basis'] != 'stated' for key in ('source', 'target'))):
                pen.setStyle(Qt.DashLine)
            item = Edge(path, edge, self.inspect)
            item.setPen(pen)
            self.scene.addItem(item)
            label = EdgeLabel(edge, item)
            label.setFont(QFont('Microsoft YaHei UI', 8))
            label.setDefaultTextColor(color)
            item.label = label
            if (edge['source'], edge['target']) in geometry.cyclic_edges or y2 - y1 > ROW_STEP:
                # Label the source's horizontal segment, away from a shared join.
                label.setPos((a + side) / 2 - label.boundingRect().width() / 2,
                             y1 + NODE_HEIGHT + 3)
            else:
                # Distinct incoming branches have distinct horizontal midpoints.
                label.setPos((a + b) / 2 + (6 if a == b else -label.boundingRect().width() / 2),
                             mid - label.boundingRect().height() / 2)
            self.scene.addPolygon(QPolygonF([QPointF(b, y2), QPointF(b - 4, y2 - 7), QPointF(b + 4, y2 - 7)]), QPen(color), QBrush(color))
        for node in nodes:
            x, y = positions[node['id']]
            self.scene.addItem(Node(node, x, y, self.inspect))
        if not nodes:
            text = self.scene.addText('等待已确认的对话内容', QFont('Microsoft YaHei UI', 12))
            text.setPos(16, 56)
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-12, -12, 12, 12))
        if changed:
            self.camera_mode = 'read'
            self.reset_read_position = True
        else:
            self.view.centerOn(center)
        self.schedule_camera()

    def fit(self):
        self.detail.hide()
        self.camera_mode = 'overview'
        self.schedule_camera()

    def read_from_top(self):
        self.detail.hide()
        self.camera_mode = 'read'
        self.reset_read_position = True
        self.schedule_camera()

    def manual_camera(self):
        self.camera_mode = 'manual'
        self.reset_read_position = False
        self.zoom_level.setText(f'{self.view.transform().m11():.0%}')
        self.schedule_camera()

    def schedule_camera(self):
        # Run after QStackedWidget/layout/scrollbar geometry settles, not during
        # construction in its hidden 100px viewport. The owned timer is disposed
        # with this widget and never calls Tk from a native Qt callback.
        self.camera_timer.start(0)

    def focus_node(self, ident):
        node = next((item for item in self.scene.items()
                     if isinstance(item, Node) and item.node['id'] == ident), None)
        if node is None:
            return
        self.camera_mode = 'manual'
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.view.resetTransform()
        self.view.centerOn(node.sceneBoundingRect().center())
        self.manual_camera()

    def update_camera(self):
        if not self.isVisible():
            return
        if self.doc is not None and self.columns_for_view() != self.layout_columns:
            self.refresh(force=True)
            return
        overview = self.camera_mode == 'overview'
        policy = Qt.ScrollBarAlwaysOff if overview else Qt.ScrollBarAsNeeded
        self.view.setHorizontalScrollBarPolicy(policy)
        self.view.setVerticalScrollBarPolicy(policy)
        rect = self.scene.sceneRect()
        area = self.view.viewport().size()
        if overview:
            zoom = min(1, (area.width()-8)/max(1, rect.width()),
                       (area.height()-8)/max(1, rect.height()))
            self.view.resetTransform()
            self.view.scale(max(0.001, zoom), max(0.001, zoom))
            self.view.centerOn(rect.center())
        elif self.reset_read_position:
            self.view.resetTransform()
            self.view.horizontalScrollBar().setValue(self.view.horizontalScrollBar().minimum())
            self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().minimum())
            self.reset_read_position = False
        self.zoom_level.setText(f'{self.view.transform().m11():.0%}')

    def inspect(self, node):
        if self.doc is not self.panel.logic_document:
            return
        snapshot = self.doc.snapshot()
        if self.signature != (id(self.doc), snapshot['revision']):
            self.refresh()
            return
        turns = {t['turn_id']: t for t in snapshot['turns']}
        parts = [node['text'], 'AI 归纳，请核对以下原话。']
        for ref in node['evidence']:
            turn = turns.get(ref['turn_id'], {})
            parts.append(f"#{ref['turn_id']}  {turn.get('start_s', 0):.1f}–{turn.get('end_s', 0):.1f}s  {turn.get('role', '未绑定')}\n摘录：{ref['quote']}\n原文：{turn.get('text', '')}")
        self.evidence.setPlainText('\n\n'.join(parts))
        self.detail.show()
