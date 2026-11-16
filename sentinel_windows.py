"""Native Windows RegisterHotKey + Shell_NotifyIcon on an owned message thread.

All callbacks emit plain events. No Tk API or audio API is called from here.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import logging
import hashlib
import os
from pathlib import Path
import queue
import threading
import time
import uuid

from sentinel_desktop import DEFAULT_HOTKEYS, ROOT
from sentinel_paths import ASSETS_DIR, user_data_dir

INSTANCE_KEY = (hashlib.sha256(str(user_data_dir()).casefold().encode()).hexdigest()[:20]
                if os.getenv("SENTINEL_DATA_DIR") else "v031")
MESSAGE_WINDOW = "SentinelDesktop-" + INSTANCE_KEY


class InstanceGuard:
    """Keep double-clicking the launcher from opening another microphone owner."""
    def __init__(self, key=INSTANCE_KEY):
        self.key, self.handle = key, None

    def acquire(self):
        if os.name != "nt":
            return True
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateMutexW.argtypes, self.kernel.CreateMutexW.restype = [W.LPVOID,W.BOOL,W.LPCWSTR], W.HANDLE
        self.kernel.CloseHandle.argtypes, self.kernel.CloseHandle.restype = [W.HANDLE], W.BOOL
        self.handle = self.kernel.CreateMutexW(None, False, "Local\\SentinelDesktop-" + self.key)
        if not self.handle:
            raise C.WinError(C.get_last_error())
        if C.get_last_error() == 183:
            self.release()
            self.restore_existing()
            return False
        return True

    def restore_existing(self):
        user = C.WinDLL("user32", use_last_error=True)
        user.FindWindowW.argtypes, user.FindWindowW.restype = [W.LPCWSTR,W.LPCWSTR], W.HWND
        user.RegisterWindowMessageW.argtypes, user.RegisterWindowMessageW.restype = [W.LPCWSTR], W.UINT
        user.PostMessageW.argtypes, user.PostMessageW.restype = [W.HWND,W.UINT,W.WPARAM,W.LPARAM], W.BOOL
        title = "SentinelDesktop-" + self.key
        message = user.RegisterWindowMessageW(title + ".show")
        for _ in range(10):
            hwnd = user.FindWindowW(None, title)
            if hwnd:
                user.PostMessageW(hwnd, message, 0, 0)
                break
            time.sleep(.05)

    def release(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

WM_HOTKEY = 0x0312
WM_TRAY = 0x8001
WM_COMMANDS = 0x8002
HOTKEY_IDS = {name: 0x5100+i for i,name in enumerate(DEFAULT_HOTKEYS)}


def parse_hotkey(chord):
    if not isinstance(chord, str):
        raise ValueError("快捷键必须是文本")
    pieces = [p.strip().upper() for p in chord.split("+")]
    if len(pieces) < 2 or len(pieces) != len(set(pieces)):
        raise ValueError("请使用 Ctrl+Alt+Shift+N 这类组合")
    modifiers = {"CTRL": 2, "CONTROL": 2, "ALT": 1, "SHIFT": 4, "WIN": 8}
    mask = 0
    for key in pieces[:-1]:
        if key not in modifiers:
            raise ValueError("未知修饰键：" + key)
        mask |= modifiers[key]
    if not mask & (1|2|8):
        raise ValueError("至少包含 Ctrl、Alt 或 Win，避免拦截普通输入")
    key = pieces[-1]
    named = {"SPACE":32, "ENTER":13, "TAB":9, "ESC":27, "HOME":36, "END":35,
             "UP":38, "DOWN":40, "LEFT":37, "RIGHT":39, "INSERT":45, "DELETE":46}
    if key in named:
        vk = named[key]
    elif len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key)
    elif key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:])-1
    else:
        raise ValueError("不支持的主键：" + key)
    if vk == 0x7B:
        raise ValueError("F12 被 Windows 调试器保留，请换一组")
    return mask | 0x4000, vk  # MOD_NOREPEAT prevents key-repeat start/stop races.


class HotkeyRegistry:
    """Testable Win32 adapter; reconfiguration rolls back on conflicts."""
    def __init__(self, api, hwnd):
        self.api, self.hwnd = api, hwnd
        self.active = {}

    def apply(self, mapping):
        parsed, seen = {}, set()
        for action, chord in mapping.items():
            if action not in HOTKEY_IDS:
                raise ValueError("未知快捷键动作")
            spec = parse_hotkey(chord)
            if spec in seen:
                raise ValueError("同一组快捷键不能绑定两个操作")
            seen.add(spec)
            parsed[action] = spec
        previous = dict(self.active)
        self.close()
        errors = {}
        for action, spec in parsed.items():
            if self.api.RegisterHotKey(self.hwnd, HOTKEY_IDS[action], *spec):
                self.active[action] = mapping[action]
            else:
                error = C.get_last_error() if os.name == "nt" else 0
                reason = "被其他程序占用" if error == 1409 else ("桌面权限被拒绝" if error == 5 else "冲突或系统保留")
                errors[action] = f"{mapping[action]} 注册失败：{reason}（Windows错误 {error}）"
        if errors and previous:
            self.close()
            for action, chord in previous.items():
                if self.api.RegisterHotKey(self.hwnd, HOTKEY_IDS[action], *parse_hotkey(chord)):
                    self.active[action] = chord
                else:
                    errors[action] = "原快捷键恢复失败，请在设置中重新绑定：" + chord
        return {"kind": "hotkeys_applied", "success": not errors,
                "active": dict(self.active), "errors": errors, "requested": dict(mapping)}

    def close(self):
        for action in list(self.active):
            self.api.UnregisterHotKey(self.hwnd, HOTKEY_IDS[action])
        self.active.clear()


class WindowsDesktop:
    def __init__(self, emit, hotkeys=None, assets=None):
        self.emit = emit
        self.hotkeys = dict(hotkeys or {})
        self.assets = Path(assets) if assets else ASSETS_DIR
        self.commands = queue.Queue()
        self.ready, self.closed = threading.Event(), threading.Event()
        self.close_requested = threading.Event()
        self.hwnd = None
        self.tray_available = False
        self.phase, self.mic_on, self.busy = "待机", False, False
        self.thread = None

    def start(self):
        if self.thread is not None:
            return
        self.thread = threading.Thread(target=self._run, name="sentinel-windows", daemon=False)
        self.thread.start()

    def configure(self, hotkeys):
        self._post({"kind":"hotkeys", "mapping": dict(hotkeys)})

    def update_status(self, phase, mic_on, busy):
        self._post({"kind":"status", "phase": phase, "mic_on": mic_on, "busy": busy})

    def close(self):
        self.close_requested.set()
        if self.hwnd:
            self.user.PostMessageW(self.hwnd, 0x10, 0, 0)

    def _post(self, command):
        self.commands.put(command)
        if self.hwnd:
            self.user.PostMessageW(self.hwnd, WM_COMMANDS, 0, 0)

    def _run(self):
        registry = None
        instance = None
        class_name = "SentinelDesktop-" + uuid.uuid4().hex
        icons = {}
        try:
            if os.name != "nt":
                raise OSError("全局快捷键与系统托盘需要 Windows")
            self.user = user = C.WinDLL("user32", use_last_error=True)
            shell = C.WinDLL("shell32", use_last_error=True)
            kernel = C.WinDLL("kernel32", use_last_error=True)
            LRESULT = C.c_ssize_t
            WNDPROC = C.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)

            class WC(C.Structure):
                _fields_ = [("style",W.UINT),("proc",WNDPROC),("extra",C.c_int),("window_extra",C.c_int),
                            ("instance",W.HINSTANCE),("icon",W.HICON),("cursor",W.HANDLE),
                            ("background",W.HBRUSH),("menu",W.LPCWSTR),("name",W.LPCWSTR)]
            class GUID(C.Structure):
                _fields_ = [("a",W.DWORD),("b",W.WORD),("c",W.WORD),("d",C.c_ubyte*8)]
            class NID(C.Structure):
                _fields_ = [("cbSize",W.DWORD),("hWnd",W.HWND),("uID",W.UINT),("uFlags",W.UINT),
                            ("uCallbackMessage",W.UINT),("hIcon",W.HICON),("szTip",W.WCHAR*128),
                            ("dwState",W.DWORD),("dwStateMask",W.DWORD),("szInfo",W.WCHAR*256),
                            ("uVersion",W.UINT),("szInfoTitle",W.WCHAR*64),("dwInfoFlags",W.DWORD),
                            ("guidItem",GUID),("hBalloonIcon",W.HICON)]
            signatures = {
                "DefWindowProcW": ([W.HWND,W.UINT,W.WPARAM,W.LPARAM], LRESULT),
                "RegisterClassW": ([C.POINTER(WC)],W.ATOM),
                "UnregisterClassW": ([W.LPCWSTR,W.HINSTANCE],W.BOOL),
                "CreateWindowExW": ([W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,C.c_int,C.c_int,C.c_int,C.c_int,W.HWND,W.HMENU,W.HINSTANCE,W.LPVOID],W.HWND),
                "DestroyWindow": ([W.HWND],W.BOOL), "PostMessageW": ([W.HWND,W.UINT,W.WPARAM,W.LPARAM],W.BOOL),
                "RegisterHotKey": ([W.HWND,C.c_int,W.UINT,W.UINT],W.BOOL),
                "UnregisterHotKey": ([W.HWND,C.c_int],W.BOOL),
                "GetMessageW": ([C.POINTER(W.MSG),W.HWND,W.UINT,W.UINT],W.BOOL),
                "TranslateMessage": ([C.POINTER(W.MSG)],W.BOOL),
                "DispatchMessageW": ([C.POINTER(W.MSG)],LRESULT),
                "PostQuitMessage": ([C.c_int],None),
                "LoadImageW": ([W.HINSTANCE,W.LPCWSTR,W.UINT,C.c_int,C.c_int,W.UINT],W.HANDLE),
                "DestroyIcon": ([W.HICON],W.BOOL),
                "CreatePopupMenu": ([],W.HMENU),
                "AppendMenuW": ([W.HMENU,W.UINT,C.c_size_t,W.LPCWSTR],W.BOOL),
                "TrackPopupMenu": ([W.HMENU,W.UINT,C.c_int,C.c_int,C.c_int,W.HWND,W.LPVOID],W.UINT),
                "DestroyMenu": ([W.HMENU],W.BOOL),
                "SetForegroundWindow": ([W.HWND],W.BOOL),
                "GetCursorPos": ([C.POINTER(W.POINT)],W.BOOL),
                "RegisterWindowMessageW": ([W.LPCWSTR],W.UINT),
            }
            for name, (argtypes, restype) in signatures.items():
                function = getattr(user, name)
                function.argtypes, function.restype = argtypes, restype
            kernel.GetModuleHandleW.argtypes, kernel.GetModuleHandleW.restype = [W.LPCWSTR], W.HMODULE
            shell.Shell_NotifyIconW.argtypes, shell.Shell_NotifyIconW.restype = [W.DWORD,C.POINTER(NID)],W.BOOL
            instance = kernel.GetModuleHandleW(None)
            taskbar_created = user.RegisterWindowMessageW("TaskbarCreated")
            show_application = user.RegisterWindowMessageW(MESSAGE_WINDOW + ".show")
            for name in ("idle", "active", "error", "stopping"):
                handle = user.LoadImageW(None, str(self.assets / f"sentinel-{name}.ico"), 1, 32, 32, 0x10)
                if not handle:
                    raise OSError(f"托盘图标加载失败：sentinel-{name}.ico")
                icons[name] = handle
            nid = NID()
            nid.cbSize, nid.uID = C.sizeof(NID), 1
            nid.uFlags, nid.uCallbackMessage = 1|2|4, WM_TRAY

            def tray(add=False):
                key = "active" if self.mic_on else ("error" if "错误" in self.phase else ("stopping" if "停止" in self.phase else "idle"))
                nid.hIcon = icons[key]
                nid.szTip = f"Sentinel 哨兵 · {self.phase} · 麦克风{'开启' if self.mic_on else '关闭'}"[:127]
                ok = shell.Shell_NotifyIconW(0 if add else 1, C.byref(nid))
                self.tray_available = bool(ok)
                if add:
                    if ok:
                        nid.uVersion = 3  # Explicit legacy notification layout.
                        shell.Shell_NotifyIconW(4, C.byref(nid))
                    self.emit(kind="tray_ready", available=bool(ok))

            def popup():
                menu = user.CreatePopupMenu()
                try:
                    mic = "开启" if self.mic_on else "关闭"
                    user.AppendMenuW(menu, 2, 0, f"{self.phase} · 麦克风{mic}")
                    for item_id, text in ((101,"显示窗口"),(102,"私人快速笔记"),
                                          (103,"停止监听" if self.busy else "开始监听"),(104,"退出 Sentinel")):
                        user.AppendMenuW(menu, 0, item_id, text)
                    point = W.POINT()
                    user.GetCursorPos(C.byref(point))
                    user.SetForegroundWindow(self.hwnd)
                    command = user.TrackPopupMenu(menu, 0x100|0x2, point.x, point.y, 0, self.hwnd, None)
                    action = {101:"show_window",102:"quick_note",103:"toggle_listening",104:"exit"}.get(command)
                    if action:
                        self.emit(kind="desktop_action", action=action)
                    user.PostMessageW(self.hwnd, 0, 0, 0)
                finally:
                    user.DestroyMenu(menu)

            @WNDPROC
            def proc(hwnd, msg, wp, lp):
                try:
                    if msg == show_application:
                        self.emit(kind="desktop_action", action="show_window")
                        return 0
                    if msg == WM_HOTKEY:
                        action = next((a for a,i in HOTKEY_IDS.items() if i == wp), None)
                        if action:
                            self.emit(kind="desktop_action", action=action)
                        return 0
                    if msg == WM_TRAY:
                        if lp in (0x202,0x203):  # Left click/double click restores.
                            self.emit(kind="desktop_action", action="show_window")
                        elif lp in (0x205,0x7B):
                            popup()
                        return 0
                    if msg == WM_COMMANDS:
                        while not self.commands.empty():
                            command = self.commands.get_nowait()
                            if command["kind"] == "hotkeys":
                                self.emit(**registry.apply(command["mapping"]))
                            elif command["kind"] == "status":
                                self.phase, self.mic_on, self.busy = command["phase"],command["mic_on"],command["busy"]
                                tray()
                        return 0
                    if msg == taskbar_created:
                        tray(add=True)
                        return 0
                    if msg == 0x10:
                        user.DestroyWindow(hwnd)
                        return 0
                    if msg == 0x2:
                        user.PostQuitMessage(0)
                        return 0
                except Exception as exc:
                    logging.exception("Windows desktop callback failed")
                    self.emit(kind="desktop_error", text=str(exc))
                    return 0
                return user.DefWindowProcW(hwnd, msg, wp, lp)

            self._proc = proc  # Keep the native callback alive until class removal.
            wc = WC()
            wc.proc, wc.instance, wc.name = proc, instance, class_name
            if not user.RegisterClassW(C.byref(wc)):
                raise C.WinError(C.get_last_error())
            self.hwnd = user.CreateWindowExW(0,class_name,MESSAGE_WINDOW,0,0,0,0,0,None,None,instance,None)
            if not self.hwnd:
                raise C.WinError(C.get_last_error())
            nid.hWnd = self.hwnd
            registry = HotkeyRegistry(user, self.hwnd)
            try:
                self.emit(**registry.apply(self.hotkeys))
            except ValueError as exc:
                self.emit(kind="desktop_error", text="快捷键配置无效，已尝试默认绑定：" + str(exc))
                self.emit(**registry.apply(DEFAULT_HOTKEYS))
            tray(add=True)
            self.ready.set()
            user.PostMessageW(self.hwnd, WM_COMMANDS, 0, 0)
            if self.close_requested.is_set():
                user.PostMessageW(self.hwnd, 0x10, 0, 0)
            message = W.MSG()
            while True:
                result = user.GetMessageW(C.byref(message), None, 0, 0)
                if result == 0:
                    break
                if result == -1:
                    raise C.WinError(C.get_last_error())
                user.TranslateMessage(C.byref(message))
                user.DispatchMessageW(C.byref(message))
        except Exception as exc:
            logging.exception("Native desktop integration failed")
            self.emit(kind="desktop_error", text="热键/托盘不可用：" + str(exc))
        finally:
            if registry:
                registry.close()
            if self.hwnd:
                shell.Shell_NotifyIconW(2, C.byref(nid))
                user.DestroyWindow(self.hwnd)
                self.hwnd = None
            self.tray_available = False
            for icon in icons.values():
                user.DestroyIcon(icon)
            if instance:
                user.UnregisterClassW(class_name, instance)
            self.ready.set()
            self.closed.set()
            self.emit(kind="desktop_closed")
