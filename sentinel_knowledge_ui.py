"""Detachable knowledge-pack selection. This window never invokes retrieval."""
import tkinter as tk
from tkinter import filedialog

from sentinel_theme import BG, CARD, TEXT, MUTED, ACCENT, BORDER, BUTTON, FONT
from sentinel_scroll import attach_scrollbars


class KnowledgeWindow:
    def __init__(self, root, catalog, on_change=lambda: None, on_error=lambda text: None, *, page_factory=None):
        self.root, self.catalog = root, catalog
        self.on_change, self.on_error = on_change, on_error
        self.page_factory = page_factory
        self.window = None
        self.variables = {}

    def open(self):
        if self.window is None or not self.window.winfo_exists():
            window = self.window = self.page_factory('知识库选择') if self.page_factory else tk.Toplevel(self.root)
            if not self.page_factory:
                window.title('Sentinel · 知识库选择')
                window.geometry('620x470')
                window.minsize(480, 370)
                window.configure(bg=BG)
                window.attributes('-topmost', bool(self.root.attributes('-topmost')))
                window.protocol('WM_DELETE_WINDOW', window.withdraw)
            outer = tk.Frame(window, bg=BG, padx=20, pady=18)
            outer.pack(fill='both', expand=True)
            tk.Label(outer, text='选择本场知识库', font=(FONT, 12, 'bold'),
                     bg=BG, fg=TEXT).pack(anchor='w')
            tk.Label(outer, text='可多选、取消选择或移除；移除不会删除原始资料。',
                     font=(FONT, 9), bg=BG, fg=MUTED).pack(anchor='w', pady=(5, 12))
            footer = tk.Frame(outer, bg=BG)
            footer.pack(side='bottom', fill='x', pady=(12, 0))
            self.status = tk.StringVar()
            tk.Label(footer, textvariable=self.status, bg=BG, fg=ACCENT,
                     font=(FONT, 9), wraplength=550, justify='left').pack(anchor='w')
            tk.Label(footer, text='仅保存知识库选择，检索将在后续接入；\n当前分析不读取这些文件。',
                     bg=BG, fg=MUTED, font=(FONT, 9), justify='left').pack(anchor='w', pady=(7, 10))
            tk.Button(footer, text='＋ 添加本地目录', command=self.add_directory, bg=ACCENT,
                      fg=CARD, activebackground=ACCENT, activeforeground=CARD,
                      font=(FONT, 9), relief='flat', bd=0, padx=12, pady=7).pack(anchor='w')
            listing = tk.Frame(outer, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
            listing.pack(fill='both', expand=True)
            self.canvas = tk.Canvas(listing, bg=CARD, highlightthickness=0, bd=0)
            attach_scrollbars(self.canvas, wheel=True)
            self.canvas.pack(side='left', fill='both', expand=True)
            self.rows = tk.Frame(self.canvas, bg=CARD)
            self.rows_item = self.canvas.create_window(0, 0, anchor='nw', window=self.rows)
            self.rows.bind('<Configure>', lambda event: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
            self.canvas.bind('<Configure>', lambda event: self.canvas.itemconfigure(self.rows_item, width=event.width))
        self.render()
        self.window.deiconify()
        self.window.lift()

    def render(self):
        for child in self.rows.winfo_children():
            child.destroy()
        self.variables.clear()
        self.status.set(self.catalog.error or self.catalog.summary())
        packs = self.catalog.packs
        if not packs:
            tk.Label(self.rows, text='还没有知识库\n\n添加一个本地目录，为后续检索保留选择。',
                     bg=CARD, fg=MUTED, font=(FONT, 10), justify='center', pady=36).pack(fill='x')
        for pack in packs:
            row = tk.Frame(self.rows, bg=CARD, padx=12, pady=9)
            row.pack(fill='x')
            variable = self.variables[pack['pack_id']] = tk.BooleanVar(value=pack['enabled'])
            tk.Button(row, text='移除', command=lambda ident=pack['pack_id']: self.remove(ident),
                      bg=BUTTON, fg=MUTED, activebackground=BUTTON, relief='flat', bd=0,
                      font=(FONT, 9), padx=8, pady=5).pack(side='right', anchor='n')
            tk.Checkbutton(row, text=pack['name'], variable=variable,
                           command=lambda ident=pack['pack_id'], value=variable: self.toggle(ident, value),
                           bg=CARD, fg=TEXT, activebackground=CARD, activeforeground=TEXT,
                           selectcolor=CARD, anchor='w', font=(FONT, 10), bd=0).pack(fill='x')
            tk.Label(row, text=pack['path'], bg=CARD, fg=MUTED, font=(FONT, 8),
                     wraplength=420, anchor='w', justify='left').pack(fill='x', padx=(22, 0), pady=(3, 0))
            tk.Frame(self.rows, bg=BORDER, height=1).pack(fill='x', padx=12)

    def _change(self, operation):
        try:
            operation()
        except (OSError, ValueError, TypeError) as exc:
            self.render()
            self.status.set('未保存：' + str(exc))
            self.on_error(str(exc))
            return
        self.render()
        self.on_change()

    def add_directory(self):
        selected = filedialog.askdirectory(parent=self.root, title='选择知识库目录', mustexist=True)
        if selected:
            self._change(lambda: self.catalog.add_directory(selected))

    def toggle(self, pack_id, variable):
        self._change(lambda: self.catalog.set_enabled(pack_id, variable.get()))

    def remove(self, pack_id):
        self._change(lambda: self.catalog.remove(pack_id))
