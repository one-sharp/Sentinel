"""Tk desktop controls; microphone/model work stays off the Tk thread."""
from __future__ import annotations

import copy
import logging
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox

from sentinel_ui import BG, CARD, TEXT, MUTED, GREEN, AMBER
SCENE_LABELS = {"合同谈判": "negotiation", "会议": "meeting", "纠纷": "dispute", "通用": "general"}


class SessionController:
    """Own one complete ASR + LLM session until its worker has actually exited."""

    def __init__(self, args, runner):
        self.args, self.runner = args, runner
        self.events = queue.Queue()
        self.thread = None
        self.stop_event = None
        self.session_id = None

    @property
    def busy(self):
        return self.thread is not None

    def start(self, scene, enrollment=False):
        if self.busy:
            return False
        args = copy.copy(self.args)
        from sentinel_desktop import new_session_id
        self.session_id = new_session_id()
        args.scene = scene
        args.enroll_only = enrollment
        args.controls = queue.Queue()
        self.controls = args.controls
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._worker, args=(args, self.stop_event),
                                       daemon=False, name="sentinel-asr")
        try:
            self.thread.start()
        except Exception:
            self.thread = None
            raise
        return True

    def _worker(self, args, stop_event):
        try:
            self.runner(args, on_event=lambda **evt: self.events.put(evt), stop_event=stop_event)
        except Exception as exc:
            logging.exception("Sentinel session failed")
            self.events.put({"kind": "fatal", "text": str(exc)})

    def stop(self):
        if self.stop_event is not None:
            self.stop_event.set()

    def send(self, **command):
        if self.busy:
            self.controls.put(command)

    def reap(self):
        # Drain callbacks before allowing a new session to receive events.
        if self.thread is not None and not self.thread.is_alive() and self.events.empty():
            self.thread.join()
            self.thread = None
            return True
        return False


class SentinelPanel:
    def __init__(self, root, args, runner, *, preferences=None, note_store=None):
        self.root = root
        self.controller = SessionController(args, runner)
        self.closing = False
        self.error_text = ""
        self.enrolling = False
        self.enrollment_saved = False
        self.turns = {}
        self.speaker_roles = {"unknown": "未绑定"}
        self.settings = None
        self.speakers = tk.BooleanVar(value=getattr(args, "speakers", False))
        self.clip_consent = tk.BooleanVar(value=False)
        self.profile_consent = tk.BooleanVar(value=False)
        self.speaker_status = tk.StringVar(value="已选本地说话人区分 · 点击开始后加载" if self.speakers.get()
                                            else "未启用声纹；可打开说话人设置")
        self.mode = "监听 + LLM 分析" if args.llm else "仅语音转写"
        from sentinel_desktop import DesktopPreferences, NoteStore, ROOT, new_session_id
        from sentinel_notes_ui import NoteComposer
        from sentinel_paths import ASSETS_DIR, user_data_dir, logs_dir, FROZEN
        from sentinel_config import api_settings, needs_api_setup, DEFAULTS
        self.preferences = preferences or DesktopPreferences()
        self.note_store = note_store or NoteStore()
        self.desktop = None
        self.desktop_events = queue.Queue()
        self.native_shutdown = False
        self.hidden = False
        self.layout_mode = "compact"
        self.previous_mode = "compact"
        self.phase = "待机"
        self.mic_on = False
        self.status_cache = None
        self.standby_id = new_session_id() + "-standby"
        self.current_log = None
        self.desktop_settings_window = None
        self.topmost = tk.BooleanVar(value=self.preferences.value["topmost"])
        self.goal = tk.StringVar(value=getattr(args, "goal", ""))
        if not self.goal.get():
            try:
                self.goal.set(api_settings().get("EVENT_GOAL") or "")
            except (OSError, ValueError):
                self.goal.set(DEFAULTS["EVENT_GOAL"])
        self.api_window = None
        self.api_check_thread = None
        self.notes = NoteComposer(root, self.note_store, self.note_context, self.show_error)
        self.logic_document = None
        self.logic_service = None
        self.logic_window = None
        self.logic_workers = []
        root.title("Sentinel 哨兵 v0.3.1")
        root.report_callback_exception = self.callback_error
        from sentinel_ui import build_view
        build_view(self, args, ASSETS_DIR, logs_dir, SCENE_LABELS)
        root.bind("<Configure>", self.resize_layout)
        root.bind("<Destroy>", self.cancel_poll, add="+")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._poll_id = root.after(80, self.poll)
        if FROZEN and needs_api_setup():
            root.after(250, self.open_api_settings)

    @staticmethod
    def make_button(parent, text, command):
        from sentinel_ui import button
        return button(parent, text, command)

    def enable_native(self, hotkeys=None):
        from sentinel_windows import WindowsDesktop
        self.desktop = WindowsDesktop(lambda **event: self.desktop_events.put(event),
                                      hotkeys={} if hotkeys is None else hotkeys)
        self.desktop.start()
        self.sync_desktop()

    def note_context(self):
        return {"session_id": self.controller.session_id or self.standby_id, "asr_log": self.current_log}

    def quick_note(self):
        if not self.closing:
            self.notes.open()

    def open_logic(self):
        if self.closing:return
        from sentinel_logic import GraphDocument
        from sentinel_logic_ui import LogicGraphWindow
        if self.logic_document is None:
            self.logic_document=GraphDocument(self.controller.session_id or self.standby_id,self.goal.get(),self.scene.get())
        if self.logic_window is None:self.logic_window=LogicGraphWindow(self)
        self.logic_window.open(self.logic_document)
        self.enable_logic_analysis()

    def enable_logic_analysis(self):
        if self.closing or self.logic_document is None:return
        if self.logic_service is None or not self.logic_service.thread.is_alive():
            from sentinel_logic import GraphWorker
            self.logic_service=GraphWorker(self.logic_document,lambda **e:self.desktop_events.put(e))
            self.logic_workers.append(self.logic_service)
        self.logic_service.notify()

    def stop_logic_services(self):
        for service in self.logic_workers:service.stop()

    def drag_start(self, event):
        self.drag_origin = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def drag_move(self, event):
        x, y, left, top = self.drag_origin
        self.root.geometry(f"+{left+event.x_root-x}+{top+event.y_root-y}")

    def resize_layout(self, event):
        if event.widget is self.root:
            width = max(280, event.width-105)
            for label in (self.headline, self.evidence, self.action, self.error_label, self.speaker_label):
                label.configure(wraplength=width)
            for label in (self.goal_summary,self.latest_label,self.log_label,self.notice_label):
                label.configure(wraplength=max(280,event.width-60))
            self.schedule_chrome()
            self.schedule_fit()

    def schedule_fit(self):
        if self._fit_job is None:
            self._fit_job = self.root.after(40,self.fit_content)

    def fit_content(self):
        self._fit_job = None
        if self.layout_mode=='bar' or not self.root.winfo_ismapped():
            return
        def padding(widget):
            value = widget.pack_info().get('pady',0)
            values = value if isinstance(value,(tuple,list)) else widget.tk.splitlist(str(value))
            return int(values[0])*2 if len(values)==1 else sum(map(int,values))
        # Requested label sizes are stable before the first native resize.
        # Measuring the Canvas's current height here can see an initial 1px
        # placeholder and incorrectly grow the floating window on startup.
        needed = 38+sum(widget.winfo_reqheight()+padding(widget)
                        for widget in self.card_shell.inner.pack_slaves())
        if self.layout_mode=='detail':
            self.card_shell.configure(height=max(260,needed))
        else:
            fixed = 26+sum(widget.winfo_reqheight()+padding(widget)
                           for widget in self.outer.pack_slaves() if widget is not self.body)
            fixed += sum(widget.winfo_reqheight()+padding(widget)
                         for widget in self.body.pack_slaves() if widget is not self.card_shell)
            fixed += padding(self.card_shell)
            minimum = min(self.root.winfo_screenheight()-80,max(420,fixed+needed+4))
            self.root.minsize(500,minimum)
            if self.root.winfo_height()<minimum:
                self.root.geometry(f'{self.root.winfo_width()}x{minimum}')

    def schedule_chrome(self):
        if self._chrome_job is None:
            self._chrome_job = self.root.after_idle(self.update_chrome)

    def update_chrome(self):
        self._chrome_job = None
        key = (self.root.winfo_width(),self.root.winfo_height(),self.layout_mode,self.root.overrideredirect())
        if key != getattr(self,'_chrome_size',None):
            self._chrome_size = key
            from sentinel_ui import apply_chrome
            apply_chrome(self.root,self.layout_mode!='detail')

    def resize_start(self, event):
        self.resize_origin = (event.x_root,event.y_root,self.root.winfo_width(),self.root.winfo_height())

    def resize_move(self, event):
        x,y,width,height = self.resize_origin
        minimum_width,minimum_height = self.root.minsize()
        self.root.geometry(f'{max(minimum_width,width+event.x_root-x)}x{max(minimum_height,height+event.y_root-y)}')

    def cancel_poll(self, event):
        if event.widget is self.root:self.stop_logic_services()
        if event.widget is self.root and getattr(self,'_fit_job',None):
            self.root.after_cancel(self._fit_job)
            self._fit_job = None
        if event.widget is self.root and getattr(self,'_chrome_job',None):
            self.root.after_cancel(self._chrome_job)
            self._chrome_job = None
        if event.widget is self.root and getattr(self, "_poll_id", None):
            try:
                self.root.after_cancel(self._poll_id)
            except tk.TclError:
                pass
            self._poll_id = None

    def toggle_pin(self):
        self.topmost.set(not self.topmost.get())
        self.apply_pin()

    def apply_pin(self):
        self.root.attributes("-topmost", self.topmost.get())
        self.pin_button.configure(text="置顶 ●" if self.topmost.get() else "置顶")
        for window in (self.settings,self.desktop_settings_window,self.api_window,self.notes.window,
                       self.logic_window.window if self.logic_window else None):
            if window is not None and window.winfo_exists():
                window.attributes('-topmost',self.topmost.get())
        try:
            self.preferences.save(topmost=self.topmost.get())
        except Exception as exc:
            self.show_error("置顶设置未保存：" + str(exc))

    def set_layout(self, mode):
        if mode not in ("bar", "compact", "detail"):
            raise ValueError("未知窗口模式")
        self.layout_mode = mode
        from sentinel_ui import apply_layout
        apply_layout(self, mode)

    def toggle_fold(self):
        if self.layout_mode == "bar":
            self.set_layout(self.previous_mode)
        else:
            self.previous_mode = self.layout_mode
            self.set_layout("bar")

    def toggle_detail(self):
        self.set_layout("compact" if self.layout_mode == "detail" else "detail")

    def show_window(self):
        self.hidden = False
        self.root.overrideredirect(self.layout_mode!='detail')
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide_window(self):
        try:
            self.notes.save_draft()
            self.notes.cancel_focus()
        except Exception as exc:
            self.show_error("隐藏取消，笔记草稿未保存：" + str(exc))
            return
        if not self.desktop or not self.desktop.tray_available:
            self.desktop_notice.set("托盘不可用：已最小化到任务栏，监听状态保持。可从任务栏恢复。")
            self.root.overrideredirect(False)
            self.root.iconify()
            return
        for window in (self.settings, self.desktop_settings_window, self.notes.window, self.api_window,
                       self.logic_window.window if self.logic_window else None):
            if window is not None and window.winfo_exists():
                window.withdraw()
        self.hidden = True
        self.root.withdraw()

    def toggle_window(self):
        if self.hidden or self.root.state() in ("withdrawn", "iconic"):
            self.show_window()
        else:
            self.hide_window()

    def toggle_listening(self):
        if self.closing:
            return
        if self.controller.busy:
            if not self.controller.stop_event.is_set():
                self.stop()
        else:
            self.start()

    def set_phase(self, phase, mic_on=None):
        self.phase = phase
        if mic_on is not None:
            self.mic_on = mic_on
        self.phase_text.set(phase)
        self.phase_label.configure(fg=AMBER if phase == "错误" else TEXT)
        self.mic_text.set("● 麦克风开启" if self.mic_on else "● 麦克风关闭")
        self.mic_label.configure(fg="#BC3328" if self.mic_on else MUTED)
        self.sync_desktop()

    def sync_desktop(self):
        status = (self.phase, self.mic_on, self.controller.busy)
        if self.desktop and status != self.status_cache:
            self.desktop.update_status(*status)
            self.status_cache = status

    def desktop_action(self, action):
        actions = {"show_window":self.show_window, "toggle_window":self.toggle_window,
                   "quick_note":self.quick_note, "toggle_listening":self.toggle_listening,
                   "toggle_fold":self.toggle_fold, "exit":self.request_exit}
        if action in actions and not self.closing:
            actions[action]()

    def handle_desktop(self, event):
        kind = event["kind"]
        if kind == 'logic_update':
            if self.logic_window:self.logic_window.refresh()
        elif kind == "desktop_action":
            self.desktop_action(event["action"])
        elif kind == "hotkeys_applied":
            # No global bindings are requested by the desktop application.
            self.hotkey_result = event
        elif kind == "tray_ready":
            if not event["available"]:
                self.desktop_problems["tray"] = "系统托盘注册失败，关闭时保留任务栏入口。"
            else:
                self.desktop_problems.pop("tray", None)
        elif kind == "desktop_error":
            self.desktop_problems["service"] = event["text"]
            if self.hidden:
                self.show_window()
        elif kind == "desktop_closed" and not self.closing:
            self.desktop_problems["service"] = "托盘服务已结束，请保存后退出并重新打开应用。"
            if self.hidden:
                self.show_window()
        elif kind == "api_check_result":
            if hasattr(self, "api_status"):
                self.api_status.set(event["text"])
        notice = []
        if "hotkeys" in self.desktop_problems:
            notice.append("快捷键未全部生效，请打开设置查看冲突和当前绑定。")
        notice.extend(v[:110] for k,v in self.desktop_problems.items() if k != "hotkeys")
        self.desktop_notice.set("\n".join(notice))

    def open_desktop_settings(self):
        if self.desktop_settings_window is not None and self.desktop_settings_window.winfo_exists():
            self.desktop_settings_window.deiconify()
            self.desktop_settings_window.lift()
            return
        window = tk.Toplevel(self.root)
        self.desktop_settings_window = window
        window.title("Sentinel 哨兵 · 设置")
        window.transient(self.root)
        window.attributes('-topmost',self.topmost.get())
        window.geometry(f"610x460+{self.root.winfo_x()+30}+{self.root.winfo_y()+30}")
        window.minsize(610,460)
        box = ttk.Frame(window, padding=18)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text="桌面与悬浮窗", font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
        ttk.Checkbutton(box, text="悬浮窗保持置顶", variable=self.topmost, command=self.apply_pin).pack(anchor="w", pady=12)
        ttk.Button(box, text="API 配置（Key / 模型 / 连接测试）", command=self.open_api_settings).pack(anchor="w", pady=(0, 8))
        ttk.Button(box, text="说话人角色 / 本人声纹录入、重录、删除", command=self.open_speakers).pack(anchor="w")
        ttk.Label(box, text="用窗口按钮切换视图；关闭窗口收起到托盘。", wraplength=550).pack(anchor="w", pady=14)
        views = ttk.Frame(box)
        views.pack(fill="x")
        for label, mode in (("主界面", "detail"), ("悬浮卡片", "compact"), ("迷你状态条", "bar")):
            ttk.Button(views, text=label, command=lambda m=mode:self.set_layout(m)).pack(side="left", padx=(0,8))
        ttk.Label(box, text="托盘红点 / “麦克风开启”表示仍在采集。隐藏窗口不会停止监听。\n私人笔记保存在 private_notes，独立于语音日志，不发送给 LLM。",
                  wraplength=550).pack(anchor="w", pady=16)
        self.desktop_exit_button = ttk.Button(box, text="退出 Sentinel（停止麦克风并保存）", command=self.request_exit)
        self.desktop_exit_button.pack(anchor="w")

    def open_api_settings(self):
        from sentinel_config import api_settings, DEFAULTS, config_path
        if self.api_window is not None and self.api_window.winfo_exists():
            self.api_window.deiconify()
            self.api_window.lift()
            return
        try:
            values = api_settings()
        except (OSError, ValueError):
            values = dict(DEFAULTS)
        window = tk.Toplevel(self.root)
        self.api_window = window
        window.title("Sentinel · 首次 API 配置")
        window.transient(self.root)
        window.attributes("-topmost", self.topmost.get())
        window.geometry(f"600x500+{self.root.winfo_x()+30}+{self.root.winfo_y()+30}")
        window.minsize(600,500)
        box = ttk.Frame(window, padding=18)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text="配置 DeepSeek / OpenAI 兼容接口", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w", pady=(0, 10))
        self.api_values = {}
        for key, label in (("LLM_API_KEY", "API Key"), ("LLM_BASE_URL", "HTTPS 地址"), ("LLM_MODEL", "模型名称")):
            row = ttk.Frame(box)
            row.pack(fill="x", pady=6)
            ttk.Label(row, text=label, width=12).pack(side="left")
            value = tk.StringVar(value=values[key])
            self.api_values[key] = value
            entry = ttk.Entry(row, textvariable=value, show="●" if key == "LLM_API_KEY" else "")
            entry.pack(side="left", fill="x", expand=True)
        self.api_values["LLM_NETWORK_MODE"] = tk.StringVar(value=values["LLM_NETWORK_MODE"])
        row = ttk.Frame(box)
        row.pack(fill="x", pady=6)
        ttk.Label(row, text="连接方式", width=12).pack(side="left")
        ttk.Combobox(row, textvariable=self.api_values["LLM_NETWORK_MODE"], values=("direct", "system"), state="readonly").pack(side="left")
        ttk.Label(box, text="direct 直连 / system 系统代理。Key 使用当前 Windows 用户加密保存。\n保存后下次开始生效；连接测试仅发送固定短文本，不使用麦克风。", wraplength=550).pack(anchor="w", pady=10)
        row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Button(row, text="保存配置", command=self.save_api).pack(side="left")
        ttk.Button(row, text="保存并测试连接", command=lambda:self.save_api(test=True)).pack(side="left", padx=10)
        self.api_status = tk.StringVar(value="配置位置：" + str(config_path()))
        ttk.Label(box, textvariable=self.api_status, wraplength=550).pack(anchor="w", pady=12)

    def save_api(self, test=False):
        from sentinel_config import save_api_settings
        if self.api_check_thread and self.api_check_thread.is_alive():
            self.api_status.set("连接测试进行中，请稍候。")
            return
        try:
            values = {key:value.get() for key,value in self.api_values.items()}
            values["EVENT_GOAL"] = self.goal.get()
            save_api_settings(values)
            self.api_status.set("已保存。下次开始监听使用新配置。")
            if test:
                def check():
                    try:
                        from sentinel_observer import check_llm_connection
                        check_llm_connection()
                        text = "连接成功，接口返回有效 JSON。"
                    except Exception as exc:
                        text = "连接失败：" + str(exc)[:200]
                    self.desktop_events.put(dict(kind="api_check_result", text=text))
                self.api_status.set("正在测试连接…")
                self.api_check_thread = threading.Thread(target=check, name="sentinel-api-check", daemon=False)
                self.api_check_thread.start()
        except Exception as exc:
            self.api_status.set("未保存：" + str(exc))

    def start(self, enrollment=False):
        if self.closing or self.controller.busy:
            return
        if len(self.goal.get()) > 250:
            self.show_error("当前目标请控制在250字以内。")
            return
        self.controller.args.goal = self.goal.get()
        self.controller.args.speakers = self.speakers.get()
        self.controller.args.save_test_audio = self.clip_consent.get() and not enrollment
        self.controller.args.enrollment_consent = self.profile_consent.get() if enrollment else False
        if not self.controller.start(SCENE_LABELS[self.scene.get()], enrollment=enrollment):
            return
        from sentinel_logic import GraphDocument
        enabled=self.logic_service is not None
        if self.logic_service:self.logic_service.stop()
        self.logic_workers=[s for s in self.logic_workers if s.thread.is_alive()]
        self.logic_document=GraphDocument(self.controller.session_id,self.goal.get(),self.scene.get())
        self.logic_service=None
        if self.logic_window and not self.logic_window.historical:
            if self.logic_window.window.winfo_exists() and self.logic_window.window.state()!='withdrawn':
                self.logic_window.open(self.logic_document)
            else:
                self.logic_window.doc=self.logic_document
                self.logic_window.signature=None
        if enabled and not enrollment:self.enable_logic_analysis()
        self.error_text = ""
        self.error.set("")
        self.current_log = None
        self.goal_entry.configure(state="disabled")
        self.set_phase("正在启动", mic_on=False)
        self.enrolling = enrollment
        self.enrollment_saved = False
        self.turns.clear()
        self.speaker_roles = {"unknown": "未绑定"}
        self.state.set("正在启动 · 加载模型")
        self.latest.set("正在准备麦克风…")
        self.log_path.set("正在创建会话日志…")
        self.headline.configure(text="正在启动监听", fg=TEXT)
        self.evidence.configure(text="一旦发现条款、承诺或目标变化，会显示在这里。")
        self.action.configure(text="继续听，等待值得提醒的事件。")
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        self.transcript.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.scene_box.configure(state="disabled")

    def stop(self):
        if not self.controller.busy:
            return
        self.controller.stop()
        self.set_phase("停止中")
        self.state.set("正在关闭 · 清理资源和保存日志" if self.closing else "正在停止 · 清理资源和保存日志")
        self.stop_button.configure(state="disabled")

    def show_error(self, text):
        self.error_text = text
        self.error.set("错误：" + text[:400])
        self.set_phase("错误")

    def callback_error(self, exc_type, exc, tb):
        logging.error("Sentinel UI callback failed", exc_info=(exc_type, exc, tb))
        self.show_error(str(exc))
        self.stop()

    def open_speakers(self):
        from sentinel_speakers import PROFILE, ROLES
        if self.settings is not None and self.settings.winfo_exists():
            self.settings.deiconify()
            self.settings.lift()
            return
        window = tk.Toplevel(self.root)
        self.settings = window
        window.title("Sentinel — 本地说话人设置")
        window.transient(self.root)
        window.attributes('-topmost',self.topmost.get())
        window.geometry("700x620")
        box = ttk.Frame(window, padding=16)
        box.pack(fill="both", expand=True)
        ttk.Checkbutton(box, text="下一次监听启用本地说话人区分", variable=self.speakers).pack(anchor="w")
        ttk.Label(box, text="先支持单独轮流讲话；短句、打断和混合声可能显示未知。", wraplength=650).pack(anchor="w", pady=8)
        self.profile_status = tk.StringVar(value="已有本人声纹" if PROFILE.exists() else "尚未录入本人声纹")
        ttk.Label(box, textvariable=self.profile_status).pack(anchor="w")
        ttk.Checkbutton(box, text="我同意在本机保存我的声纹特征；录入时仅我一人讲话", variable=self.profile_consent).pack(anchor="w", pady=6)
        row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Button(row, text="录入 / 重录我的声音", command=self.enroll).pack(side="left")
        ttk.Button(row, text="完成录入（至少10秒）", command=self.finish_enrollment).pack(side="left", padx=8)
        ttk.Button(row, text="删除我的声纹", command=self.delete_profile).pack(side="left")
        ttk.Label(box, text="建议15秒有效语音，可分多段；达到目标自动结束。停止监听可取消未完成录入。重录成功前保留旧档案。", wraplength=650).pack(anchor="w", pady=8)
        ttk.Separator(box).pack(fill="x", pady=8)
        ttk.Label(box, text="本会话匿名 ID：A 为已录入本人，其他人使用 B、C 等；未绑定不代表对方。").pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(fill="x", pady=8)
        self.actor = tk.StringVar(value="unknown")
        self.actor_box = ttk.Combobox(row, textvariable=self.actor, values=list(self.speaker_roles), state="readonly", width=14)
        self.actor_box.pack(side="left")
        self.role_choice = tk.StringVar(value="未绑定")
        ttk.Combobox(row, textvariable=self.role_choice, values=ROLES, state="readonly", width=10).pack(side="left", padx=8)
        ttk.Button(row, text="绑定角色", command=self.bind_role).pack(side="left")
        ttk.Label(box, text="纠正最近片段（ID 见转写的 #编号）；相似度不是身份正确率。", wraplength=650).pack(anchor="w", pady=(12, 4))
        row = ttk.Frame(box)
        row.pack(fill="x")
        self.turn_choice = tk.StringVar()
        self.turn_box = ttk.Combobox(row, textvariable=self.turn_choice, values=list(self.turns), state="readonly", width=9)
        self.turn_box.pack(side="left")
        ttk.Button(row, text="纠正为上面所选说话人", command=self.correct_turn).pack(side="left", padx=8)
        ttk.Button(row, text="标记重叠 / 未知", command=lambda: self.correct_turn(unknown=True)).pack(side="left")
        ttk.Checkbutton(box, text="参与者已同意：下次监听保存必要测试音频（每段≤4.5秒，总计≤30秒）", variable=self.clip_consent).pack(anchor="w", pady=(20, 8))
        ttk.Label(box, text="默认不保存原始音频。声纹档案位于 voice_profiles/self.json；测试片段位于会话日志旁。匿名声纹只在本会话内存中保留。", wraplength=650).pack(anchor="w")
        ttk.Label(box, textvariable=self.speaker_status, wraplength=650).pack(anchor="w", pady=12)
        ttk.Label(box, textvariable=self.error, wraplength=650, foreground=AMBER).pack(anchor="w")

    def enroll(self):
        if self.controller.busy:
            self.show_error("请先停止当前监听，再录入本人声音。")
        elif not self.profile_consent.get():
            self.show_error("请先勾选同意保存本人声纹特征。")
        else:
            self.start(enrollment=True)

    def delete_profile(self):
        from sentinel_speakers import PROFILE
        if self.controller.busy:
            self.show_error("请先停止监听，再删除声纹。")
            return
        if messagebox.askyesno("删除本地声纹", "删除本机保存的本人声纹特征？", parent=self.settings):
            PROFILE.unlink(missing_ok=True)
            logging.info("Local self voice profile deleted")
            self.profile_status.set("本人声纹已删除")

    def finish_enrollment(self):
        if not self.controller.busy or not self.enrolling:
            self.show_error("请先开始录入本人声音。")
            return
        self.controller.send(kind="finish_enrollment")

    def bind_role(self):
        if self.actor.get() == "unknown" or not self.controller.busy:
            self.show_error("请在监听中选择已发现的说话人，未知不能绑定角色。")
            return
        self.controller.send(kind="role", speaker_id=self.actor.get(), role=self.role_choice.get())

    def correct_turn(self, unknown=False):
        if not self.controller.busy:
            self.show_error("请在监听中纠正片段归属。")
            return
        try:
            turn_id = int(self.turn_choice.get())
        except ValueError:
            self.show_error("请选择要纠正的片段编号。")
            return
        self.controller.send(kind="correct", turn_id=turn_id, speaker_id="unknown" if unknown else self.actor.get(),
                             reason="manual_overlap_or_unknown" if unknown else "manual_correction")

    def render_turns(self):
        self.transcript.configure(state="normal")
        self.transcript.delete("1.0", "end")
        for turn in self.turns.values():
            score = turn.get("confidence")
            confidence = "人工" if score is None else f"相似度 {score:.2f}"
            display_id = turn.get("speaker_id", "unknown")
            if display_id == "unknown":
                display_id = "unknown（未知）"
            self.transcript.insert("end", f"[#{turn['turn_id']} {turn.get('start_s', 0):.1f}-{turn.get('end_s', 0):.1f}s "
                                   f"{display_id} / {turn.get('role', '未绑定')} {confidence}] " + turn.get("text", "") + "\n")
        self.transcript.see("end")
        self.transcript.configure(state="disabled")
        if self.settings is not None and self.settings.winfo_exists():
            self.turn_box.configure(values=list(self.turns))

    def handle(self, evt):
        kind = evt.get("kind")
        if self.logic_document is not None and not self.enrolling:
            if kind=='final':evt.setdefault('turn_id',max(self.logic_document.turns,default=0)+1)
            self.logic_document.observe(evt)
            if self.logic_service and kind in ('final','attribution_update','speaker_roles','stopped'):
                self.logic_service.notify()
        stopping = self.controller.stop_event is not None and self.controller.stop_event.is_set()
        if kind == "session":
            self.log_path.set("会话日志：" + evt["log_path"])
            self.current_log = evt["log_path"]
            if self.controller.session_id:
                self.note_store.bind_session(self.controller.session_id, self.current_log)
        elif kind in ("llm_error", "fatal", "speaker_error"):
            self.show_error(evt.get("text", "未知错误"))
            if kind == "fatal":
                self.set_phase("错误", mic_on=False)
        elif kind in ("speaker_ready", "speaker_segment", "speaker_roles"):
            self.speaker_roles.update(evt.get("speakers", {}))
            names = "、".join(f"{k}/{v}" for k, v in self.speaker_roles.items() if k != "unknown")
            self.speaker_status.set("本地声纹已启用 · " + (names or "暂无可靠说话人，显示未知"))
            if kind == "speaker_segment":
                actor = evt.get("speaker_id", "unknown")
                self.speaker_status.set("当前：" + ("unknown（未知）" if actor == "unknown" else actor) + " / " + self.speaker_roles.get(actor, "未绑定"))
            if self.settings is not None and self.settings.winfo_exists():
                self.actor_box.configure(values=list(self.speaker_roles))
        elif kind == "enrollment_progress":
            self.speaker_status.set(f"本人声纹录入：有效片段 {evt['seconds']:.1f} / {evt['target']:.0f} 秒")
        elif kind == "enrollment_complete":
            self.enrollment_saved = True
            self.error_text = ""
            self.error.set("")
            self.speaker_status.set(f"本人声纹已保存（{evt['seconds']:.1f} 秒），下次监听识别为 Speaker A")
            self.headline.configure(text="本人声纹录入完成")
            self.action.configure(text="下次点击开始监听，可识别为 Speaker A。")
            if self.settings is not None and self.settings.winfo_exists():
                self.profile_status.set("已有本人声纹")
        elif kind == "attribution_update":
            if evt["turn_id"] in self.turns:
                self.turns[evt["turn_id"]] = evt
                self.render_turns()
        elif kind == "stopping_analysis":
            self.set_phase("停止中", mic_on=False)
            self.state.set("正在关闭 · 等待当前分析结束并保存日志" if self.closing
                           else "正在停止 · 等待当前分析结束并保存日志")
        elif kind == "stopped":
            self.set_phase("停止中", mic_on=False)
            self.log_path.set("日志已保存：" + evt["log_path"])
        elif stopping:
            return
        elif kind == "listening":
            self.set_phase("监听中" if not self.error_text else "错误", mic_on=True)
            self.state.set("● 录入本人声纹中 · 不请求 LLM" if self.enrolling else "● 监听中 · " + self.mode)
            self.headline.configure(text="正在监听，暂无重要变化")
            self.latest.set("等待麦克风输入…")
            if self.enrolling:
                self.headline.configure(text="正在录入本人声纹")
                self.evidence.configure(text="请本人单独自然讲话，可分多段；不会请求 LLM。")
                self.action.configure(text="达到15秒有效片段会自动保存；点击停止可取消。")
        elif kind == "partial":
            self.latest.set(evt.get("text", ""))
        elif kind == "final":
            evt.setdefault("turn_id", len(self.turns) + 1)
            self.turns[evt["turn_id"]] = evt
            if len(self.turns) > 128:
                self.turns.pop(next(iter(self.turns)))
            self.render_turns()
            self.latest.set("等待下一句话…")
        elif kind == "llm_pending":
            self.set_phase("监听中" if not self.error_text else "错误")
            self.state.set("● 监听中 · 等待 AI 分析")
        elif kind == "llm_running":
            self.set_phase("监听中" if not self.error_text else "错误")
            self.state.set("● 监听中 · AI 正在分析")
        elif kind == "llm_silent":
            self.set_phase("监听中" if not self.error_text else "错误")
            self.state.set(f"● 监听中 · AI 已检查（{evt.get('latency', 0):.1f}s）")
        elif kind == "alert":
            self.set_phase("监听中" if not self.error_text else "错误")
            provisional = evt.get("provisional", False)
            self.headline.configure(text=evt.get("headline", "重要变化"), fg=AMBER if provisional else GREEN)
            self.evidence.configure(text=("新转写尚在核实｜" if provisional else "依据｜") + evt.get("summary", ""))
            self.action.configure(text=evt.get("action", "请核对原话。"))
            self.state.set("● 监听中 · 发现关键变化")
            self.root.bell()

    def poll(self):
        try:
            for _ in range(40):
                try:
                    event = self.desktop_events.get_nowait()
                except queue.Empty:
                    break
                self.handle_desktop(event)
            for _ in range(200):
                self.handle(self.controller.events.get_nowait())
        except queue.Empty:
            pass
        except Exception as exc:
            self.callback_error(type(exc), exc, exc.__traceback__)
        if self.controller.reap():
            self.state.set("■ 已停止 · 存在错误，请查看下方" if self.error_text else "■ 已停止 · 可再次开始")
            self.latest.set("麦克风已关闭")
            self.set_phase("错误" if self.error_text else "待机", mic_on=False)
            self.goal_entry.configure(state="normal")
            if self.enrolling and not self.enrollment_saved:
                self.headline.configure(text="声纹录入已停止")
                self.action.configure(text="未完成录入，原有档案保留。")
            if self.headline.cget("text") in ("正在启动监听", "正在监听，暂无重要变化"):
                self.headline.configure(text="本次监听已结束")
                self.action.configure(text="选择场景后，可再次点击「开始监听」。")
            self.start_button.configure(state="disabled" if self.closing else "normal")
            self.stop_button.configure(state="disabled")
            self.scene_box.configure(state="readonly")
        from sentinel_ui import refresh_view
        refresh_view(self)
        if self.logic_window:self.logic_window.refresh()
        # ASR may flush one last confirmed segment during stop. Drain those
        # events before the graph worker takes its final persistent snapshot.
        if self.closing and not self.controller.busy:self.stop_logic_services()
        if self.closing and not self.controller.busy and not (self.api_check_thread and self.api_check_thread.is_alive()) and not any(s.thread.is_alive() for s in self.logic_workers):
            if not self.native_shutdown:
                self.native_shutdown = True
                if self.desktop:
                    self.desktop.close()
            if self.desktop is None or self.desktop.closed.is_set():
                self.root.destroy()
                return
        self._poll_id = self.root.after(80, self.poll)

    def close(self):
        # Closing a floating window does not terminate microphone capture.
        self.hide_window()

    def request_exit(self):
        if self.closing:
            return
        try:
            self.notes.save_draft()
        except Exception as exc:
            self.show_error("退出取消，笔记草稿未保存：" + str(exc))
            self.show_window()
            return
        self.closing = True
        self.start_button.configure(state="disabled")
        self.scene_box.configure(state="disabled")
        if self.controller.busy:
            self.stop()
        else:
            self.set_phase("停止中", mic_on=False)


def run_panel(args, runner):
    root = tk.Tk()
    panel = SentinelPanel(root, args, runner)
    panel.enable_native()
    root.after_idle(panel.show_window)
    try:
        root.mainloop()
    finally:
        # Also clean up if the Tk event loop ends unexpectedly.
        panel.controller.stop()
        panel.stop_logic_services()
        for service in panel.logic_workers:service.thread.join()
        if panel.controller.thread is not None:
            panel.controller.thread.join()
        try:
            panel.notes.save_draft()
        except tk.TclError:
            pass  # The normal exit path saved the draft before destroying Tk.
        if panel.desktop:
            panel.desktop.close()
            if panel.desktop.thread:
                panel.desktop.thread.join(timeout=5)
        if panel.api_check_thread:
            panel.api_check_thread.join()
