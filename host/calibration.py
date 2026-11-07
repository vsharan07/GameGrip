"""Collect three swings and turn them into one template.

A window opens when gyro_mag rises above the still threshold and closes
when it falls back to still. The template is the medoid of the three
windows. The match threshold is the largest pairwise DTW distance
multiplied by the margin in config.
"""

from matcher import dtw_distance


class CalibrationError(Exception):
    """The three swings could not be turned into a template."""


class GestureTemplate(object):
    def __init__(self, samples, threshold, medoid_index):
        self.samples = samples
        self.threshold = threshold
        self.medoid_index = medoid_index


class Calibrator(object):
    def __init__(self, config):
        self._config = config
        self.repetitions = []
        self.rejection = None
        self._active = []
        self._in_motion = False
        self._wait_for_still = False
        self._attempt = 0

    def push_frame(self, frame):
        """Segment one aligned frame. Partial frames are ignored."""
        if frame.missing_module is not None:
            return
        if frame.gyro_upper is None or frame.gyro_lower is None:
            return
        if frame.twist_deg is None:
            return
        gyro_mag = max(frame.gyro_upper, frame.gyro_lower)
        self.push_sample(
            gyro_mag,
            (
                frame.twist_deg,
                frame.gyro_upper,
                frame.gyro_lower,
                frame.accel_upper,
                frame.accel_lower,
            ),
        )

    def push_sample(self, gyro_mag, vector=None):
        """Add one sample. `vector` defaults to the gyro value itself."""
        if self.rejection is not None:
            return
        if vector is None:
            sample = (float(gyro_mag),)
        else:
            sample = tuple(float(value) for value in vector)
        moving = gyro_mag > self._config.still_gyro_threshold
        if self._wait_for_still:
            if not moving:
                self._wait_for_still = False
            return
        if not self._in_motion:
            if moving:
                self._in_motion = True
                self._active = [sample]
            return
        if moving:
            self._active.append(sample)
            if len(self._active) > self._config.max_gesture_samples:
                self._attempt += 1
                self.rejection = (
                    "Swing %d was too long. Swing once, then hold your arm still."
                    % self._attempt
                )
                self._active = []
                self._in_motion = False
                self._wait_for_still = True
            return
        self._close_window()

    def build(self):
        """Return a GestureTemplate, or raise CalibrationError in plain language."""
        if self.rejection is not None:
            raise CalibrationError(self.rejection)
        if self._in_motion:
            raise CalibrationError(
                "Swing %d never came back to still. Finish the swing and hold your arm still."
                % (self._attempt + 1)
            )
        if len(self.repetitions) < 3:
            raise CalibrationError(
                "Only %d complete swing(s) were captured. Three swings are required."
                % len(self.repetitions)
            )
        return medoid_template(self.repetitions[:3], self._config)

    def _close_window(self):
        samples = list(self._active)
        self._active = []
        self._in_motion = False
        self._attempt += 1
        if len(samples) < self._config.min_gesture_samples:
            self.rejection = (
                "Swing %d was too short. Make one full swing, then hold your arm still."
                % self._attempt
            )
            return
        self.repetitions.append(samples)


def medoid_template(repetitions, config):
    """Pick the repetition closest to the other two and scale its spread by the margin."""
    band = config.dtw_window_samples
    pair_distance = {}
    for left, right in ((0, 1), (0, 2), (1, 2)):
        pair_distance[(left, right)] = dtw_distance(
            repetitions[left], repetitions[right], band
        )
    totals = []
    for index in range(3):
        total = 0.0
        for other in range(3):
            if other == index:
                continue
            key = (index, other) if index < other else (other, index)
            total += pair_distance[key]
        totals.append(total)
    medoid_index = min(range(3), key=lambda index: (totals[index], index))
    largest = max(pair_distance.values())
    threshold = largest * config.match_distance_margin
    return GestureTemplate(repetitions[medoid_index], threshold, medoid_index)
