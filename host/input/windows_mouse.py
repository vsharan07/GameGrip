"""Relative mouse motion for Windows games, using native SendInput."""

import ctypes


class MouseInput(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_int32), ("dy", ctypes.c_int32),
                ("mouseData", ctypes.c_uint32), ("dwFlags", ctypes.c_uint32),
                ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]


class KeyboardInput(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_uint16), ("wScan", ctypes.c_uint16),
                ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32),
                ("dwExtraInfo", ctypes.c_size_t)]


class HardwareInput(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_uint32), ("wParamL", ctypes.c_uint16),
                ("wParamH", ctypes.c_uint16)]


class InputUnion(ctypes.Union):
    _fields_ = [("mi", MouseInput), ("ki", KeyboardInput), ("hi", HardwareInput)]


class Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("value", InputUnion)]


class WindowsMouse:
    def __init__(self, send_input=None):
        if send_input is None:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            send_input = user32.SendInput
            send_input.argtypes = (ctypes.c_uint32, ctypes.POINTER(Input), ctypes.c_int)
            send_input.restype = ctypes.c_uint32
        self._send_input = send_input

    def move(self, dx, dy):
        event = Input(type=0, value=InputUnion(mi=MouseInput(dx=dx, dy=dy, dwFlags=0x0001)))
        if self._send_input(1, ctypes.byref(event), ctypes.sizeof(Input)) != 1:
            raise OSError("Windows rejected relative mouse input")
