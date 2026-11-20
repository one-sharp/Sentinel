"""Optional chrome for Tk workspace/bar; meeting acrylic lives in Qt."""
import ctypes as C
from ctypes import wintypes as W
import logging
import os
import tkinter as tk

FLOATING_OPACITY = .98


def enable_dpi_awareness():
    """Call before creating Tk; an existing host DPI policy always wins."""
    if os.name == 'nt':
        try:
            user = C.WinDLL('user32')
            user.SetProcessDpiAwarenessContext.argtypes = [C.c_void_p]
            user.SetProcessDpiAwarenessContext(C.c_void_p(-4))
        except (OSError, AttributeError):
            pass


def apply_chrome(root, floating, translucent=True):
    if not root.winfo_ismapped():
        return
    try:
        alpha = FLOATING_OPACITY if floating and translucent else 1.0
        root.attributes('-alpha', alpha)
    except tk.TclError:
        logging.debug('Optional window translucency unavailable', exc_info=True)
    if os.name != 'nt':
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
            # Keep a taskbar entry for recovering a hidden floating window.
            user.GetWindowLongPtrW.argtypes,user.GetWindowLongPtrW.restype = [W.HWND,C.c_int],C.c_ssize_t
            user.SetWindowLongPtrW.argtypes,user.SetWindowLongPtrW.restype = [W.HWND,C.c_int,C.c_ssize_t],C.c_ssize_t
            exstyle = user.GetWindowLongPtrW(hwnd,-20)
            wanted = (exstyle & ~0x80) | 0x40000
            if wanted != exstyle:
                user.SetWindowLongPtrW(hwnd,-20,wanted)
                user.SetWindowPos.argtypes = [W.HWND,W.HWND,C.c_int,C.c_int,C.c_int,C.c_int,W.UINT]
                user.SetWindowPos(hwnd,None,0,0,0,0,0x37)
            rect = W.RECT()
            user.GetWindowRect.argtypes = [W.HWND,C.POINTER(W.RECT)]
            user.GetWindowRect(hwnd,C.byref(rect))
            radius = max(20,round(20*root.winfo_fpixels('1i')/96))
            region = gdi.CreateRoundRectRgn(0,0,rect.right-rect.left+1,rect.bottom-rect.top+1,radius,radius)
            # SetWindowRgn owns a successfully assigned region.
            if region and not user.SetWindowRgn(hwnd,region,True):
                gdi.DeleteObject(region)
        else:
            user.SetWindowRgn(hwnd,None,True)
        dwm = C.WinDLL('dwmapi')
        dwm.DwmSetWindowAttribute.argtypes = [W.HWND,W.DWORD,W.LPCVOID,W.DWORD]
        dark, corners = C.c_int(0), C.c_int(2)
        dwm.DwmSetWindowAttribute(hwnd,20,C.byref(dark),C.sizeof(dark))
        dwm.DwmSetWindowAttribute(hwnd,33,C.byref(corners),C.sizeof(corners))
    except (OSError, AttributeError):
        logging.debug('Optional window chrome unavailable',exc_info=True)
