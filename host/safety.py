"""Emergency stop and the stale-stream guard.

Esc pressed three times within one second latches the stop. It stays
latched until the process exits. A gesture is not injected while a module
it depends on is DOWN.
"""

import time

ESCAPE_PRESSES = 3
ESCAPE_WINDOW_S = 1.0


def _print(message):
    print(message, flush=True)


def key_name(key):
    character = getattr(key, "char", None)
    if character:
        return character
    name = getattr(key, "name", None)
    if name:
        return name
    return str(key)


class Safety(object):
    def __init__(self, output=None):
        self.stopped = False
        self._escapes = []
        self._output = output if output is not None else _print
        self._listener = None

    def listen(self):
        """Start the pynput keyboard listener. Returns False if it cannot see keys."""
        from pynput.keyboard import Listener

        self._output("Listening for keys. Press Esc three times within one second to stop.")
        listener = Listener(on_press=self.on_key_press)
        listener.daemon = True
        listener.start()
        listener.wait()
        self._listener = listener
        trusted = getattr(listener, "IS_TRUSTED", True)
        if listener.is_alive() and trusted:
            return True
        self._output(
            "This terminal is not allowed to read the keyboard. Turn it on under "
            "System Settings, Privacy & Security, Accessibility, then run the script again."
        )
        return False

    def stop(self):
        listener = self._listener
        self._listener = None
        if listener is not None:
            listener.stop()

    def on_key_press(self, key, injected=False, now=None):
        """Print the key. Esc is counted toward the emergency stop."""
        name = key_name(key)
        self._output("key press: %s" % name)
        if name in ("esc", "escape"):
            self.note_escape(now)

    def note_escape(self, now=None):
        """Record one Esc press. The third press inside the window latches the stop."""
        if self.stopped:
            return
        if now is None:
            now = time.monotonic()
        self._escapes = [
            stamp for stamp in self._escapes if now - stamp <= ESCAPE_WINDOW_S
        ]
        self._escapes.append(now)
        if len(self._escapes) >= ESCAPE_PRESSES:
            self.stopped = True
            self._output(
                "Emergency stop is active. No keys will be sent until the host restarts."
            )

    def injection_blocked(self, depends_on, upper_down, lower_down, sensor_down=False):
        """True when the stop is latched or a required module is DOWN."""
        if self.stopped:
            return True
        return self.stream_blocked(depends_on, upper_down, lower_down, sensor_down)

    def stream_blocked(self, depends_on, upper_down, lower_down, sensor_down=False):
        """True when a module this gesture uses is DOWN."""
        needed = set(depends_on)
        if sensor_down and "sensor" in needed:
            return True
        if upper_down and "upper" in needed:
            return True
        if lower_down and "lower" in needed:
            return True
        return False
