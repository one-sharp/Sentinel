"""Sentinel's three views. Rendering only; all controls use the existing panel."""
import ctypes as C
from ctypes import wintypes as W
import logging
import os
import tkinter as tk
from tkinter import ttk

BG = '#101C28'
CARD = '#123038'
TEXT = '#F1F7FA'
MUTED = '#9BB0BF'
GREEN = '#36E0D0'
AMBER = '#FFBC77'
BORDER = '#314956'
BUTTON = '#20313F'
FONT = 'Microsoft YaHei UI'


def button(parent, text, command):
    return tk.Button(parent, text=text, command=command, bg=BUTTON, fg=TEXT,
                     activebackground='#304A59', activeforeground=TEXT,
                     disabledforeground='#687F8C', relief='flat', bd=0, cursor='hand2',
                     font=(FONT, 10), padx=12, pady=8, takefocus=True)


class RoundedCard(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, bg=BG, highlightthickness=0, bd=0)
        self.inner = tk.Frame(self, bg=CARD, padx=18, pady=10)
        self.item = self.create_window(1, 1, anchor='nw', window=self.inner)
        self.bind('<Configure>', self.draw)

    def draw(self, event):
        w, h, r = event.width-1, event.height-1, 18
        self.delete('border')
        points = [r,1,w-r,1,w,1,w,r,w,h-r,w,h,w-r,h,r,h,1,h,1,h-r,1,r,1,1]
        self.create_polygon(points, smooth=True, splinesteps=24, fill=CARD,
                            outline='#2CA99F', width=1, tags='border')
        self.tag_lower('border')
        self.coords(self.item, 9, 8)
        self.itemconfigure(self.item, width=max(10,w-18), height=max(10,h-16))


def apply_chrome(root, floating):
    """Round borderless windows on Win10/11. No microphone/native event changes.

    SetWindowRgn takes ownership on success; delete our GDI region only on failure.
    https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowrgn
    """
    if os.name != 'nt' or not root.winfo_ismapped():
        return
    try:
        user, gdi = C.WinDLL('user32'), C.WinDLL('gdi32')
        user.GetParent.argtypes, user.GetParent.restype = [W.HWND], W.HWND
        user.SetWindowRgn.argtypes, user.SetWindowRgn.restype = [W.HWND,W.HANDLE,W.BOOL], C.c_int
        gdi.CreateRoundRectRgn.argtypes = [C.c_int]*6
        gdi.CreateRoundRectRgn.restype = W.HANDLE
        gdi.DeleteObject.argtypes, gdi.DeleteObject.restype = [W.HANDLE], W.BOOL
        hwnd = user.GetParent(root.winfo_id()) or root.winfo_id()
        if floating:
            # Tk marks override-redirect windows as tool windows. Retain an app
            # taskbar entry so the floating view remains discoverable/recoverable.
            user.GetWindowLongPtrW.argtypes,user.GetWindowLongPtrW.restype = [W.HWND,C.c_int],C.c_ssize_t
            user.SetWindowLongPtrW.argtypes,user.SetWindowLongPtrW.restype = [W.HWND,C.c_int,C.c_ssize_t],C.c_ssize_t
            exstyle = user.GetWindowLongPtrW(hwnd,-20)
            wanted = (exstyle & ~0x80) | 0x40000  # TOOLWINDOW -> APPWINDOW
            if wanted != exstyle:
                user.SetWindowLongPtrW(hwnd,-20,wanted)
                user.SetWindowPos.argtypes = [W.HWND,W.HWND,C.c_int,C.c_int,C.c_int,C.c_int,W.UINT]
                user.SetWindowPos(hwnd,None,0,0,0,0,0x37)
            rect = W.RECT()
            user.GetWindowRect.argtypes = [W.HWND,C.POINTER(W.RECT)]
            user.GetWindowRect(hwnd,C.byref(rect))
            radius = max(20,round(24*root.winfo_fpixels('1i')/96))
            region = gdi.CreateRoundRectRgn(0,0,rect.right-rect.left+1,rect.bottom-rect.top+1,radius,radius)
            if region and not user.SetWindowRgn(hwnd,region,True):
                gdi.DeleteObject(region)
        else:
            user.SetWindowRgn(hwnd,None,True)
            # Dark native title bar; older Windows builds may simply ignore it.
            dark = C.c_int(1)
            dwm = C.WinDLL('dwmapi')
            dwm.DwmSetWindowAttribute.argtypes = [W.HWND,W.DWORD,W.LPCVOID,W.DWORD]
            dwm.DwmSetWindowAttribute(hwnd,20,C.byref(dark),C.sizeof(dark))
    except (OSError, AttributeError):
        logging.debug('Optional window chrome unavailable',exc_info=True)


def build_view(p, args, assets, logs_dir, scene_labels):
    root = p.root
    root.geometry('560x440+80+80')
    root.minsize(500,420)
    root.configure(bg=BG)
    root.attributes('-topmost',p.topmost.get())
    root.overrideredirect(True)
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.',font=(FONT,10),background=BG,foreground=TEXT)
    for name in ('TFrame','TLabel','TCheckbutton','TRadiobutton'):
        style.configure(name,background=BG,foreground=TEXT)
    style.map('TCheckbutton',background=[('active',BUTTON)])
    style.configure('TButton',background=BUTTON,foreground=TEXT,padding=8,borderwidth=0)
    style.map('TButton',background=[('active','#304A59')],foreground=[('disabled','#687F8C')])
    style.configure('TEntry',fieldbackground='#192C3A',foreground=TEXT,insertcolor=TEXT,bordercolor=BORDER,padding=7)
    style.map('TEntry',fieldbackground=[('disabled',BUTTON)],foreground=[('disabled',MUTED)])
    style.configure('TCombobox',fieldbackground=BUTTON,background=BUTTON,foreground=TEXT,arrowcolor=MUTED,padding=5)
    style.map('TCombobox',fieldbackground=[('readonly',BUTTON)],foreground=[('readonly',TEXT)])
    root.option_add('*TCombobox*Listbox.background',BUTTON)
    root.option_add('*TCombobox*Listbox.foreground',TEXT)
    root.option_add('*TCombobox*Listbox.selectBackground','#21665F')

    p.shell = tk.Frame(root,bg=BG,highlightbackground=BORDER,highlightthickness=1)
    p.shell.pack(fill='both',expand=True)
    p.outer = outer = tk.Frame(p.shell,bg=BG,padx=18,pady=12)
    outer.pack(fill='both',expand=True)
    header = tk.Frame(outer,bg=BG)
    header.pack(fill='x')
    try:
        p.logo_image = tk.PhotoImage(file=str(assets/'sentinel-logo.png')).subsample(38,38)
        logo = tk.Label(header,image=p.logo_image,bg='#E8F4F2',padx=3,pady=3)
        logo.pack(side='left',padx=(0,10))
        root.iconbitmap(str(assets/'sentinel-idle.ico'))
    except (OSError,tk.TclError):
        logging.exception('Logo could not be loaded')
    p.brand = tk.Label(header,text='Sentinel 哨兵',font=(FONT,17,'bold'),bg=BG,fg=TEXT)
    p.brand.pack(side='left')
    for widget in (header,p.brand):
        widget.bind('<ButtonPress-1>',p.drag_start)
        widget.bind('<B1-Motion>',p.drag_move)
    button(header,'×',p.close).pack(side='right',padx=(5,0))
    p.fold_button = button(header,'﹣',p.toggle_fold)
    p.fold_button.pack(side='right',padx=(4,0))
    button(header,'设置',p.open_desktop_settings).pack(side='right',padx=(4,0))
    p.pin_button = button(header,'置顶 ●' if p.topmost.get() else '置顶',p.toggle_pin)
    p.pin_button.pack(side='right')
    tk.Frame(outer,bg=BORDER,height=1).pack(fill='x',pady=(12,10))
    status = tk.Frame(outer,bg=BG)
    status.pack(fill='x',pady=(0,8))
    p.phase_text = tk.StringVar(value='待机')
    p.phase_label = tk.Label(status,textvariable=p.phase_text,font=(FONT,12,'bold'),fg=TEXT,bg=BG)
    p.phase_label.pack(side='left')
    p.mic_text = tk.StringVar(value='● 麦克风关闭')
    p.mic_label = tk.Label(status,textvariable=p.mic_text,font=(FONT,10,'bold'),fg=MUTED,bg=BG)
    p.mic_label.pack(side='right')
    p.state = tk.StringVar(value='待机 · 点击开始监听')
    p.body = tk.Frame(outer,bg=BG)
    p.body.pack(fill='both',expand=True)
    p.goal_summary = tk.Label(p.body,textvariable=p.goal,font=(FONT,10),bg=BG,fg=MUTED,
                              anchor='w',justify='left',wraplength=500)
    p.goal_summary.pack(fill='x',pady=(0,8))
    p.goal_summary.configure(cursor='hand2')
    p.goal_summary.bind('<Button-1>',lambda event:p.set_layout('detail'))
    p.goal_editor = tk.Frame(p.body,bg=BG)
    tk.Label(p.goal_editor,text='本次目标',font=(FONT,11,'bold'),fg=MUTED,bg=BG).pack(side='left',padx=(0,14))
    p.goal_entry = ttk.Entry(p.goal_editor,textvariable=p.goal,font=(FONT,12))
    p.goal_entry.pack(side='left',fill='x',expand=True)
    p.scene_controls = tk.Frame(p.body,bg=BG)
    label = next((k for k,v in scene_labels.items() if v==args.scene),'合同谈判')
    p.scene = tk.StringVar(value=label)
    p.scene_box = ttk.Combobox(p.scene_controls,textvariable=p.scene,values=list(scene_labels),state='readonly',width=12)
    p.scene_box.pack(side='left')
    button(p.scene_controls,'说话人 / 本人声纹 / 角色绑定',p.open_speakers).pack(side='right')
    p.logic_button=button(p.scene_controls,'对话逻辑图 ↗',p.open_logic)
    p.logic_button.pack(side='right',padx=(0,8))

    p.card_shell = RoundedCard(p.body)
    p.card_shell.pack(fill='both',expand=True,pady=(0,8))
    card = p.card_shell.inner
    p.advice_label = tk.Label(card,text='◆  当前建议',font=(FONT,11,'bold'),fg=GREEN,bg=CARD)
    p.advice_label.pack(anchor='w')
    p.headline = tk.Label(card,text='待机，尚未开始监听',font=(FONT,21,'bold'),fg=TEXT,bg=CARD,
                           wraplength=460,justify='left',anchor='w')
    p.headline.pack(fill='x',pady=(6,5))
    p.evidence = tk.Label(card,text='确认目标后开始，重要提醒会显示在这里。',font=(FONT,10),fg=MUTED,bg=CARD,
                           wraplength=460,justify='left',anchor='w')
    p.evidence.pack(fill='x')
    p.next_label = tk.Label(card,text='下一步行动',font=(FONT,9,'bold'),fg=GREEN,bg=CARD)
    p.next_label.pack(anchor='w',pady=(12,3))
    p.action = tk.Label(card,text='准备好后开始，随时可以停止。',font=(FONT,12,'bold'),fg=TEXT,bg=CARD,
                         wraplength=460,justify='left',anchor='w')
    p.action.pack(fill='x')

    p.detail = tk.Frame(p.body,bg=BG)
    detail_header = tk.Frame(p.detail,bg=BG)
    detail_header.pack(fill='x',pady=(0,8))
    tk.Label(detail_header,text='实时转写',font=(FONT,13,'bold'),fg=TEXT,bg=BG).pack(side='left')
    p.session_summary = tk.StringVar(value='0 个确认片段 · 私人笔记单独保存')
    tk.Label(detail_header,textvariable=p.session_summary,font=(FONT,10),fg=MUTED,bg=BG).pack(side='right')
    p.latest = tk.StringVar(value='麦克风尚未开启')
    p.latest_label = tk.Label(p.detail,textvariable=p.latest,fg=GREEN,bg=BG,wraplength=880,
                              justify='left',anchor='w',font=(FONT,11))
    p.latest_label.pack(fill='x',pady=(0,6))
    transcript_frame = tk.Frame(p.detail,bg=BORDER,padx=1,pady=1)
    transcript_frame.pack(fill='both',expand=True)
    p.transcript = tk.Text(transcript_frame,height=5,bg='#152734',fg=TEXT,insertbackground=TEXT,bd=0,
                            wrap='word',padx=14,pady=10,font=(FONT,11),state='disabled')
    scroll = ttk.Scrollbar(transcript_frame,orient='vertical',command=p.transcript.yview)
    p.transcript.configure(yscrollcommand=scroll.set)
    scroll.pack(side='right',fill='y')
    p.transcript.pack(fill='both',expand=True)
    p.log_path = tk.StringVar(value='日志：'+str(logs_dir()))
    p.log_label = tk.Label(p.detail,textvariable=p.log_path,fg=MUTED,bg=BG,wraplength=880,
                           justify='left',anchor='w',font=(FONT,8))
    p.log_label.pack(fill='x',pady=(6,0))
    tk.Label(p.detail,textvariable=p.state,fg=MUTED,bg=BG,font=(FONT,9),anchor='w').pack(fill='x')

    p.actions = actions = tk.Frame(outer,bg=BG)
    actions.pack(fill='x',pady=(4,7))
    p.start_button = button(actions,'开始监听',p.start)
    p.start_button.configure(bg='#1C665E',activebackground='#257E74',fg=TEXT)
    p.start_button.pack(side='left')
    p.stop_button = button(actions,'停止',p.stop)
    p.stop_button.configure(state='disabled')
    p.stop_button.pack(side='left',padx=5)
    button(actions,'✎ 笔记',p.quick_note).pack(side='left')
    button(actions,'逻辑图',p.open_logic).pack(side='left',padx=5)
    p.expand_button = button(actions,'主界面 ↗',p.toggle_detail)
    p.expand_button.pack(side='right')
    p.speaker_label = tk.Label(outer,textvariable=p.speaker_status,fg=MUTED,bg=BG,wraplength=500,
                               justify='left',anchor='w',font=(FONT,9))
    p.speaker_label.pack(fill='x')
    p.error = tk.StringVar(value=p.preferences.error)
    p.error_label = tk.Label(outer,textvariable=p.error,fg=AMBER,bg=BG,wraplength=500,
                             justify='left',anchor='w',font=(FONT,10))
    p.error_label.pack(fill='x',pady=(2,0))
    p.desktop_notice = tk.StringVar()
    p.notice_label = tk.Label(outer,textvariable=p.desktop_notice,fg=AMBER,bg=BG,wraplength=500,
                              justify='left',anchor='w',font=(FONT,9))
    p.notice_label.pack(fill='x')
    p.desktop_problems = {}
    # Reserve the controls and status footer before allocating expandable body
    # space. Otherwise Tk may clip Stop / Notes on a compact or resized window.
    for widget in (p.body,actions,p.speaker_label,p.error_label,p.notice_label):
        widget.pack_forget()
    for widget in (p.notice_label,p.error_label,p.speaker_label,actions):
        widget.pack(side='bottom',fill='x',pady=(3,0))
    p.body.pack(side='top',fill='both',expand=True)

    p.bar_frame = tk.Frame(p.shell,bg=BG,padx=14,pady=12)
    if hasattr(p,'logo_image'):
        tk.Label(p.bar_frame,image=p.logo_image,bg='#E8F4F2').pack(side='left',padx=(0,10))
    p.bar_text = tk.StringVar(value='哨兵 · 待机')
    p.bar_label = tk.Label(p.bar_frame,textvariable=p.bar_text,font=(FONT,12,'bold'),bg=BG,fg=TEXT,anchor='w')
    p.bar_label.pack(side='left',fill='x',expand=True)
    p.bar_label.bind('<ButtonPress-1>',p.drag_start)
    p.bar_label.bind('<B1-Motion>',p.drag_move)
    p.bar_label.bind('<Double-Button-1>',lambda event:p.set_layout('compact'))
    button(p.bar_frame,'⌃',p.toggle_fold).pack(side='right',padx=(4,0))
    button(p.bar_frame,'↗',lambda:p.set_layout('detail')).pack(side='right',padx=(4,0))
    button(p.bar_frame,'✎',p.quick_note).pack(side='right',padx=(4,0))
    p.mini_listen_button = button(p.bar_frame,'● 关',p.toggle_listening)
    p.mini_listen_button.pack(side='right',padx=(4,0))
    # Reserve controls first; a long headline can only consume the remaining
    # space, never push the microphone / restore buttons out of the status bar.
    p.bar_label.pack_forget()
    p.bar_label.pack(side='left',fill='x',expand=True)
    p.bar_frame.bind('<ButtonPress-1>',p.drag_start)
    p.bar_frame.bind('<B1-Motion>',p.drag_move)
    p.resize_grip = tk.Label(p.shell,text='⋰',bg=BG,fg=MUTED,cursor='size_nw_se',font=(FONT,11))
    p.resize_grip.place(relx=1,rely=1,anchor='se',x=-3,y=-2)
    p.resize_grip.bind('<ButtonPress-1>',p.resize_start)
    p.resize_grip.bind('<B1-Motion>',p.resize_move)
    p._chrome_job = None
    p._fit_job = None
    p._view_snapshot = None
    root.bind('<Map>',lambda event:p.schedule_chrome() if event.widget is root else None,add='+')


def refresh_view(p):
    snapshot = (p.phase,p.mic_on,p.controller.busy,p.closing,p.error.get(),p.desktop_notice.get(),
                p.headline.cget('text'),p.evidence.cget('text'),p.action.cget('text'),len(p.turns),p.speaker_status.get())
    if snapshot == p._view_snapshot:
        return
    p._view_snapshot = snapshot
    brief = ' '.join((p.error.get() or p.headline.cget('text')).split())
    p.bar_text.set('哨兵 · '+(brief[:19]+'…' if len(brief)>19 else brief))
    p.bar_label.configure(fg=AMBER if p.error.get() else TEXT)
    p.mini_listen_button.configure(text='● 麦克风开' if p.mic_on else '○ 麦克风关',
                                  fg=GREEN if p.mic_on else MUTED,
                                  state='disabled' if p.closing or (p.controller.busy and p.controller.stop_event.is_set()) else 'normal')
    p.mic_label.configure(fg=GREEN if p.mic_on else MUTED)
    p.session_summary.set(f'{len(p.turns)} 个确认片段 · 私人笔记单独保存')
    p.schedule_fit()
    # Empty footer widgets should not consume valuable space in the floating view.
    for widget,text in ((p.error_label,p.error.get()),(p.notice_label,p.desktop_notice.get())):
        if text and not widget.winfo_manager():widget.pack(side='bottom',fill='x',pady=(2,0),before=p.actions)
        elif not text:widget.pack_forget()


def apply_layout(p, mode):
    root = p.root
    floating = mode != 'detail'
    root.overrideredirect(floating)
    p.outer.pack_forget()
    p.bar_frame.pack_forget()
    if mode == 'bar':
        p.body.pack_forget()
        p.bar_frame.pack(fill='both',expand=True)
        root.minsize(500,66)
        root.geometry('560x72')
    else:
        p.outer.pack(fill='both',expand=True)
        p.body.pack(side='top',fill='both',expand=True)
        if mode == 'detail':
            p.goal_summary.pack_forget()
            p.goal_editor.pack(fill='x',before=p.card_shell,pady=(0,10))
            p.scene_controls.pack(fill='x',before=p.card_shell,pady=(0,12))
            p.detail.pack(fill='both',expand=True,pady=(14,0))
            p.card_shell.configure(height=260)
            p.card_shell.pack_configure(expand=False)
            p.brand.configure(font=(FONT,25,'bold'))
            p.headline.configure(font=(FONT,29,'bold'))
            p.evidence.configure(font=(FONT,12))
            p.action.configure(font=(FONT,15,'bold'))
            p.expand_button.configure(text='悬浮窗 ↗')
            p.fold_button.configure(text='迷你条')
            root.minsize(720,680)
            root.geometry(f'{min(980,root.winfo_screenwidth()-80)}x{min(850,root.winfo_screenheight()-100)}')
        else:
            p.goal_editor.pack_forget()
            p.scene_controls.pack_forget()
            p.detail.pack_forget()
            p.goal_summary.pack(fill='x',before=p.card_shell,pady=(0,8))
            p.card_shell.pack_configure(expand=True)
            p.brand.configure(font=(FONT,17,'bold'))
            p.headline.configure(font=(FONT,21,'bold'))
            p.evidence.configure(font=(FONT,10))
            p.action.configure(font=(FONT,12,'bold'))
            p.expand_button.configure(text='主界面 ↗')
            p.fold_button.configure(text='﹣')
            root.minsize(500,420)
            root.geometry('560x440')
    p.schedule_chrome()
    p.schedule_fit()
