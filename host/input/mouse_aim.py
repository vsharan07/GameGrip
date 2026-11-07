"""Relative mouse movement from gyro, plus clicks.

The axis map is configuration. gx/gy/gz are the chip axes in deg/s, not a
claim about how the toy is mounted. Position is never integrated.
"""

_AXES = {"gx": 3, "gy": 4, "gz": 5}


class AimSettings(object):
    def __init__(
        self,
        enabled=False,
        horizontal_axis="gx",
        vertical_axis="gy",
        invert_horizontal=False,
        invert_vertical=False,
        sensitivity=18.0,
        horizontal_sensitivity=None,
        vertical_sensitivity=None,
        smoothing=0.9,
        deadzone_dps=2.0,
        neutral_dps=None,
        max_pixels_per_second=25000.0,
        acceleration=1.4,
        paused=False,
        bias_dps=None,
        mount_learned=False,
    ):
        self.enabled = bool(enabled)
        self.horizontal_axis = horizontal_axis
        self.vertical_axis = vertical_axis
        self.invert_horizontal = bool(invert_horizontal)
        self.invert_vertical = bool(invert_vertical)
        self.sensitivity = float(sensitivity)
        self.horizontal_sensitivity = float(
            self.sensitivity if horizontal_sensitivity is None else horizontal_sensitivity
        )
        self.vertical_sensitivity = float(
            self.sensitivity if vertical_sensitivity is None else vertical_sensitivity
        )
        self.smoothing = float(smoothing)
        self.deadzone_dps = float(deadzone_dps)
        self.neutral_dps = None if neutral_dps is None else float(neutral_dps)
        self.max_pixels_per_second = float(max_pixels_per_second)
        self.acceleration = float(acceleration)
        self.paused = bool(paused)
        self.bias_dps = (0.0, 0.0, 0.0) if not bias_dps else tuple(float(value) for value in bias_dps)
        self.mount_learned = bool(mount_learned)

    def neutral_threshold(self):
        if self.neutral_dps is None:
            return self.deadzone_dps
        return self.neutral_dps

    def to_dict(self):
        return {
            "enabled": self.enabled,
            "horizontal_axis": self.horizontal_axis,
            "vertical_axis": self.vertical_axis,
            "invert_horizontal": self.invert_horizontal,
            "invert_vertical": self.invert_vertical,
            "sensitivity": self.sensitivity,
            "horizontal_sensitivity": self.horizontal_sensitivity,
            "vertical_sensitivity": self.vertical_sensitivity,
            "smoothing": self.smoothing,
            "deadzone_dps": self.deadzone_dps,
            "neutral_dps": self.neutral_dps,
            "max_pixels_per_second": self.max_pixels_per_second,
            "acceleration": self.acceleration,
            "paused": self.paused,
            "bias_dps": list(self.bias_dps),
            "mount_learned": self.mount_learned,
        }

    @classmethod
    def from_dict(cls, raw):
        if not raw:
            return cls()
        known = cls()
        values = known.to_dict()
        values.update(raw)
        if "horizontal_sensitivity" not in raw and "sensitivity" in raw:
            values["horizontal_sensitivity"] = raw["sensitivity"]
        if "vertical_sensitivity" not in raw and "sensitivity" in raw:
            values["vertical_sensitivity"] = raw["sensitivity"]
        return cls(**values)


def aim_delta(sample, dt_s, settings, smooth=(0.0, 0.0)):
    """Return (dx, dy, new_smooth). dx/dy are pixels for this time step.

    Positive dx moves the pointer right. Positive dy moves the pointer down.
    A learned mount inverts the vertical axis so toy-up becomes pointer-up.
    """
    if settings.paused or not _finite_gyro(sample):
        return 0.0, 0.0, (0.0, 0.0)
    horizontal = _axis(sample, settings.horizontal_axis, settings.bias_dps)
    vertical = _axis(sample, settings.vertical_axis, settings.bias_dps)
    if settings.invert_horizontal:
        horizontal = -horizontal
    if settings.invert_vertical:
        vertical = -vertical
    alpha = settings.smoothing
    if alpha <= 0.0:
        smoothed_h, smoothed_v = horizontal, vertical
    else:
        if alpha > 1.0:
            alpha = 1.0
        smoothed_h = alpha * horizontal + (1.0 - alpha) * smooth[0]
        smoothed_v = alpha * vertical + (1.0 - alpha) * smooth[1]
    used_h = _above_deadzone(smoothed_h, settings.deadzone_dps)
    used_v = _above_deadzone(smoothed_v, settings.deadzone_dps)
    used_h = _curve(used_h, settings.acceleration)
    used_v = _curve(used_v, settings.acceleration)
    dx = used_h * settings.horizontal_sensitivity * dt_s
    dy = used_v * settings.vertical_sensitivity * dt_s
    cap = settings.max_pixels_per_second * dt_s
    dx = _clamp(dx, -cap, cap)
    dy = _clamp(dy, -cap, cap)
    return dx, dy, (smoothed_h, smoothed_v)


class MouseController(object):
    """Clicks and pointer motion. live=False never calls the output callbacks."""

    def __init__(self, live=False, move=None, press=None, release=None, output=None):
        self.live = bool(live)
        self._move = move
        self._press = press
        self._release = release
        self._output = output if output is not None else _print
        self._held = set()
        self._mouse = None
        self.smooth = (0.0, 0.0)
        self._fraction = [0.0, 0.0]
        self._relative = post_relative_delta

    def set_live(self, live):
        if self.live and not live:
            self.release_all()
        self.live = bool(live)

    def aim(self, sample, dt_s, settings):
        dx, dy, self.smooth = aim_delta(sample, dt_s, settings, self.smooth)
        if not settings.enabled:
            return dx, dy
        if not self.live:
            return dx, dy
        self._move_pixels(dx, dy)
        return dx, dy

    def tap(self, button):
        """button is left, right, or middle."""
        if not self.live:
            self._output("dry-run: would click %s" % button)
            return False
        self.press(button)
        self.release(button)
        return True

    def press(self, button):
        if not self.live:
            return False
        self._held.add(button)
        if self._press is not None:
            self._press(button)
            return True
        mouse, enum = self._backend()
        mouse.press(getattr(enum, button))
        return True

    def release(self, button):
        self._held.discard(button)
        if not self.live and self._release is None:
            return False
        if self._release is not None:
            self._release(button)
            return True
        if not self.live:
            return False
        mouse, enum = self._backend()
        mouse.release(getattr(enum, button))
        return True

    def release_all(self):
        for button in list(self._held):
            self.release(button)
        self._held.clear()

    def reset_motion(self):
        self.smooth = (0.0, 0.0)
        self._fraction = [0.0, 0.0]

    def _move_pixels(self, dx, dy):
        """Send relative pixels. Fractions carry to the next sample.

        A 0.4 pixel step used to round to 0, so a normal turn never moved
        the camera. pynput on macOS posts an absolute cursor position.
        Minecraft's captured mouse uses the event's relative deltas, so the
        live path sets those delta fields instead.
        """
        self._fraction[0] += dx
        self._fraction[1] += dy
        ix = int(self._fraction[0])
        iy = int(self._fraction[1])
        self._fraction[0] -= ix
        self._fraction[1] -= iy
        if ix == 0 and iy == 0:
            return
        if self._move is not None:
            self._move(ix, iy)
            return
        try:
            self._relative(ix, iy)
        except Exception as exc:
            self._output("Relative mouse move failed (%s). Further moves are not sent." % exc)
            self.live = False

    def _backend(self):
        if self._mouse is None:
            from pynput.mouse import Button, Controller

            self._mouse = Controller()
            self._enum = Button
        return self._mouse, self._enum


def mapped_rates(sample, settings):
    """Bias-subtracted, inverted gyro rates in deg/s. (horizontal, vertical)."""
    if settings.paused or not _finite_gyro(sample):
        return 0.0, 0.0
    horizontal = _axis(sample, settings.horizontal_axis, settings.bias_dps)
    vertical = _axis(sample, settings.vertical_axis, settings.bias_dps)
    if settings.invert_horizontal:
        horizontal = -horizontal
    if settings.invert_vertical:
        vertical = -vertical
    return horizontal, vertical


def _axis(sample, name, bias):
    try:
        index = _AXES[name]
    except KeyError:
        raise ValueError("Aim axis must be gx, gy, or gz, not %r." % name)
    return float(sample[index]) - float(bias[index - 3])


def _above_deadzone(rate, deadzone):
    """Drop the dead zone off the rate so a move just outside it does not jump."""
    if abs(rate) <= deadzone:
        return 0.0
    if rate > 0.0:
        return rate - deadzone
    return rate + deadzone


def _curve(rate, strength):
    """Linear at strength 1. Higher strengths boost fast rotation more than slow."""
    if rate == 0.0 or strength <= 1.0:
        return rate
    sign = 1.0 if rate > 0.0 else -1.0
    magnitude = abs(rate)
    return sign * magnitude * (magnitude / 100.0) ** (strength - 1.0)


def _finite_gyro(sample):
    if sample is None or len(sample) < 6:
        return False
    for value in sample[3:6]:
        if value != value or value in (float("inf"), float("-inf")):
            return False
    return True


def _clamp(value, low, high):
    if value < low:
        return low
    if value > high:
        return high
    return value


def post_relative_delta(dx, dy):
    """Post one relative mouse move.

    Minecraft on macOS reads kCGMouseEventDeltaX/Y, not a new cursor
    coordinate. The desktop cursor is moved by the same amount so a test
    outside the game is visible too. The point is kept on the main display
    so the cursor does not stick at the screen edge.
    """
    import Quartz

    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateCombinedSessionState)
    current = Quartz.CGEventCreate(source)
    point = Quartz.CGEventGetLocation(current)
    width = Quartz.CGDisplayPixelsWide(Quartz.CGMainDisplayID())
    height = Quartz.CGDisplayPixelsHigh(Quartz.CGMainDisplayID())
    point.x = min(max(point.x + float(dx), 1.0), float(width) - 2.0)
    point.y = min(max(point.y + float(dy), 1.0), float(height) - 2.0)
    event = Quartz.CGEventCreateMouseEvent(source, Quartz.kCGEventMouseMoved, point, 0)
    Quartz.CGEventSetDoubleValueField(event, Quartz.kCGMouseEventDeltaX, float(dx))
    Quartz.CGEventSetDoubleValueField(event, Quartz.kCGMouseEventDeltaY, float(dy))
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    Quartz.CGEventPost(Quartz.kCGSessionEventTap, event)


def _print(message):
    print(message, flush=True)
