"""Conversation-first workspace: alerts, persistent context, and embedded tools.

This module only arranges and renders widgets. Session and window lifecycle
belong to sentinel_panel; grounded summaries belong to sentinel_briefing.
"""
import tkinter as tk
from tkinter import ttk

from sentinel_theme import (BG, CARD, TEXT, MUTED, ACCENT, ACCENT_HOVER, ACCENT_SOFT,
                            BORDER, BUTTON, HOVER, DISABLED, SUCCESS, WARNING, FONT,
                            ALERT_TEXT, CONTEXT_BG)
from sentinel_window_effects import apply_chrome
from sentinel_scroll import attach_scrollbars
GREEN, AMBER = SUCCESS, WARNING


def button(parent, text, command):
    return tk.Button(parent, text=text, command=command, bg=BG, fg=TEXT,
                     activebackground=HOVER, activeforeground=TEXT,
                     disabledforeground=DISABLED, relief='flat', bd=0, cursor='hand2',
                     highlightthickness=0, font=(FONT, 9), padx=10, pady=6, takefocus=True)


def label(parent, text='', *, size=9, color=TEXT, bold=False, **kwargs):
    return tk.Label(parent, text=text, bg=parent.cget('bg'), fg=color,
                    font=(FONT, size, 'bold' if bold else 'normal'), **kwargs)


class ContextSection(tk.Frame):
    """Bounded, always visible context. Long content scrolls within its section."""
    def __init__(self, parent, title, subtitle, empty, on_source, *, background=CARD,
                 foreground=TEXT, emphasis=False):
        super().__init__(parent, bg=background, padx=14, pady=12)
        row = tk.Frame(self, bg=background)
        row.pack(fill='x', pady=(0, 6))
        label(row, title, size=10, color=ACCENT if emphasis else TEXT, bold=True).pack(side='left')
        source = button(row, '依据', on_source)
        source.configure(bg=background, fg=MUTED, font=(FONT, 8), padx=3, pady=0)
        source.pack(side='right', padx=(5, 0))
        label(row, subtitle, size=8, color=MUTED).pack(side='right')
        area = tk.Frame(self, bg=background)
        area.pack(fill='both', expand=True)
        self.text = tk.Text(area, wrap='word', height=3, width=1, bg=background, fg=foreground,
                            font=(FONT, 11 if emphasis else 10, 'bold' if emphasis else 'normal'), bd=0, highlightthickness=0,
                            spacing3=5, cursor='arrow', takefocus=False)
        attach_scrollbars(self.text)
        self.text.pack(fill='both', expand=True)
        self.text.tag_configure('agreement', foreground=SUCCESS, font=(FONT, 10, 'bold'), spacing1=4)
        self.text.tag_configure('blocker', foreground=ACCENT, font=(FONT, 10, 'bold'), spacing1=4)
        self.value = None
        self.render(empty)

    def render(self, value):
        if value == self.value:
            return
        self.value = value
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        for line in value.splitlines(keepends=True):
            tag = {'已达成': 'agreement', '待确认 / 卡点': 'blocker'}.get(line.strip())
            self.text.insert('end', line, (tag,) if tag else ())
        self.text.configure(state='disabled')
        self.text.yview_moveto(0)


def build_view(p, args, assets, logs_dir, scene_labels):
    root = p.root
    p.ui_scale = root.winfo_fpixels('1i') / 96
    u = lambda value: round(value * p.ui_scale)
    root.configure(bg=BG)
    root.attributes('-topmost', p.topmost.get())
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', font=(FONT, 9), background=BG, foreground=TEXT)
    for name in ('TFrame', 'TLabel', 'TCheckbutton', 'TRadiobutton'):
        style.configure(name, background=BG, foreground=TEXT)
    style.configure('TButton', background=BUTTON, foreground=TEXT, padding=(9, 6), borderwidth=0, relief='flat')
    style.map('TButton', background=[('active', HOVER)], foreground=[('disabled', DISABLED)])
    style.configure('TEntry', fieldbackground=CARD, foreground=TEXT, padding=7, bordercolor=BORDER)
    style.configure('TCombobox', fieldbackground=CARD, background=BUTTON, foreground=TEXT, padding=5)
    style.map('TCombobox', fieldbackground=[('readonly', CARD)], foreground=[('readonly', TEXT)])
    try:
        root.iconbitmap(str(assets / 'sentinel-idle.ico'))
    except (OSError, tk.TclError):
        pass
    p.shell = tk.Frame(root, bg=BG)
    p.shell.pack(fill='both', expand=True)
    p.outer = outer = tk.Frame(p.shell, bg=BG, padx=18, pady=10)
    header = tk.Frame(outer, bg=BG)
    header.pack(fill='x')
    p.brand = label(header, 'Sentinel', size=13)
    p.brand.pack(side='left')
    p.tagline = label(header, '  /  对话提醒', color=MUTED)
    p.tagline.pack(side='left')
    for widget in (header, p.brand, p.tagline):
        widget.bind('<ButtonPress-1>', p.drag_start)
        widget.bind('<B1-Motion>', p.drag_move)
    button(header, '×', p.close).pack(side='right')
    p.fold_button = button(header, '收起', p.toggle_fold)
    p.fold_button.pack(side='right', padx=4)
    button(header, '设置', p.open_desktop_settings).pack(side='right')
    # Kept as a controller target; pinning is an explicit setting, not main chrome.
    p.pin_button = button(header, '置顶', p.toggle_pin)
    p.toolbar = tk.Frame(header, bg=BG)
    p.toolbar.pack(side='right', padx=8)
    p.phase_text = tk.StringVar(value='待机')
    p.phase_label = label(p.toolbar, textvariable=p.phase_text, color=ACCENT)
    p.phase_label.pack(side='left', padx=(0, 12))
    p.mic_text = tk.StringVar(value='● 麦克风关闭')
    p.mic_label = label(p.toolbar, textvariable=p.mic_text, color=MUTED)
    p.mic_label.pack(side='left')
    p.actions = tk.Frame(p.toolbar, bg=BG)
    p.actions.pack(side='right')
    p.start_button = button(p.actions, '开始监听', p.start)
    p.start_button.configure(bg=ACCENT, fg=CARD, activebackground=ACCENT_HOVER, activeforeground=CARD)
    p.start_button.pack(side='left')
    p.stop_button = button(p.actions, '停止', p.stop)
    p.stop_button.configure(state='disabled')
    p.stop_button.pack(side='left', padx=(5, 0))
    p.section_rule = tk.Frame(outer, bg=BORDER, height=1)
    p.section_rule.pack(fill='x')
    p.navigation = tk.Frame(outer, bg=BG)
    p.navigation.pack(fill='x', pady=(3, 5))
    p.home_button = button(p.navigation, '提醒', lambda: p.set_layout('compact'))
    p.home_button.pack(side='left')
    p.logic_button = button(p.navigation, '逻辑图', p.open_logic)
    p.logic_button.pack(side='left', padx=3)
    p.expand_button = button(p.navigation, '转写', lambda: p.set_layout('detail'))
    p.expand_button.pack(side='left')
    p.meeting_button = button(p.navigation, '会议悬浮 ↗', lambda: p.set_layout('meeting'))
    p.meeting_button.pack(side='left', padx=6)
    p.prepare_button = button(p.navigation, '本场准备', p.open_preparation)
    p.prepare_button.pack(side='right')
    p.notes_button = button(p.navigation, '私人笔记', p.quick_note)
    p.notes_button.pack(side='right', padx=4)
    p.error = tk.StringVar(value=p.preferences.error)
    p.desktop_notice = tk.StringVar()
    p.error_label = label(outer, textvariable=p.error, color=WARNING, anchor='w', justify='left')
    p.notice_label = label(outer, textvariable=p.desktop_notice, color=WARNING, anchor='w', justify='left')
    p.desktop_problems = {}
    p.body = tk.Frame(outer, bg=BG)
    p.body.pack(fill='both', expand=True)
    p.workspace = tk.Frame(p.body, bg=BG)
    p.workspace.columnconfigure(0, weight=1)
    p.workspace.columnconfigure(1, weight=0, minsize=u(276))
    p.workspace.rowconfigure(0, weight=1)
    p.card_shell = main = tk.Frame(p.workspace, bg=BG)
    main.inner = main
    main.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
    p.advice_canvas = canvas = tk.Canvas(main, bg=BG, width=u(380), height=u(210), highlightthickness=0)
    attach_scrollbars(canvas, wheel=True)
    canvas.pack(fill='both', expand=True)
    advice = tk.Frame(canvas, bg=BG)
    item = canvas.create_window(0, 0, window=advice, anchor='nw')
    advice.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox('all')))
    def resize_advice(event):
        canvas.itemconfigure(item, width=event.width)
        for widget in (p.headline, p.evidence, p.action, p.recent_label):
            widget.configure(wraplength=max(100, event.width - u(44)))
        # Keep spoken suggestions on a comfortable reading measure instead of
        # stretching one long sentence across the entire conversation surface.
        p.action.configure(wraplength=max(100, min(u(520), event.width - u(44))))
    canvas.bind('<Configure>', resize_advice)
    alert = tk.Frame(advice, bg=ACCENT_SOFT)
    alert.pack(fill='x')
    tk.Frame(alert, bg=ACCENT, width=u(4)).pack(side='left', fill='y')
    alert_content = tk.Frame(alert, bg=ACCENT_SOFT, padx=18, pady=18)
    alert_content.pack(side='left', fill='both', expand=True)
    label(alert_content, '当前提醒', size=9, color=ACCENT, bold=True).pack(anchor='w', pady=(0, 10))
    p.headline = label(alert_content, '专注当下这场对话', size=19, bold=True, color=ALERT_TEXT,
                       wraplength=u(430), anchor='w', justify='left')
    p.headline.pack(fill='x', pady=(0, 12))
    p.evidence = label(alert_content, '重要变化出现时，会在这里提醒你。', size=10, color=MUTED, wraplength=u(430), anchor='w', justify='left')
    p.evidence.pack(fill='x')
    response = tk.Frame(advice, bg=CARD, padx=18, pady=18)
    response.pack(fill='x', pady=(12, 0))
    response_heading = tk.Frame(response, bg=CARD)
    response_heading.pack(fill='x', pady=(0, 12))
    label(response_heading, '建议回应', size=10, color=ACCENT, bold=True).pack(side='left')
    label(response_heading, '可直接参考', size=8, color=MUTED).pack(side='right')
    p.action = label(response, '准备好后，开始监听。', size=14, wraplength=u(430), anchor='w', justify='left')
    p.action.pack(fill='x')
    p.recent_label = label(advice, color=MUTED, size=9, wraplength=u(430), anchor='w', justify='left')
    p.recent_label.pack(fill='x', pady=(24, 0))
    label(main, 'AI 提醒 · 以实际语境和原话为准', size=8, color=MUTED).pack(anchor='w', pady=(12, 0))
    p.context_column = side = tk.Frame(p.workspace, bg=BG, width=u(276))
    side.grid(row=0, column=1, sticky='nsew')
    side.columnconfigure(0, weight=1)
    for i, (weight, minimum) in enumerate(((0, 100), (3, 160), (1, 110))):
        side.rowconfigure(i, weight=weight, minsize=u(minimum))
    p.background_view = ContextSection(side, '背景', 'AI 归纳', '等待对话中的背景信息。', lambda: p.show_context_sources('background'), background=CONTEXT_BG, foreground=MUTED)
    p.current_view = ContextSection(side, '当前', '进展与卡点', '等待更多已确认的对话内容。', lambda: p.show_context_sources('current'))
    p.next_goal_view = ContextSection(side, '下一步目标', 'AI 建议', '等待有依据的推进目标。', lambda: p.show_context_sources('next_goal'), background=ACCENT_SOFT, foreground=ALERT_TEXT, emphasis=True)
    for i, view in enumerate((p.background_view, p.current_view, p.next_goal_view)):
        view.grid(row=i, column=0, sticky='nsew', pady=(0, 8 if i < 2 else 0))
    p.situation_text = tk.StringVar()
    p.detail = tk.Frame(p.body, bg=BG)
    p.state = tk.StringVar(value='待机 · 点击开始监听')
    p.latest = tk.StringVar(value='麦克风尚未开启')
    p.session_summary = tk.StringVar(value='0 个确认片段 · 私人笔记单独保存')
    row = tk.Frame(p.detail, bg=BG)
    row.pack(fill='x', pady=(4, 8))
    label(row, '实时转写', size=11).pack(side='left')
    label(row, textvariable=p.session_summary, size=8, color=MUTED).pack(side='right')
    p.latest_label = label(p.detail, textvariable=p.latest, color=SUCCESS, anchor='w')
    p.latest_label.pack(fill='x')
    p.transcript = tk.Text(p.detail, wrap='word', bg=CARD, fg=TEXT, bd=0, height=5,
                           font=(FONT, 10), padx=14, pady=10, state='disabled')
    attach_scrollbars(p.transcript)
    p.transcript.pack(fill='both', expand=True, pady=8)
    p.log_path = tk.StringVar(value='日志：' + str(logs_dir()))
    p.log_label = label(p.detail, textvariable=p.log_path, size=8, color=MUTED, anchor='w')
    p.log_label.pack(fill='x')
    p.speaker_label = label(p.detail, textvariable=p.speaker_status, size=8, color=MUTED, anchor='w')
    p.speaker_label.pack(fill='x')
    label(p.detail, textvariable=p.state, size=8, color=MUTED, anchor='w').pack(fill='x')
    # Prepared here so the session controller always has valid controls, even
    # before the embedded preparation page is first shown. No widget reparenting.
    p.preparation = tk.Frame(p.body, bg=BG, padx=16, pady=12)
    label(p.preparation, '本场准备', size=15).pack(anchor='w', pady=(0, 16))
    label(p.preparation, '本场目标', color=MUTED).pack(anchor='w', pady=(0, 6))
    p.goal_editor = tk.Frame(p.preparation, bg=BG)
    p.goal_editor.pack(fill='x')
    p.goal_entry = ttk.Entry(p.goal_editor, textvariable=p.goal)
    p.goal_entry.pack(fill='x')
    p.goal_summary = label(p.preparation, textvariable=p.goal, color=MUTED)
    p.scene_controls = tk.Frame(p.preparation, bg=BG)
    p.scene_controls.pack(fill='x', pady=18)
    label(p.scene_controls, '对话场景', color=MUTED).pack(side='left', padx=(0, 12))
    p.scene = tk.StringVar(value=next((k for k, v in scene_labels.items() if v == args.scene), '合同谈判'))
    p.scene_box = ttk.Combobox(p.scene_controls, textvariable=p.scene, values=list(scene_labels), state='readonly', width=14)
    p.scene_box.pack(side='left')
    button(p.preparation, '说话人 / 本人声纹', p.open_speakers).pack(anchor='w', pady=(0, 18))
    label(p.preparation, '知识库 · 可拆卸弹药舱', size=11).pack(anchor='w')
    p.knowledge_summary = tk.StringVar(value=p.knowledge_catalog.summary())
    label(p.preparation, textvariable=p.knowledge_summary, color=MUTED, wraplength=u(600), justify='left').pack(anchor='w', pady=8)
    button(p.preparation, '选择知识库  +', p.open_knowledge).pack(anchor='w')
    p.bar_frame = tk.Frame(p.shell, bg=BG, padx=10, pady=8)
    p.bar_text = tk.StringVar(value='哨兵 · 待机')
    p.bar_label = label(p.bar_frame, textvariable=p.bar_text, anchor='w')
    button(p.bar_frame, '打开', lambda: p.set_layout('compact')).pack(side='right')
    p.mini_listen_button = button(p.bar_frame, '○ 麦克风关', p.toggle_listening)
    p.mini_listen_button.pack(side='right', padx=4)
    p.bar_label.pack(side='left', fill='x', expand=True)
    p.bar_label.bind('<ButtonPress-1>', p.drag_start)
    p.bar_label.bind('<B1-Motion>', p.drag_move)
    from sentinel_overlay_ui import MeetingOverlay
    p.meeting_overlay = MeetingOverlay(p)
    p.resize_grip = label(p.shell, '⋰', color=MUTED, cursor='size_nw_se')
    p.resize_grip.bind('<ButtonPress-1>', p.resize_start)
    p.resize_grip.bind('<B1-Motion>', p.resize_move)
    p._chrome_job = p._fit_job = None
    root.bind('<Map>', lambda event: p.schedule_chrome(force=True) if event.widget is root else None, add='+')
    apply_layout(p, 'compact')


def refresh_view(p):
    brief = p.briefing.snapshot()
    situation = brief['situation'] or ('等待更多已确认的对话内容。' if p.turns else '等待对话开始。')
    p.situation_text.set(situation)
    p.background_view.render(brief.get('background') or '等待对话中的背景信息。')
    sections = [situation]
    if brief['agreements']:
        sections.append('已达成\n' + '\n'.join('· ' + item['text'] for item in brief['agreements']))
    if brief['open_questions']:
        sections.append('待确认 / 卡点\n' + '\n'.join('· ' + item['text'] for item in brief['open_questions']))
    p.current_view.render('\n'.join(sections))
    p.next_goal_view.render(brief.get('next_goal') or '等待有依据的推进目标。')
    p.knowledge_summary.set(p.knowledge_catalog.summary())
    summary = ' '.join((p.error.get() or p.headline.cget('text')).split())
    p.bar_text.set('哨兵 · ' + (summary[:25] + '…' if len(summary) > 25 else summary))
    p.mini_listen_button.configure(text='● 麦克风开' if p.mic_on else '○ 麦克风关', fg=GREEN if p.mic_on else MUTED,
                                  state='disabled' if p.closing or (p.controller.busy and p.controller.stop_event.is_set()) else 'normal')
    p.mic_label.configure(fg=GREEN if p.mic_on else MUTED)
    p.meeting_overlay.refresh()
    p.session_summary.set(f'{len(p.turns)} 个确认片段 · 私人笔记单独保存')
    earlier = p.recent_alerts[:-1][-2:]
    p.recent_label.configure(text=('此前提醒\n' + '\n'.join('· ' + title for title in reversed(earlier))) if earlier else '')
    if (p.window_visible() and p.layout_mode in ('compact', 'meeting') and not p.pages.active
            and (p.layout_mode == 'meeting' or not p.preparation.winfo_manager())):
        p.unread_alert = False
    p.home_button.configure(text='提醒 ·' if p.unread_alert else '提醒')
    p.sync_desktop()
    for widget, value in ((p.error_label, p.error.get()), (p.notice_label, p.desktop_notice.get())):
        if value and not widget.winfo_manager():
            widget.pack(side='bottom', fill='x', before=p.body, pady=(3, 0))
        elif not value:
            widget.pack_forget()


def apply_layout(p, mode):
    root = p.root
    u = lambda value: round(value * p.ui_scale)
    if getattr(p, '_active_layout', None) in ('compact', 'detail'):
        p._workspace_position = (root.winfo_x(), root.winfo_y())
    for widget in (p.outer, p.bar_frame, p.meeting_overlay, p.workspace, p.detail, p.preparation):
        widget.pack_forget()
    p.resize_grip.place_forget()
    root.overrideredirect(mode != 'detail')
    root.configure(bg=BG)
    p.shell.configure(bg=root.cget('bg'))
    if mode == 'bar':
        p.bar_frame.pack(fill='both', expand=True)
        root.minsize(u(480), u(52))
        width, height = u(540), u(52)
    else:
        p.outer.pack(fill='both', expand=True)
        (p.detail if mode == 'detail' else p.workspace).pack(fill='both', expand=True)
        root.minsize(min(u(800), root.winfo_screenwidth() - 40), min(u(560), root.winfo_screenheight() - 80))
        width, height = (u(960), u(700)) if mode == 'detail' else (u(960), u(650))
        p.resize_grip.place(relx=1, rely=1, anchor='se', x=-2, y=-1)
    width, height = min(width, root.winfo_screenwidth() - 40), min(height, root.winfo_screenheight() - 80)
    pos = getattr(p, '_workspace_position', (80, 40))
    x = max(0, min(root.winfo_screenwidth() - width, pos[0]))
    y = max(0, min(root.winfo_screenheight() - height - 40, pos[1]))
    root.geometry(f'{width}x{height}+{x}+{y}')
    p._active_layout = mode
    set_page_focus(p)


def set_page_focus(p, title=None):
    """Only update navigation; listening controls stay visible on every page."""
    p.page_focus = title
    p.tagline.configure(text='  /  ' + (title or ('实时转写' if p.layout_mode == 'detail' else '对话提醒')))
    active = 'graph' if title and '逻辑' in title else ('tool' if title else ('transcript' if p.layout_mode == 'detail' else 'home'))
    for widget, name in ((p.home_button, 'home'), (p.logic_button, 'graph'), (p.expand_button, 'transcript')):
        widget.configure(bg=ACCENT_SOFT if active == name else BG, fg=ACCENT if active == name else MUTED)
    p.schedule_chrome()
