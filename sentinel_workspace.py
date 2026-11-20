"""Tools stay inside one workspace; pages own no native windows or audio.

The small show/hide aliases let existing tool renderers migrate from Toplevel
without duplicating their form logic. Native window policy belongs to the root.
"""
import tkinter as tk
from tkinter import ttk

from sentinel_theme import BG, CARD, TEXT, MUTED, FONT
from sentinel_scroll import attach_scrollbars


class WorkspacePages:
    def __init__(self, panel):
        self.panel = panel
        self.active = None
        self.pages = []

    def create(self, title, *, scroll=True, immersive=False):
        page = WorkspacePage(self, title, scroll=scroll, immersive=immersive)
        self.pages.append(page)
        return page.content

    def show(self, page):
        if self.panel.layout_mode in ('bar', 'meeting'):
            self.panel.set_layout('compact')
        if not self.hide():
            return
        self.active = page
        if hasattr(self.panel, 'preparation') and self.panel.preparation.winfo_manager():
            self.panel.preparation.pack_forget()
            (self.panel.detail if self.panel.layout_mode == 'detail' else self.panel.workspace).pack(fill='both', expand=True)
        self.focus_chrome(page.title)
        page.place(x=0, y=0, relwidth=1, relheight=1)
        page.lift()
        self.panel.show_window()

    def hide(self):
        if self.active is not None:
            try:
                self.active.content.before_leave()
            except Exception as exc:
                self.panel.show_error('页面切换取消，草稿未保存：' + str(exc))
                return False
            focused = self.panel.root.focus_get()
            ancestor = focused
            while ancestor is not None:
                if ancestor is self.active:
                    self.panel.root.focus_set()
                    break
                ancestor = getattr(ancestor, 'master', None)
            self.active.place_forget()
            self.active = None
            self.focus_chrome(None)
        return True

    def focus_chrome(self, title):
        # Standalone renderer tests can provide a minimal host without chrome.
        if hasattr(self.panel, 'tagline'):
            from sentinel_ui import set_page_focus
            set_page_focus(self.panel, title)


class PageContent(tk.Frame):
    """Tool renderer surface with explicit in-workspace visibility semantics."""
    def __init__(self, page, parent):
        super().__init__(parent, bg=BG)
        self.page = page
        self.before_leave = lambda: None

    def deiconify(self):
        self.page.host.show(self.page)

    def withdraw(self):
        if self.page.host.active is self.page:
            self.page.host.hide()

    def state(self):
        return 'normal' if self.page.host.active is self.page else 'withdrawn'

    def lift(self, aboveThis=None):
        self.page.lift()


class WorkspacePage(tk.Frame):
    def __init__(self, host, title, *, scroll, immersive=False):
        super().__init__(host.panel.body, bg=BG)
        self.host = host
        self.title, self.immersive = title, immersive
        header = tk.Frame(self, bg=BG)
        if not immersive:
            header.pack(fill='x', pady=(0, 8))
        ttk.Button(header, text='‹ 返回工作台', command=host.hide).pack(side='left')
        tk.Label(header, text=title, bg=BG, fg=TEXT, font=(FONT, 11, 'bold')).pack(side='left', padx=14)
        if scroll:
            canvas = tk.Canvas(self, bg=BG, bd=0, highlightthickness=0)
            attach_scrollbars(canvas, wheel=True)
            canvas.pack(fill='both', expand=True)
            self.content = PageContent(self, canvas)
            item = canvas.create_window(0, 0, anchor='nw', window=self.content)
            self.content.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
            canvas.bind('<Configure>', lambda e: canvas.itemconfigure(item, width=e.width))
        else:
            self.content = PageContent(self, self)
            self.content.pack(fill='both', expand=True)
