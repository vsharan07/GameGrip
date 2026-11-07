"""Split a stream into one movement.

Onset and the return to still decide the window. Naming the gesture is a
later step. Gyro magnitude is deg/s, matching the CSV stream.
"""

from sensor.features import gyro_mag


class MovementSegmenter(object):
    def __init__(self, onset_dps, still_dps, min_samples, max_samples, end_still_samples):
        self.onset_dps = float(onset_dps)
        self.still_dps = float(still_dps)
        self.min_samples = int(min_samples)
        self.max_samples = int(max_samples)
        self.end_still_samples = int(end_still_samples)
        self.rejection = None
        self._active = []
        self._in_motion = False
        self._still = 0
        self._wait_for_still = False

    def push(self, sample):
        """Return a finished window of raw samples, or None."""
        if sample is None:
            return None
        speed = gyro_mag(sample)
        if self._wait_for_still:
            if speed <= self.still_dps:
                self._wait_for_still = False
            return None
        if not self._in_motion:
            if speed >= self.onset_dps:
                self._in_motion = True
                self._still = 0
                self._active = [sample]
            return None
        if speed >= self.still_dps:
            self._active.append(sample)
            self._still = 0
            if len(self._active) > self.max_samples:
                self.rejection = "Movement was too long. Do it once, then hold still."
                self._active = []
                self._in_motion = False
                self._wait_for_still = True
            return None
        self._still += 1
        if self._still < self.end_still_samples:
            return None
        return self._close()

    def flush(self):
        """Close the open window. A recording that ends mid-swing still counts."""
        if not self._in_motion:
            return None
        return self._close()

    def _close(self):
        window = list(self._active)
        self._active = []
        self._in_motion = False
        self._still = 0
        if len(window) < self.min_samples:
            self.rejection = "Movement was too short to recognize."
            return None
        self.rejection = None
        return window
