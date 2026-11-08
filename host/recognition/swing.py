"""Require a deliberate arc for an armed mouse click, not a single gyro spike.

Gyro is bias-corrected deg/s. Net rotation prevents a small back-and-forth
shake from adding up to a large swing. All timing comes from sample arrivals;
no sleeps or assumed sample rate are used.
"""

import math

MIN_ROTATION_DEG = 60.0
MIN_PEAK_DPS = 200.0
MIN_DURATION_S = 0.12
MAX_DURATION_S = 0.8
START_DPS = 80.0
QUIET_DPS = 35.0
MAX_SAMPLE_GAP_S = 0.1


class SwingGate:
    def __init__(self):
        self.reset()

    def reset(self):
        self._ready = False
        self._last_s = None
        self._clear_stroke()

    def _clear_stroke(self):
        self._started_s = None
        self._rotation = [0.0, 0.0, 0.0]
        self._peak_dps = 0.0

    def update(self, gyro, now_s):
        """Return True once a large, quick stroke qualifies after a quiet sample."""
        dt = 0.0 if self._last_s is None else now_s - self._last_s
        if self._last_s is not None and (dt <= 0.0 or dt > MAX_SAMPLE_GAP_S):
            self.reset()
            dt = 0.0
        self._last_s = now_s
        speed = math.sqrt(sum(value * value for value in gyro))
        if speed < QUIET_DPS:
            self._clear_stroke()
            self._ready = True
            return False
        if not self._ready:
            return False
        if self._started_s is None:
            if speed < START_DPS:
                return False
            self._started_s = now_s
        elapsed = now_s - self._started_s
        if elapsed > MAX_DURATION_S:
            self.reset()  # another attempt must start from quiet
            return False
        for index, value in enumerate(gyro):
            self._rotation[index] += value * dt
        self._peak_dps = max(self._peak_dps, speed)
        rotation_deg = math.sqrt(sum(value * value for value in self._rotation))
        if (rotation_deg >= MIN_ROTATION_DEG and self._peak_dps >= MIN_PEAK_DPS
                and elapsed >= MIN_DURATION_S):
            self.reset()
            return True
        return False
