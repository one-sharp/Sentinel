"""White native acrylic for Qt; no global opacity or colour-key input holes."""
import ctypes as C
from ctypes import wintypes as W
import logging
import os

class AccentPolicy(C.Structure):
    _fields_ = [('state', C.c_int), ('flags', C.c_int), ('color', W.DWORD), ('animation', C.c_int)]

class CompositionData(C.Structure):
    _fields_ = [('attribute', C.c_int), ('data', C.c_void_p), ('size', C.c_size_t)]

def apply_backdrop(hwnd):
    if os.name != 'nt':
        return False
    try:
        user = C.WinDLL('user32')
        user.SetWindowCompositionAttribute.argtypes = [W.HWND, C.POINTER(CompositionData)]
        # Untinted accent blur can inherit the Windows grey tint. Use a light
        # white acrylic base, keeping all foreground text fully opaque.
        for state, flags, color in ((4, 2, 0x20FFFFFF), (3, 0, 0)):
            accent = AccentPolicy(state, flags, color, 0)
            data = CompositionData(19, C.cast(C.pointer(accent), C.c_void_p), C.sizeof(accent))
            if user.SetWindowCompositionAttribute(hwnd, C.byref(data)):
                return True
        return False
    except (OSError, AttributeError):
        logging.debug('Native acrylic unavailable', exc_info=True)
        return False
