"""Send a key only after a match, with every required module UP and the stop off.

Dry-run is the default: the key is printed and nothing is sent. Live mode
presses the key through pynput. pynput is imported only while sending a
live key. The Esc listener lives in safety.py and shares this Safety object.
"""


def _print(message):
    print(message, flush=True)


class Injector(object):
    def __init__(self, live=False, press=None, output=None):
        self.live = bool(live)
        self._press = press
        self._output = output if output is not None else _print
        self._controller = None
        self._mouse = None

    def inject(self, key, matched, upper_down, lower_down, safety, depends_on, sensor_down=False):
        """Print or press `key`. Return True only when a key was accepted.

        `depends_on` names the modules this gesture needs, such as
        ("upper", "lower") or ("sensor",). A module that is DOWN blocks
        only those gestures. sensor_down means the single IMU stream is
        missing or invalid.
        """
        if not matched:
            return False
        if safety.stopped:
            self._output("blocked: %s" % key)
            return False
        if safety.injection_blocked(depends_on, upper_down, lower_down, sensor_down=sensor_down):
            return False
        if not self.live:
            self._output("dry-run: would press %s" % key)
            return True
        self._send_live(key)
        return True

    def close(self):
        return None

    def _send_live(self, key):
        if self._press is not None:
            self._press(key)
            return
        if key in _MOUSE_CLICKS:
            self._click(_MOUSE_CLICKS[key])
            return
        from pynput.keyboard import Controller, Key

        if self._controller is None:
            self._controller = Controller()
        resolved = _keyboard_key(key, Key)
        self._controller.press(resolved)
        self._controller.release(resolved)

    def _click(self, button_name):
        from pynput.mouse import Button, Controller

        if self._mouse is None:
            self._mouse = Controller()
        self._mouse.click(getattr(Button, button_name))


_MOUSE_CLICKS = {
    "left_click": "left",
    "right_click": "right",
    "middle_click": "middle",
}


def _keyboard_key(name, key_enum):
    names = {
        "space": "space",
        "esc": "esc",
        "escape": "esc",
        "enter": "enter",
        "return": "enter",
        "tab": "tab",
        "left_ctrl": "ctrl_l",
        "right_ctrl": "ctrl_r",
        "ctrl": "ctrl",
        "left_shift": "shift_l",
        "right_shift": "shift_r",
        "shift": "shift",
        "left_alt": "alt_l",
        "right_alt": "alt_r",
        "alt": "alt",
    }
    if name in names:
        return getattr(key_enum, names[name])
    if len(name) == 1:
        return name
    raise ValueError("Cannot send %r. It is not a keyboard key." % name)
