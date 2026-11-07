"""Gyroscope rotation-threshold detector.

This is the live swing test from the serial calibrator. Rotation is the
sum of gyro speed while the sensor is moving, in degrees. It is not a
Dynamic Time Warping template match. host/matcher.py is the separate,
older DTW matcher and is not called from here.

Gyro samples are deg/s. Gravity does not enter the test.
"""

import math

# The swing is the earliest stroke that is nearly as fast as the fastest one.
_EARLIEST_FAST_FRACTION = 0.75


def vadd(left, right):
    return (left[0] + right[0], left[1] + right[1], left[2] + right[2])


def vsub(left, right):
    return (left[0] - right[0], left[1] - right[1], left[2] - right[2])


def vscale(vector, scale):
    return (vector[0] * scale, vector[1] * scale, vector[2] * scale)


def vdot(left, right):
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]


def vnorm(vector):
    return math.sqrt(vdot(vector, vector))


def vmean(rows):
    count = float(len(rows))
    return (
        sum(row[0] for row in rows) / count,
        sum(row[1] for row in rows) / count,
        sum(row[2] for row in rows) / count,
    )


def vstd_speed(speeds):
    count = float(len(speeds))
    if count == 0:
        return 0.0
    mean = sum(speeds) / count
    var = sum((speed - mean) ** 2 for speed in speeds) / count
    return math.sqrt(var)


class DetectorSettings(object):
    def __init__(
        self,
        sensitivity=0.85,
        ignore_backswing=True,
        backswing_angle_deg=120.0,
        slow_fraction=0.08,
        forget_seconds=0.4,
        min_gap_seconds=0.15,
        alpha=0.5,
        rest_seconds=2.0,
        rest_max_noise_dps=6.0,
        rest_attempts=3,
        swing_window_seconds=3.0,
        debug=True,
        debug_min_fraction=0.25,
        stale_timeout_s=1.0,
    ):
        if not 90.0 < float(backswing_angle_deg) <= 180.0:
            raise ValueError("backswing_angle_deg must be between 90 and 180")
        if float(sensitivity) <= 0.0:
            raise ValueError("sensitivity must be positive")
        self.sensitivity = float(sensitivity)
        self.ignore_backswing = bool(ignore_backswing)
        self.backswing_angle_deg = float(backswing_angle_deg)
        self.slow_fraction = float(slow_fraction)
        self.forget_seconds = float(forget_seconds)
        self.min_gap_seconds = float(min_gap_seconds)
        self.alpha = float(alpha)
        self.rest_seconds = float(rest_seconds)
        self.rest_max_noise_dps = float(rest_max_noise_dps)
        self.rest_attempts = int(rest_attempts)
        self.swing_window_seconds = float(swing_window_seconds)
        self.debug = bool(debug)
        self.debug_min_fraction = float(debug_min_fraction)
        self.stale_timeout_s = float(stale_timeout_s)

    @property
    def swing_fraction(self):
        # 1.0 sensitivity fires after about half of a calibration swing.
        return 0.5 / self.sensitivity

    @property
    def backswing_cos(self):
        return math.cos(math.radians(self.backswing_angle_deg))


def counted_speed(smooth, speed, swing_axis, backswing_cos):
    """Speed that counts toward a swing. Reverse motion counts as zero."""
    if swing_axis is not None and speed > 0.0:
        if vdot(smooth, swing_axis) / speed < backswing_cos:
            return 0.0
    return speed


class RotationTracker(object):
    """Adds up rotation and fires once per swing, then waits out the gap."""

    def __init__(self, bias, speed_gate, full_swing_deg, hz, swing_axis, settings, output=None):
        self.bias = tuple(bias)
        self.speed_gate = float(speed_gate)
        self.full_swing_deg = float(full_swing_deg)
        self.swing_axis = None if swing_axis is None else tuple(swing_axis)
        self.settings = settings
        self.hz = float(hz)
        self.dt = 1.0 / self.hz
        self.swing_deg = settings.swing_fraction * self.full_swing_deg
        self.forget_samples = max(int(settings.forget_seconds * self.hz), 1)
        self.gap_samples = max(int(settings.min_gap_seconds * self.hz), 1)
        self._output = output if output is not None else _print
        self.reset()

    def reset(self):
        self.smooth = (0.0, 0.0, 0.0)
        self.angle = 0.0
        self.still = 0
        self.since_fire = 10 ** 9
        self.stroke_vec = (0.0, 0.0, 0.0)
        self.move = None

    def update(self, gyro):
        """Feed one gyro triple in deg/s. True when a swing crosses the line."""
        raw = tuple(gyro)
        unbiased = vsub(raw, self.bias)
        alpha = self.settings.alpha
        self.smooth = vadd(vscale(unbiased, alpha), vscale(self.smooth, 1.0 - alpha))
        speed = vnorm(self.smooth)
        self.since_fire += 1
        fired = False
        settings = self.settings

        if speed > self.speed_gate:
            if vdot(self.smooth, self.stroke_vec) < 0.0:
                self.angle = max(self.angle, 0.0)
                self.stroke_vec = (0.0, 0.0, 0.0)
            self.stroke_vec = vadd(self.stroke_vec, self.smooth)
            counted = counted_speed(
                self.smooth, speed, self.swing_axis, settings.backswing_cos
            )
            self.angle += counted * self.dt
            self.still = 0
            if settings.debug:
                self._track(raw, speed, counted)
        else:
            self.still += 1
            if self.move is not None:
                self.move["samples"] += 1
            if self.still >= self.forget_samples:
                self.angle = 0.0
                self.stroke_vec = (0.0, 0.0, 0.0)
                self._finish_movement()

        if self.angle >= self.swing_deg:
            if self.since_fire >= self.gap_samples:
                if self.move is not None:
                    self.move["fires"] += 1
                    self.move["best"] = 0.0
                # The rest of this same swing must not fire a second time.
                self.angle = -max(self.full_swing_deg - self.swing_deg, 0.0)
                self.since_fire = 0
                fired = True
            elif self.move is not None:
                self.move["gap_blocked"] += 1

        if self.move is not None:
            self.move["best"] = max(self.move["best"], self.angle)
        return fired

    def _track(self, raw, speed, counted):
        move = self.move
        if move is None:
            move = {
                "samples": 0,
                "peak": 0.0,
                "axis_peak": 0.0,
                "at_peak": 0,
                "total": 0.0,
                "counted": 0.0,
                "fires": 0,
                "gap_blocked": 0,
                "best": self.angle,
            }
            self.move = move
        move["samples"] += 1
        move["peak"] = max(move["peak"], speed)
        move["total"] += speed * self.dt
        move["counted"] += counted * self.dt
        peak_axis = max(abs(raw[0]), abs(raw[1]), abs(raw[2]))
        if peak_axis > move["axis_peak"] + 0.05:
            move["axis_peak"] = peak_axis
            move["at_peak"] = 1
        elif peak_axis >= move["axis_peak"] - 0.05:
            move["at_peak"] += 1

    def _finish_movement(self):
        move = self.move
        self.move = None
        if move is None or not self.settings.debug:
            return
        if move["total"] < self.settings.debug_min_fraction * self.swing_deg:
            return
        duration = (move["samples"] - self.forget_samples) * self.dt
        ignored = move["total"] - move["counted"]
        if move["fires"] == 0:
            progress = " | best %.0f / need %.0f" % (move["best"], self.swing_deg)
        else:
            progress = " | need %.0f per swing" % self.swing_deg
        line = (
            "[move %.2fs] swings=%d | peak %.0f dps | rot %.0f = counted %.0f + backswing-ignored %.0f%s"
            % (
                duration,
                move["fires"],
                move["peak"],
                move["total"],
                move["counted"],
                ignored,
                progress,
            )
        )
        if move["gap_blocked"]:
            line += " | gap-blocked %d" % move["gap_blocked"]
        notes = []
        if move["at_peak"] >= 3 and move["axis_peak"] > 100.0:
            notes.append("GYRO MAXED OUT at %.0f dps" % move["axis_peak"])
        if move["fires"] == 0 and move["best"] >= 0.6 * self.swing_deg:
            notes.append("NEAR MISS (raise sensitivity)")
        if move["fires"] == 0 and ignored > move["counted"]:
            notes.append("mostly ignored as backswing (raise backswing_angle_deg)")
        if notes:
            line += "  <<< " + "; ".join(notes)
        self._output(line)


def smoothed_speeds(gyro_rows, bias, alpha):
    smooth = (0.0, 0.0, 0.0)
    speeds = []
    for gyro in gyro_rows:
        smooth = vadd(vscale(vsub(gyro, bias), alpha), vscale(smooth, 1.0 - alpha))
        speeds.append(vnorm(smooth))
    return speeds


def main_stroke(gyro_rows, bias, speed_gate, hz, alpha):
    """Largest early stroke in one recording. Returns (degrees, unit axis or None)."""
    smooth = (0.0, 0.0, 0.0)
    strokes = []
    rotation = 0.0
    direction = (0.0, 0.0, 0.0)
    peak = 0.0
    for gyro in gyro_rows:
        smooth = vadd(vscale(vsub(gyro, bias), alpha), vscale(smooth, 1.0 - alpha))
        speed = vnorm(smooth)
        reversed_dir = rotation > 0.0 and vdot(smooth, direction) < 0.0
        if speed <= speed_gate or reversed_dir:
            if rotation > 0.0:
                strokes.append((peak, rotation, direction))
            rotation = 0.0
            direction = (0.0, 0.0, 0.0)
            peak = 0.0
        if speed > speed_gate:
            rotation += speed / hz
            direction = vadd(direction, vscale(smooth, 1.0 / hz))
            peak = max(peak, speed)
    if rotation > 0.0:
        strokes.append((peak, rotation, direction))
    if not strokes:
        return 0.0, None
    fastest = max(item[0] for item in strokes)
    for item in strokes:
        if item[0] >= _EARLIEST_FAST_FRACTION * fastest:
            axis_norm = vnorm(item[2])
            axis = None if axis_norm == 0.0 else vscale(item[2], 1.0 / axis_norm)
            return item[1], axis
    return 0.0, None


def flat_top(gyro_rows):
    """Value a saturated gyro is stuck on, or None."""
    top = 0.0
    hits = 0
    for gyro in gyro_rows:
        for component in gyro:
            magnitude = abs(component)
            if magnitude > top:
                top = magnitude
    if top <= 100.0:
        return None
    for gyro in gyro_rows:
        for component in gyro:
            if abs(component) >= top - 0.05:
                hits += 1
    if hits >= 3:
        return top
    return None


def _print(message):
    print(message, flush=True)
