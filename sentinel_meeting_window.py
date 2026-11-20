"""Per-pixel glass meeting window. The Tk controller owns all session state."""
import time
from PySide6.QtCore import Qt, QEvent, QRectF
from PySide6.QtGui import QPainter, QPainterPath, QRegion, QFontMetrics
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMenu, QSizeGrip
from sentinel_glass import apply_backdrop
from sentinel_meeting_icons import IconButton
from sentinel_meeting_content import MeetingContent
from sentinel_meeting_material import paint_glass, paint_reference_skin, rounded_path, SHADOW

STYLE = '''
QWidget { background: transparent; color: #293340; font-family: "Microsoft YaHei UI"; font-size: 10pt; }
QToolButton, QPushButton { border: 0; border-radius: 8px; padding: 0; }
QToolButton { background: rgba(255,255,255,38); border: 1px solid rgba(255,255,255,120); }
QToolButton:hover, QPushButton:hover { background: rgba(255,255,255,100); }
QToolButton:focus { border-color: rgba(193,112,60,135); }
QToolButton:checked { background: rgba(255,211,177,135); border: 1px solid rgba(193,112,60,135); }
QPushButton:focus { background: rgba(255,255,255,95); }
QToolButton:disabled { color: #969DA5; }
QPushButton#reminder { background: rgba(255,235,220,55); border: 1px solid rgba(255,255,255,135); text-align: left; padding: 0 8px; font-weight: 600; }
QPushButton#section { text-align: left; color: #7E8791; font-size: 9pt; padding: 0; }
QPushButton#section:hover { background: transparent; color: #A3532B; }
QLabel#context { color: #34404D; font-size: 9pt; }
QLabel#source { color: #78818A; font-size: 8pt; }
QTextBrowser { background: transparent; border: 0; padding: 0; selection-background-color: #F0D4BE; }
QScrollArea { background: transparent; border: 0; }
QPlainTextEdit { background: transparent; border: 0; padding: 3px; selection-background-color: #F0D4BE; }
QScrollBar:vertical { background: transparent; width: 6px; margin: 2px 0; }
QScrollBar:horizontal { background: transparent; height: 6px; margin: 0 2px; }
QScrollBar::handle { background: rgba(93,106,116,85); border-radius: 3px; min-height: 22px; min-width: 22px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QMenu { background: #FAF8F5; padding: 5px; border: 1px solid #DED7CF; }
QMenu::item { padding: 7px 14px; }
QMenu::item:selected { background: #F6E9DE; }
'''


class MeetingWindow(QWidget):
    WIDTH = 600
    BAR_HEIGHT = 42
    HEADER_HEIGHT = 58
    PANEL_GAP = 14
    CONTENT_HEIGHT = 300

    def __init__(self, panel, dispatch):
        super().__init__()
        self.panel = panel
        self.dispatch = dispatch
        self.view = None
        self.content = None
        self.tint = 74
        self.native_glass = False
        self.headline_value = ''
        self.expanded_size = (self.WIDTH, self.CONTENT_HEIGHT)
        self.graph_size = (760, 440)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle('Sentinel · 会议悬浮窗')
        self.setStyleSheet(STYLE)
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(20, 8, 20, 8)
        self.column.setSpacing(self.PANEL_GAP)
        self.header = QWidget()
        self.header.setFixedHeight(self.BAR_HEIGHT)
        row = QHBoxLayout(self.header)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        self.status = QLabel('○ 麦克风关闭')
        self.status.setFixedWidth(132)
        self.status.setCursor(Qt.SizeAllCursor)
        self.status.installEventFilter(self)
        self.header.installEventFilter(self)
        row.addWidget(self.status)
        self.headline = QPushButton()
        self.headline.setObjectName('reminder')
        self.headline.setMinimumWidth(0)
        self.headline.setFixedHeight(30)
        self.headline.clicked.connect(lambda: self.dispatch(lambda: self.toggle_view('background')))
        row.addWidget(self.headline, 1)
        buttons = (
            ('transcript', '对话转录', lambda: self.toggle_view('transcript')),
            ('graph', '逻辑图', lambda: self.toggle_view('graph')),
            ('background', '背景与推进目标', lambda: self.toggle_view('background')),
            ('listen', '开始监听', panel.toggle_listening),
            ('more', '玻璃与置顶设置', self.show_menu),
            ('home', '打开主页面', lambda: panel.set_layout('compact')),
            ('hide_button', '隐藏，监听继续', panel.hide_window),
        )
        for name, hint, command in buttons:
            glyph = {'listen': 'play', 'home': 'expand', 'hide_button': 'hide'}.get(name, name)
            button = IconButton(glyph, hint, lambda checked=False, action=command: self.dispatch(action))
            if name in ('transcript', 'graph', 'background'):
                button.setCheckable(True)
            setattr(self, name, button); row.addWidget(button)
        self.column.addWidget(self.header)
        self.grip = QSizeGrip(self)
        self.grip.setFixedSize(12, 12)
        self.grip.hide()
        self.setMinimumSize(560, self.HEADER_HEIGHT)
        self.resize(self.WIDTH, self.HEADER_HEIGHT)
        screen = self.screen().availableGeometry()
        self.move(screen.right()-min(self.WIDTH, screen.width())-30, screen.top()+80)
        self.clamp_geometry()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for index, surface in enumerate(self.surface_rects()):
            paint_glass(painter, surface, 17,
                        opacity=self.tint if self.native_glass else 235)
            paint_reference_skin(painter, surface, 'toolbar' if index == 0 else 'panel', self.tint)

    def surface_rects(self):
        surfaces = [QRectF(8, 8, self.width()-16, self.BAR_HEIGHT)]
        if self.view:
            top = 8+self.BAR_HEIGHT+self.PANEL_GAP
            surfaces.append(QRectF(8, top, self.width()-16, max(1, self.height()-top-8)))
        return surfaces

    def apply_material(self):
        self.native_glass = apply_backdrop(int(self.winId()))
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self.apply_material()

    def update_pin(self):
        visible, minimized = self.isVisible(), self.isMinimized()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, self.panel.topmost.get())
        if visible:
            self.showMinimized() if minimized else self.show()
        self.apply_material()

    def show_explicit(self):
        self.setWindowTitle(self.panel.root.title())
        self.update_pin()
        self.clamp_geometry()
        self.showNormal(); self.raise_(); self.activateWindow()
        self.refresh()

    def closeEvent(self, event):
        event.ignore()
        self.dispatch(self.panel.hide_window)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self.drag_origin = (event.globalPosition().toPoint(), self.pos())
            return True
        if event.type() == QEvent.MouseMove and event.buttons() & Qt.LeftButton and hasattr(self, 'drag_origin'):
            point, origin = self.drag_origin
            self.move(origin+event.globalPosition().toPoint()-point)
            return True
        return super().eventFilter(watched, event)

    def clamp_geometry(self):
        area = self.screen().availableGeometry()
        self.setMinimumWidth(min(560, area.width()))
        width, height = min(self.width(), area.width()), min(self.height(), area.height())
        self.resize(width, height)
        self.move(max(area.left(), min(self.x(), area.right()-width+1)),
                  max(area.top(), min(self.y(), area.bottom()-height+1)))

    def toggle_view(self, view):
        self.set_view(None if self.view == view else view)

    def set_view(self, view):
        if view not in (None, 'transcript', 'graph', 'background'):
            raise ValueError('未知会议浮窗视图')
        if self.view:
            if self.view == 'graph':
                self.graph_size = (self.width(), self.height())
            else:
                self.expanded_size = (self.width(), self.height())
        self.view = view
        if view:
            if self.content is None:
                self.content = MeetingContent(self.panel, self)
                self.column.addWidget(self.content, 1)
            self.content.set_view(view)
            self.content.show()
            self.setMinimumHeight(260)
            self.resize(*(self.graph_size if view == 'graph' else self.expanded_size))
            self.grip.show(); self.grip.raise_()
        else:
            if self.content:
                self.content.hide()
            self.grip.hide()
            self.setMinimumHeight(self.HEADER_HEIGHT)
            self.resize(self.expanded_size[0], self.HEADER_HEIGHT)
        for name in ('transcript', 'graph', 'background'):
            getattr(self, name).setChecked(view == name)
        self.clamp_geometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # The mask rounds the native material too, not only the painted fill.
        shape = QPainterPath()
        for surface in self.surface_rects():
            # One HWND with two rounded islands: the gap remains desktop.
            shadow = surface.adjusted(-SHADOW, -SHADOW, SHADOW, SHADOW+2)
            shape = shape.united(rounded_path(shadow, 17+SHADOW))
        self.setMask(QRegion(shape.toFillPolygon().toPolygon()))
        self.grip.move(self.width()-26, self.height()-26)
        self.render_headline()

    def render_headline(self):
        metrics = QFontMetrics(self.headline.font())
        self.headline.setText(metrics.elidedText(self.headline_value, Qt.ElideRight, max(0, self.headline.width()-24)))
        self.headline.setToolTip(self.headline_value)

    def show_menu(self):
        if getattr(self, '_menu', None):
            self._menu.close()
        menu = QMenu(self)
        self._menu = menu
        menu.setAttribute(Qt.WA_DeleteOnClose)
        menu.addAction('玻璃更透 / 更白', lambda: self.dispatch(self.cycle_tint))
        menu.addAction('取消置顶' if self.panel.topmost.get() else '置顶', lambda: self.dispatch(self.panel.toggle_pin))
        menu.addSeparator()
        menu.addAction('设置', lambda: self.dispatch(self.panel.open_desktop_settings))
        menu.aboutToHide.connect(lambda: setattr(self, '_menu', None))
        menu.popup(self.more.mapToGlobal(self.more.rect().bottomLeft()))

    def cycle_tint(self):
        values = (48, 74, 98, 124)
        self.tint = values[(values.index(self.tint)+1) % len(values)]
        self.update()

    def refresh(self):
        p = self.panel
        self.headline_value = ' '.join((('服务提示 · '+p.error.get()) if p.error.get() else p.headline.cget('text')).split())
        self.render_headline()
        elapsed = int(time.monotonic()-p.mic_started) if p.mic_started is not None else 0
        hours, rest = divmod(elapsed, 3600); minutes, seconds = divmod(rest, 60)
        idle_status = '○ 正在启动…' if p.controller.busy and p.phase == '正在启动' else '○ 麦克风关闭'
        self.status.setText(f'● 监听中  {hours:02}:{minutes:02}:{seconds:02}' if p.mic_on else idle_status)
        self.status.setToolTip(getattr(p, 'audio_device', '') or p.state.get())
        self.status.setStyleSheet('color: #39725E' if p.mic_on else 'color: #62707C')
        self.listen.setEnabled(not (p.closing or (p.controller.busy and p.controller.stop_event.is_set())))
        name = 'stop' if p.controller.busy else 'play'
        if self.listen.icon_name != name:
            self.listen.set_icon(name, '停止监听' if p.controller.busy else '开始监听')
        if self.view and self.content:
            self.content.refresh()
