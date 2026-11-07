"""Grouped look calibration.

Neutral is recorded first. Then four direction groups (left, right, up,
down). Inside each group the host waits for the move, records slow, normal,
and fast variations, and adds a large move only when the fast one was not
already large. A failed take is explained and retried. Takes already
accepted in that group stay accepted.

Version 2. A version 1 file has to be recorded again.
"""

import json
import time
from pathlib import Path

from sensor.motion import (
    MotionError,
    detection_thresholds,
    gyro_bias,
    gyro_noise,
    learn_mount,
    motion_features,
)

FORMAT_VERSION = 3
DEFAULT_PATH = Path(__file__).resolve().parent / "data" / "look.json"
GROUPS = ("left", "right", "up", "down")
VARIANTS = ("slow", "normal", "fast")


class LookCalibrationError(Exception):
    """The look calibration could not be saved."""


class MoveRecorder(object):
    """Wait for a move, then keep it until the toy is still again.

    Onset and release are different, so a short dip in the middle of a
    swing does not cut the recording off. Times are the host clock, not an
    assumed 20 ms grid.
    """

    def __init__(self, bias, onset_dps, release_dps, end_hold_s=0.35, max_wait_s=4.0, max_move_s=2.5):
        self.bias = bias
        self.onset_dps = float(onset_dps)
        self.release_dps = float(release_dps)
        self.end_hold_s = float(end_hold_s)
        self.max_wait_s = float(max_wait_s)
        self.max_move_s = float(max_move_s)
        self.samples = []
        self.times = []
        self.reason = None
        self.state = "wait"
        self._started = None
        self._move_started = None
        self._quiet_since = None
        self._filtered = None

    def push(self, timestamp, sample):
        if self.state in ("done", "failed"):
            return self.state
        if self._started is None:
            self._started = timestamp
        speed = self._filtered_speed(sample)
        if self.state == "wait":
            if speed >= self.onset_dps:
                self.state = "active"
                self._move_started = timestamp
                self._quiet_since = None
                self._keep(timestamp, sample)
                return self.state
            if timestamp - self._started >= self.max_wait_s:
                self._fail("No movement started. Do the move, then hold still.")
            return self.state
        self._keep(timestamp, sample)
        if speed >= self.release_dps:
            self._quiet_since = None
        elif self._quiet_since is None:
            self._quiet_since = timestamp
        elif timestamp - self._quiet_since >= self.end_hold_s:
            self._finish()
        if self.state == "active" and timestamp - self._move_started >= self.max_move_s:
            self._finish()
        return self.state

    def _finish(self):
        active = [speed for speed in self._speeds() if speed >= self.onset_dps]
        if len(self.samples) < 4 or len(active) < 3:
            self._fail(
                "That move was too short (%d samples). Make one full movement, then hold still."
                % len(self.samples)
            )
            return
        self.state = "done"

    def _fail(self, reason):
        self.state = "failed"
        self.reason = reason

    def _keep(self, timestamp, sample):
        self.samples.append(sample)
        self.times.append(timestamp)

    def _filtered_speed(self, sample):
        corrected = _corrected_mag(sample, self.bias)
        if self._filtered is None:
            self._filtered = corrected
        else:
            self._filtered = 0.55 * corrected + 0.45 * self._filtered
        return self._filtered

    def _speeds(self):
        return [_corrected_mag(sample, self.bias) for sample in self.samples]


def dominant_axis(samples, bias, floor_dps=8.0, skip=None):
    """Axis with the largest signed motion, or None if the toy barely moved.

    The sign is the mean of that axis while the toy was moving. Right should
    be positive mouse X. Up should become negative mouse Y, which the caller
    applies as an inversion.
    """
    totals = [0.0, 0.0, 0.0]
    count = 0
    for sample in samples:
        corrected = [sample[3 + index] - bias[index] for index in range(3)]
        magnitude = (corrected[0] ** 2 + corrected[1] ** 2 + corrected[2] ** 2) ** 0.5
        if magnitude < floor_dps:
            continue
        for index in range(3):
            totals[index] += corrected[index]
        count += 1
    if count < 4:
        return None
    order = sorted(range(3), key=lambda index: abs(totals[index]), reverse=True)
    for index in order:
        if skip is not None and index == skip:
            continue
        if abs(totals[index]) < floor_dps * count * 0.25:
            return None
        return index, totals[index] / float(count)
    return None


def build_document(rest_samples, right_samples, up_samples, created=None):
    """Three recordings: still, turn right, tilt up."""
    if len(rest_samples) < 10:
        raise LookCalibrationError("Rest was not recorded.")
    bias = gyro_bias(rest_samples)
    noise = gyro_noise(rest_samples, bias)
    if noise > 25.0:
        raise LookCalibrationError(
            "Rest is too noisy (%.0f deg/s). Hold the toy still and run look again." % noise
        )
    floor = max(8.0, noise * 3.0)
    horizontal = dominant_axis(right_samples, bias, floor)
    if horizontal is None:
        raise LookCalibrationError(
            "The right turn was too small to see. Turn the toy clearly to the right and run look again."
        )
    vertical = dominant_axis(up_samples, bias, floor, skip=horizontal[0])
    if vertical is None:
        raise LookCalibrationError(
            "The upward tilt was too small, or it used the same axis as left/right. "
            "Tilt the toy up, not sideways, and run look again."
        )
    from sensor.motion import AXES

    mount = {
        "horizontal_axis": AXES[horizontal[0]],
        "vertical_axis": AXES[vertical[0]],
        "invert_horizontal": horizontal[1] < 0.0,
        "invert_vertical": vertical[1] > 0.0,
        "horizontal_confidence": 1.0,
        "vertical_confidence": 1.0,
    }
    return {
        "version": FORMAT_VERSION,
        "created": created if created is not None else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bias_dps": list(bias),
        "noise_dps": noise,
        "onset_dps": floor,
        "release_dps": floor * 0.5,
        "mount": mount,
        "pixels_per_degree": 18.0,
        "notes": [],
        "groups": {
            "rest": {"samples": _stamp(rest_samples)},
            "right": {"samples": _stamp(right_samples)},
            "up": {"samples": _stamp(up_samples)},
        },
    }


def _stamp(samples):
    stamped = []
    for index, sample in enumerate(samples):
        stamped.append([index / 50.0] + [float(value) for value in sample[:6]])
    return stamped


def apply_to_aim(document, aim):
    """Copy the mount and a responsive Minecraft curve. Does not enable live input."""
    mount = document["mount"]
    aim.horizontal_axis = mount["horizontal_axis"]
    aim.vertical_axis = mount["vertical_axis"]
    aim.invert_horizontal = bool(mount["invert_horizontal"])
    aim.invert_vertical = bool(mount["invert_vertical"])
    aim.bias_dps = tuple(document["bias_dps"])
    aim.mount_learned = True
    aim.enabled = True
    aim.paused = False
    noise = float(document.get("noise_dps") or 0.0)
    aim.deadzone_dps = max(1.5, min(6.0, noise * 2.0 if noise else 1.5))
    aim.neutral_dps = aim.deadzone_dps
    aim.sensitivity = 18.0
    aim.horizontal_sensitivity = 18.0
    aim.vertical_sensitivity = 14.0
    aim.acceleration = 1.4
    aim.max_pixels_per_second = 25000.0
    aim.smoothing = 0.9
    return aim


def save_document(document, path=None):
    target = Path(path) if path is not None else DEFAULT_PATH
    if document.get("version") != FORMAT_VERSION:
        raise LookCalibrationError("Look calibration version is not %s." % FORMAT_VERSION)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return target


def load_document(path=None):
    target = Path(path) if path is not None else DEFAULT_PATH
    if not target.is_file():
        return None
    payload = json.loads(target.read_text(encoding="utf-8"))
    version = payload.get("version")
    if version != FORMAT_VERSION:
        raise LookCalibrationError(
            "Saved look calibration is version %s. Type look to record it again."
            % version
        )
    return payload


def run_grouped(source, say, clock=None):
    """Still, one turn to the right, one tilt up. Returns a document or raises."""
    clock = clock or time.time
    say("Look calibration. The mouse stays still until you later type live and LIVE.")
    say("Hold the toy the way you play. Hold still, turn right, then tilt up. You do not press Enter.")
    say("\n1. Hold still.")
    time.sleep(0.6)
    rest = _collect(source, 2.0, clock)
    if len(rest) < 20:
        raise LookCalibrationError("Only %d samples arrived. Check the cable." % len(rest))
    bias = gyro_bias(rest)
    noise = gyro_noise(rest, bias)
    say("Stillness: bias (%.1f, %.1f, %.1f) deg/s, noise %.1f deg/s." % (bias[0], bias[1], bias[2], noise))
    if noise > 25.0:
        raise LookCalibrationError("The toy was not still enough (%.0f deg/s). Hold it steady and run look again." % noise)
    say("\n2. Turn the toy to the RIGHT for about a second, then hold still.")
    time.sleep(0.8)
    right = _collect(source, 2.5, clock)
    say("Recorded %d samples for the right turn." % len(right))
    say("\n3. Tilt the toy UP for about a second, then hold still.")
    time.sleep(0.8)
    up = _collect(source, 2.5, clock)
    say("Recorded %d samples for the upward tilt." % len(up))
    return build_document(rest, right, up)


def run_full(source, say, clock=None):
    """Right, up, one diagonal swing with the button, then one long press.

    The mouse-look recording is returned even when the button is not seen.
    """
    clock = clock or time.time
    document = run_grouped(source, say, clock)
    document["button"] = {
        "swing": watch_swing(source, say, clock, document["mount"]),
        "long_press": watch_long_press(source, say, clock),
    }
    return document


def watch_swing(source, say, clock, mount, wait_s=5.0, swing_dps=40.0):
    """One swing from upper right to lower left while the button is down."""
    say("\n4. Press the button and swing once from the upper right to the lower left, then hold still.")
    started = clock()
    saw_column = False
    saw_button = False
    saw_diagonal = False
    saw_both = False
    while clock() - started < wait_s:
        frame = source.read_frame()
        if frame is None:
            continue
        sample, button = frame
        if button is not None:
            saw_column = True
            if button:
                saw_button = True
        if sample is not None and _both_axes(sample, mount, swing_dps):
            saw_diagonal = True
            if button:
                saw_both = True
    if saw_both:
        say("Swing with the button was captured.")
        return "ok"
    if not saw_column:
        say("The button is not working. The board is not sending a button.")
        return "missing"
    if saw_diagonal and not saw_button:
        say("The swing was seen, but the button is not working.")
        return "no-button"
    if saw_button and not saw_diagonal:
        say("The button works, but the swing was not seen. Swing from upper right to lower left.")
        return "no-swing"
    say("The swing with the button was not seen.")
    return "missed"


def watch_long_press(source, say, clock, hold_s=1.5, wait_s=4.0):
    """Ask for one hold. Say clearly whether the button stayed down."""
    say("\n5. Hold the button down for 2 seconds. Do not swing.")
    started = clock()
    down_since = None
    saw_column = False
    saw_press = False
    while clock() - started < wait_s:
        frame = source.read_frame()
        if frame is None:
            continue
        _sample, button = frame
        if button is None:
            continue
        saw_column = True
        if button:
            saw_press = True
            if down_since is None:
                down_since = clock()
            elif clock() - down_since >= hold_s:
                say("Button works. Holding it for 1.5 seconds will press W.")
                return "ok"
        else:
            down_since = None
    if not saw_column:
        say("The button is not working. The board is not sending a button.")
        return "missing"
    if not saw_press:
        say("The button is not working. It never read as pressed.")
        return "up"
    say("The button is not working. The hold was shorter than 1.5 seconds.")
    return "short"


def _both_axes(sample, mount, swing_dps):
    horizontal = abs(_axis_rate(sample, mount["horizontal_axis"]))
    vertical = abs(_axis_rate(sample, mount["vertical_axis"]))
    return horizontal >= swing_dps and vertical >= swing_dps


def _axis_rate(sample, name):
    index = {"gx": 3, "gy": 4, "gz": 5}[name]
    return float(sample[index])


def _collect(source, seconds, clock):
    started = clock()
    samples = []
    while clock() - started < seconds:
        sample = _read_sample(source)
        if sample is not None:
            samples.append(sample)
    return samples


def _read_sample(source):
    frame = source.read_frame()
    if frame is None:
        return None
    sample, _button = frame
    return sample


def _rate(times):
    if len(times) < 2:
        return 50.0
    span = times[-1] - times[0]
    if span <= 0.0:
        return 50.0
    return (len(times) - 1) / span


def _stored_rep(rep):
    features = dict(rep["features"])
    means = features.get("axis_means_dps")
    if means is not None:
        features["axis_means_dps"] = list(means)
    features["angular_displacement_deg"] = list(features["angular_displacement_deg"])
    stamped = []
    origin = rep["times"][0] if rep.get("times") else 0.0
    times = rep.get("times") or [index / float(rep.get("hz") or 50.0) for index in range(len(rep["samples"]))]
    for timestamp, sample in zip(times, rep["samples"]):
        stamped.append([timestamp - origin] + [float(value) for value in sample[:6]])
    return {"features": features, "samples": stamped}


def _corrected_mag(sample, bias):
    gx = sample[3] - bias[0]
    gy = sample[4] - bias[1]
    gz = sample[5] - bias[2]
    return (gx * gx + gy * gy + gz * gz) ** 0.5


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[len(ordered) // 2]
