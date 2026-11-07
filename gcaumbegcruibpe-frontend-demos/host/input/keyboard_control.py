"""Key taps and holds. The recognizer decides when to call this."""

_NAMES = {
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
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
}


class KeyboardController(object):
    def __init__(self, live=False, press=None, release=None, output=None):
        self.live = bool(live)
        self._press = press
        self._release = release
        self._output = output if output is not None else _print
        self._held = set()
        self._controller = None

    def set_live(self, live):
        if self.live and not live:
            self.release_all()
        self.live = bool(live)

    def tap(self, key):
        if not self.live:
            self._output("dry-run: would press %s" % key)
            return False
        self.press(key)
        self.release(key)
        return True

    def press(self, key):
        if not self.live:
            return False
        self._held.add(key)
        if self._press is not None:
            self._press(key)
            return True
        self._controller_for().press(_resolve(key))
        return True

    def release(self, key):
        self._held.discard(key)
        if self._release is not None:
            self._release(key)
            return True
        if not self.live:
            return False
        self._controller_for().release(_resolve(key))
        return True

    def release_all(self):
        for key in list(self._held):
            self.release(key)
        self._held.clear()

    def _controller_for(self):
        if self._controller is None:
            from pynput.keyboard import Controller

            self._controller = Controller()
        return self._controller


def _resolve(name):
    from pynput.keyboard import Key

    if name in _NAMES:
        return getattr(Key, _NAMES[name])
    if len(name) == 1:
        return name
    raise ValueError("Cannot send %r. It is not a keyboard key." % name)


def _print(message):
    print(message, flush=True)
