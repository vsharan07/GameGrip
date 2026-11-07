"""DTW matching against stored gesture templates.

The Sakoe-Chiba band and the refractory period both come from config.
A module that is DOWN cannot fire a gesture.
"""

import math


class GestureTemplate(object):
    def __init__(self, name, samples, threshold):
        self.name = name
        self.samples = as_series(samples)
        self.threshold = float(threshold)


class Matcher(object):
    def __init__(self, templates, config):
        self._templates = list(templates)
        self._band = config.dtw_window_samples
        self._refractory_ms = config.refractory_ms
        self._last_fire_ms = {}

    def match(self, window, now_ms, upper_down=False, lower_down=False):
        """Return the gesture name, or None when nothing should fire."""
        if upper_down or lower_down:
            return None
        series = as_series(window)
        if not series:
            return None
        best_distance = None
        best = None
        for template in self._templates:
            distance = dtw_distance(series, template.samples, self._band)
            # Equal distance is the template itself. Identical calibration
            # swings produce a threshold of 0, and that exact swing must still fire.
            if distance <= template.threshold and (
                best_distance is None or distance < best_distance
            ):
                best_distance = distance
                best = template
        if best is None:
            return None
        previous = self._last_fire_ms.get(best.name)
        if previous is not None and (now_ms - previous) < self._refractory_ms:
            return None
        self._last_fire_ms[best.name] = now_ms
        return best.name


def dtw_distance(left, right, band):
    """DTW distance. Cells farther than `band` samples from the diagonal are forbidden."""
    series_a = as_series(left)
    series_b = as_series(right)
    length_a = len(series_a)
    length_b = len(series_b)
    if length_a == 0 or length_b == 0 or abs(length_a - length_b) > band:
        return float("inf")
    infinity = float("inf")
    costs = [[infinity] * (length_b + 1) for _ in range(length_a + 1)]
    costs[0][0] = 0.0
    for i in range(1, length_a + 1):
        j_start = max(1, i - band)
        j_stop = min(length_b, i + band)
        for j in range(j_start, j_stop + 1):
            step = _point_distance(series_a[i - 1], series_b[j - 1])
            costs[i][j] = step + min(
                costs[i - 1][j],
                costs[i][j - 1],
                costs[i - 1][j - 1],
            )
    return costs[length_a][length_b]


def as_series(window):
    """Accept a list of numbers or a list of feature vectors."""
    if not window:
        return []
    first = window[0]
    if isinstance(first, (int, float)):
        return [(float(value),) for value in window]
    return [tuple(float(value) for value in row) for row in window]


def _point_distance(left, right):
    if len(left) != len(right):
        raise ValueError("feature vectors have different lengths")
    return math.sqrt(sum((a - b) * (a - b) for a, b in zip(left, right)))
