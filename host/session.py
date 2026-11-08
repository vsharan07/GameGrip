"""One sample in, at most one gesture event out.

Mouse look runs on every valid sample. A finished movement can also match a
gesture template. Look does not wait for that match, and a look that matches
no template does not click. Live output is off until the caller sets it.
"""

from controls import is_mouse_input, mouse_button
from input.mouse_aim import aim_delta, mapped_rates
from recognition.swing import SwingGate
from sensor.motion import classify_direction

# A shorter press is a click. Holding past this holds W until release.
CLICK_MAX_S = 0.3
DOUBLE_CLICK_S = 0.45
WALK_KEY = "w"


class PlaySession(object):
    def __init__(self, library, controls, recognizer, segmenter, mouse, keyboard, safety):
        self.library = library
        self.controls = controls
        self.recognizer = recognizer
        self.segmenter = segmenter
        self.mouse = mouse
        self.keyboard = keyboard
        self.safety = safety
        self.live = False
        self._last_sample_s = None
        self._button_down = False
        self._press_started = None
        self._walking = False
        self._last_short_s = None
        self._look_paused = False
        self._armed = None
        self._swing = SwingGate()
        self._motion_started_s = None
        self.look = {
            "direction": "neutral",
            "dx": 0.0,
            "dy": 0.0,
            "neutral": True,
            "rate_dps": 0.0,
        }

    def set_live(self, live):
        self.live = bool(live)
        self.mouse.set_live(self.live)
        self.keyboard.set_live(self.live)
        if not self.live or self.safety.stopped:
            self.release_inputs()

    def release_inputs(self):
        try:
            self.mouse.release_all()
        finally:
            self.keyboard.release_all()
        self._button_down = False
        self._press_started = None
        self._walking = False
        self._last_short_s = None
        self._armed = None
        self._look_paused = False
        self._swing.reset()

    def push(self, sample, now_s, button=None):
        """Return a list of event dicts. Does not inject when stopped or dry-run."""
        events = []
        if self.safety.stopped:
            self.release_inputs()
            events.append({"type": "stopped"})
            return events
        if sample is None or not _finite_gyro(sample):
            self._swing.reset()
            self.mouse.reset_motion()
            self._set_look("neutral", 0.0, 0.0, 0.0, True)
            return events
        dt = 0.02 if self._last_sample_s is None else max(0.0, now_s - self._last_sample_s)
        self._last_sample_s = now_s
        if dt > 0.2:
            dt = 0.02
        if self._look_paused:
            _dx, _dy, self.mouse.smooth = aim_delta(sample, dt, self.controls.aim, self.mouse.smooth)
            dx, dy = 0.0, 0.0
        else:
            dx, dy = self.mouse.aim(sample, dt, self.controls.aim)
            if not self.controls.aim.enabled or self.controls.aim.paused:
                dx, dy = 0.0, 0.0
        direction, rate, neutral, gain, deadzone = _look_reading(sample, self.controls.aim, dx, dy)
        self._set_look(direction, dx, dy, rate, neutral, gain, deadzone)
        self.look["gx"] = sample[3]
        self.look["gy"] = sample[4]
        self.look["gz"] = sample[5]
        self.look["paused"] = self._look_paused
        if self.controls.aim.enabled and not self.controls.aim.paused and not self._look_paused and (dx or dy):
            events.append({
                "type": "aim",
                "dx": dx,
                "dy": dy,
                "live": self.live,
                "direction": direction,
            })
        if button is not None:
            events.extend(self._update_button(bool(button), now_s))
        events.extend(self._swing_click(sample, now_s))
        was_moving = self.segmenter._in_motion
        window = self.segmenter.push(sample)
        if self.segmenter._in_motion and not was_moving:
            self._motion_started_s = now_s
        if window is not None:
            started = self._motion_started_s if self._motion_started_s is not None else now_s
            decision = self.recognizer.consider(window, now_s * 1000.0)
            event = self._decision_event(decision)
            if decision.matched:
                action = self._action_for(decision.name, started, now_s)
                event["input"] = action
            events.append(event)
            if decision.matched:
                self._fire_action(event["input"], decision.name, events)
        return events

    def _decision_event(self, decision):
        binding = None if decision.name is None else self.controls.binding(decision.name)
        return {
            "type": "gesture",
            "name": decision.name,
            "distance": decision.distance,
            "threshold": decision.threshold,
            "reason": decision.reason,
            "input": binding,
            "runner_up": decision.runner_up,
        }

    def _action_for(self, gesture_id, started_s, ended_s):
        """Clicks come from a paused look plus a large swing, not from a template."""
        binding = self.controls.binding(gesture_id)
        if binding in ("left_click", "right_click"):
            return None
        return binding

    def _fire_action(self, binding, gesture_id, events):
        if not binding or self.safety.stopped:
            return
        if not self.live:
            events.append({"type": "dry-run", "input": binding, "gesture": gesture_id})
            return
        if is_mouse_input(binding):
            self.mouse.tap(mouse_button(binding))
        else:
            self.keyboard.tap(binding)
        events.append({"type": "injected", "input": binding, "gesture": gesture_id})

    def _update_button(self, pressed, now_s):
        events = []
        if pressed and not self._button_down:
            self._button_down = True
            self._press_started = now_s
            self._walking = False
            return events
        if pressed and self._button_down:
            if (not self._walking and self._press_started is not None
                    and (now_s - self._press_started) >= CLICK_MAX_S):
                self._walking = True
                # A long hold cancels an earlier click opportunity.
                self._armed = None
                self._look_paused = False
                self._last_short_s = None
                if self.live:
                    self.keyboard.press(WALK_KEY)
                events.append({"type": "injected" if self.live else "dry-run",
                               "input": WALK_KEY, "gesture": "hold"})
            return events
        if self._button_down:
            started = self._press_started
            was_walking = self._walking
            self._button_down = False
            self._press_started = None
            self._walking = False
            if was_walking:
                if self.live:
                    self.keyboard.release(WALK_KEY)
                events.append({"type": "released", "input": WALK_KEY, "gesture": "hold"})
            if not was_walking and started is not None and (now_s - started) < CLICK_MAX_S:
                self._count_click(now_s, events)
        return events

    def _count_click(self, now_s, events):
        second = self._last_short_s is not None and (now_s - self._last_short_s) <= DOUBLE_CLICK_S
        self._last_short_s = now_s
        self._arm("right" if second else "left", events)

    def _arm(self, which, events):
        self._look_paused = True
        self._armed = which
        self._swing.reset()
        events.append({"type": "paused", "input": which})

    def _swing_click(self, sample, now_s):
        events = []
        if self._armed is None or self._button_down or self.safety.stopped:
            self._swing.reset()
            return events
        gyro = tuple(sample[3 + index] - self.controls.aim.bias_dps[index] for index in range(3))
        if not self._swing.update(gyro, now_s):
            return events
        which = self._armed
        self._armed = None
        self._look_paused = False
        name = "left_click" if which == "left" else "right_click"
        if not self.live:
            events.append({"type": "dry-run", "input": name, "gesture": "swing"})
            return events
        self.mouse.tap(which)
        events.append({"type": "injected", "input": name, "gesture": "swing"})
        return events

    def _set_look(self, direction, dx, dy, rate, neutral, gain=1.0, deadzone=False):
        self.look = {
            "direction": direction,
            "dx": dx,
            "dy": dy,
            "neutral": neutral,
            "rate_dps": rate,
            "gain": gain,
            "deadzone": deadzone,
            "gx": self.look.get("gx", 0.0),
            "gy": self.look.get("gy", 0.0),
            "gz": self.look.get("gz", 0.0),
        }


def _look_reading(sample, aim, dx, dy):
    from input.mouse_aim import _curve

    horizontal, vertical = mapped_rates(sample, aim)
    rate = (horizontal * horizontal + vertical * vertical) ** 0.5
    direction = classify_direction(horizontal, vertical, aim.neutral_threshold())
    linear = max(abs(horizontal), abs(vertical), 1.0)
    shaped = max(abs(_curve(horizontal, aim.acceleration)), abs(_curve(vertical, aim.acceleration)), 0.0)
    gain = shaped / linear
    deadzone = direction == "neutral" or (dx == 0.0 and dy == 0.0 and rate < aim.deadzone_dps)
    return direction, rate, direction == "neutral" and dx == 0.0 and dy == 0.0, gain, deadzone


def _finite_gyro(sample):
    for value in sample[3:6]:
        if value != value or value in (float("inf"), float("-inf")):
            return False
    return True
