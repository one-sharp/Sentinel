"""Readable meeting content: transcript and one continuous context column.

Context labels grow with their text. Only the column scrolls, so background,
progress and next goal are never clipped into individual miniature editors.
"""
from html import escape
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QPlainTextEdit,
                               QTextBrowser, QPushButton, QLabel, QScrollArea,
                               QStackedWidget, QFrame)
from sentinel_meeting_graph import MeetingGraph


def text_area():
    widget = QPlainTextEdit()
    widget.setReadOnly(True)
    widget.setFrameShape(QPlainTextEdit.NoFrame)
    return widget


def update_text(widget, value, *, follow=False, reset=False):
    if value == widget.toPlainText():
        return
    bar = widget.verticalScrollBar()
    at_end, position = bar.value() >= bar.maximum()-2, bar.value()
    widget.setPlainText(value)
    bar.setValue(0 if reset else (bar.maximum() if follow and at_end else position))


def context_label(name='context'):
    label = QLabel()
    label.setObjectName(name)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
    return label


def update_label(widget, value):
    if widget.text() != value:
        widget.setText(value)


def render_transcript(widget, turns):
    bar = widget.verticalScrollBar()
    at_end, position = bar.value() >= bar.maximum()-2, bar.value()
    rows = []
    for turn in turns:
        start = int(turn.get('start_s', 0))
        role = escape(turn.get('role', '未绑定'))
        text = escape(turn.get('text', '')).replace('\n', '<br>')
        rows.append(f'<p style="margin:0 0 10px 0">'
                    f'<span style="color:#8A929A;font-size:8pt">{start//60:02}:{start%60:02}&nbsp;&nbsp;{role}</span><br>'
                    f'<span style="color:#34404D;font-size:9pt">{text}</span></p>')
    widget.setHtml(''.join(rows))
    bar.setValue(bar.maximum() if at_end else position)


class MeetingContent(QStackedWidget):
    def __init__(self, panel, parent=None):
        super().__init__(parent)
        self.panel = panel
        self.last_turns = None
        self.view = None
        self.reading = QWidget()
        row = QHBoxLayout(self.reading)
        row.setContentsMargins(5, 12, 5, 12)
        row.setSpacing(14)
        self.left = QStackedWidget()
        self.transcript = QTextBrowser()
        self.transcript.setOpenLinks(False)
        self.transcript.setOpenExternalLinks(False)
        self.transcript.setFrameShape(QFrame.NoFrame)
        self.transcript.document().setDocumentMargin(0)
        self.transcript.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.reminder = text_area()
        self.left.addWidget(self.transcript)
        self.left.addWidget(self.reminder)
        row.addWidget(self.left, 3)
        separator = QFrame()
        separator.setFixedWidth(1)
        separator.setStyleSheet('background:rgba(120,135,150,25)')
        row.addWidget(separator)
        self.sidebar = QScrollArea()
        self.sidebar.setFrameShape(QFrame.NoFrame)
        self.sidebar.setWidgetResizable(True)
        self.sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.sidebar.setMinimumWidth(190)
        side = QWidget()
        col = QVBoxLayout(side)
        col.setContentsMargins(0, 0, 6, 0)
        col.setSpacing(5)
        self.context = []
        for index, (title, section) in enumerate((('背景', 'background'), ('当前', 'current'), ('下一步 · AI 建议', 'next_goal'))):
            if index:
                col.addSpacing(7)
            label = QPushButton(title)
            label.setObjectName('section')
            label.setFixedHeight(17)
            label.setToolTip('查看原话依据')
            label.clicked.connect(lambda checked=False, s=section: self.toggle_sources(s))
            col.addWidget(label)
            text = context_label()
            col.addWidget(text)
            self.context.append(text)
        self.sources = context_label('source')
        col.addWidget(self.sources)
        self.sources.hide()
        col.addStretch(1)
        self.sidebar.setWidget(side)
        row.addWidget(self.sidebar, 2)
        self.addWidget(self.reading)
        self.graph_view = MeetingGraph(panel)
        self.addWidget(self.graph_view)

    def set_view(self, view):
        self.view = view
        if view == 'graph':
            p = self.panel
            if p.logic_document is None:
                from sentinel_logic import GraphDocument
                p.logic_document = GraphDocument(p.controller.session_id or p.standby_id, p.goal.get(), p.scene.get())
            p.enable_logic_analysis()
            self.setCurrentWidget(self.graph_view)
        else:
            self.setCurrentWidget(self.reading)
            self.left.setCurrentWidget(self.transcript if view == 'transcript' else self.reminder)
        self.refresh()

    def show_sources(self, section):
        brief = self.panel.briefing.snapshot()
        if section == 'current':
            quotes = [q for item in brief['agreements']+brief['open_questions'] for q in item['quotes']]
        else:
            quotes = brief.get(section+'_quotes', [])
        self.source_section = section
        value = ('原话依据 · AI 归纳需核对\n'+'\n'.join('“'+q+'”' for q in dict.fromkeys(quotes))
                 if quotes else '暂无有效引文，请核对实时转写。')
        update_label(self.sources, value)

    def toggle_sources(self, section):
        if self.sources.isVisible() and getattr(self, 'source_section', None) == section:
            self.sources.hide()
        else:
            self.show_sources(section)
            self.sources.show()
            self.sidebar.widget().layout().activate()
            self.sidebar.ensureWidgetVisible(self.sources, 0, 8)

    def refresh(self):
        p = self.panel
        if self.view == 'graph':
            self.graph_view.refresh()
            return
        brief = p.briefing.snapshot()
        current = [brief['situation']] if brief['situation'] else []
        if brief['agreements']:
            current.append('已达成 · '+'；'.join(item['text'] for item in brief['agreements']))
        if brief['open_questions']:
            current.append('待推进 · '+'；'.join(item['text'] for item in brief['open_questions']))
        if not current:
            current.append('等待已确认的对话内容。')
        values = (brief['background'] or '等待背景信息。', '\n'.join(current), brief['next_goal'] or '等待有依据的推进目标。')
        for widget, value in zip(self.context, values):
            update_label(widget, value)
        if hasattr(self, 'source_section'):
            self.show_sources(self.source_section)
        revision = (p.controller.session_id, p.turn_view_revision, p.source_epoch)
        if revision != self.last_turns:
            render_transcript(self.transcript, p.turns.values())
            self.last_turns = revision
        value = p.headline.cget('text')+'\n\n'+p.evidence.cget('text')+'\n\n建议回应 · AI\n'+p.action.cget('text')
        if p.error.get():
            value = '服务提示：'+p.error.get()+'\n\n'+value
        update_text(self.reminder, value, reset=True)
