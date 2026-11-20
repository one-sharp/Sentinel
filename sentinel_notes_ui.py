"""Private keyboard notes; no calls to the speech runner or LLM observer."""
import logging
import tkinter as tk
from tkinter import ttk

from sentinel_desktop import NOTE_KINDS
from sentinel_theme import BG, CARD, TEXT, MUTED, ACCENT
from sentinel_scroll import attach_scrollbars


class NoteComposer:
    def __init__(self, root, store, context, on_error, *, page_factory=None):
        self.root, self.store, self.context_provider, self.on_error = root, store, context, on_error
        self.page_factory = page_factory
        self.window = None
        self.context = None
        self.last_written = None
        self.focus_after = None

    def open(self):
        if self.window is None or not self.window.winfo_exists():
            self.window = self.page_factory('私人笔记') if self.page_factory else tk.Toplevel(self.root)
            if not self.page_factory:
                self.window.attributes('-topmost',bool(self.root.attributes('-topmost')))
                self.window.title("Sentinel 哨兵 · 私人快速笔记")
                self.window.geometry("580x500")
                self.window.minsize(420, 400)
                self.window.configure(bg=BG)
                self.window.protocol("WM_DELETE_WINDOW", self.close)
            self.window.bind("<Destroy>", lambda event: self.cancel_focus() if event.widget is self.window else None)
            box = ttk.Frame(self.window, padding=16)
            box.pack(fill="both", expand=True)
            ttk.Label(box, text="只写给自己", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
            ttk.Label(box, text="私人输入 · 不算作已说出口 · 不自动发送给 AI", foreground=MUTED).pack(anchor="w", pady=(4, 12))
            self.kind = tk.StringVar(value=NOTE_KINDS[0])
            ttk.Combobox(box, textvariable=self.kind, values=NOTE_KINDS, state="readonly", width=12).pack(anchor="w")
            self.session_label = tk.StringVar()
            ttk.Label(box, textvariable=self.session_label, foreground=MUTED, wraplength=520).pack(anchor="w", pady=8)
            frame = ttk.Frame(box)
            frame.pack(fill="both", expand=True)
            self.editor = tk.Text(frame, wrap="word", undo=True, height=7, font=("Microsoft YaHei UI", 11),
                                  bg=CARD, fg=TEXT, insertbackground=ACCENT, bd=0, padx=10, pady=10)
            attach_scrollbars(self.editor)
            self.editor.pack(fill="both", expand=True)
            self.editor.bind("<Control-Return>", lambda event: (self.save(), "break")[-1])
            footer = ttk.Frame(box)
            frame.pack_forget()
            footer.pack(side='bottom',fill='x')
            frame.pack(fill='both',expand=True)
            row = ttk.Frame(footer)
            row.pack(fill="x", pady=10)
            self.save_button = ttk.Button(row, text="保存并新建  Ctrl+Enter", command=self.save)
            self.save_button.pack(side="left")
            ttk.Button(row, text="新笔记", command=self.new).pack(side="left", padx=8)
            self.status = tk.StringVar(value="支持不完整句子和长文本")
            ttk.Label(footer, textvariable=self.status, wraplength=520).pack(anchor="w")
            ttk.Label(footer, text="最近记录 / 草稿（选择后可另存新条）").pack(anchor="w", pady=(10, 4))
            self.history = ttk.Combobox(footer, state="readonly")
            self.history.pack(fill="x")
            self.history.bind("<<ComboboxSelected>>", self.restore)
            self._new()
            if self.page_factory:
                self.window.before_leave = self.save_draft
        self.refresh_history()
        self.window.deiconify()
        self.window.lift()
        self.cancel_focus()
        self.focus_after = self.window.after(50, self.focus)

    def focus(self):
        self.cancel_focus()
        if self.window is not None and self.window.winfo_exists() and self.window.state() != "withdrawn":
            self.window.focus_force()
            self.editor.focus_set()

    def cancel_focus(self):
        if self.focus_after is not None:
            try:
                self.window.after_cancel(self.focus_after)
            except tk.TclError:
                pass
            self.focus_after = None

    def _new(self):
        self.context = dict(self.context_provider())
        self.editor.delete("1.0", "end")
        self.last_written = None
        self.session_label.set("所属场次：" + self.context["session_id"])

    def new(self):
        try:
            self.save_draft()
            self._new()
            self.focus()
        except Exception as exc:
            self._error(exc)

    def _snapshot(self):
        return self.editor.get("1.0", "end-1c"), self.kind.get(), self.context["session_id"]

    def save(self):
        try:
            text, kind, _ = self._snapshot()
            record = self.store.save(text, kind, **self.context)
            self._new()
            self.refresh_history()
            self.status.set("已本地保存 · " + record["created_at"])
            self.focus()
            return record
        except Exception as exc:
            self._error(exc)
            return None

    def save_draft(self):
        if self.window is None or not self.window.winfo_exists():
            return
        snapshot = self._snapshot()
        if snapshot[0].strip() and snapshot != self.last_written:
            self.store.save(snapshot[0], snapshot[1], **self.context, draft=True)
            self.last_written = snapshot
            self.status.set("草稿已本地保存")

    def refresh_history(self):
        self.records = self.store.read()[-30:][::-1]
        self.history.configure(values=[f"{'草稿' if r.get('draft') else '笔记'} · {r['created_at'][5:19]} · {r['note_kind']} · {r['text'][:22].replace(chr(10), ' ')}"
                                       for r in self.records])

    def restore(self, event=None):
        try:
            index = self.history.current()
            if index < 0:
                return
            record = self.records[index]
            self.save_draft()
            self.context = {"session_id":record["session_id"], "asr_log":record.get("asr_log")}
            self.editor.delete("1.0", "end")
            self.editor.insert("1.0", record["text"])
            self.kind.set(record["note_kind"])
            self.session_label.set("历史所属场次：" + record["session_id"])
            self.last_written = self._snapshot()
            self.status.set("已读取本地记录；保存将另存新条")
            self.focus()
        except Exception as exc:
            self._error(exc)

    def close(self):
        try:
            self.cancel_focus()
            self.save_draft()
            self.window.withdraw()
        except Exception as exc:
            self._error(exc)

    def _error(self, exc):
        logging.exception("Private note operation failed")
        self.status.set("未保存：" + str(exc))
        self.on_error("私人笔记未保存：" + str(exc))
