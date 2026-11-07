"""Motion features for one recording.

Gyro values are deg/s. Acceleration is g. Angular displacement is the sum of
bias-subtracted gyro over this recording only, in degrees. It is not a
position in the room, and it is not carried into the next recording.
"""

import math

from sensor.features import accel_mag, gyro_mag

AXES = ("gx", "gy", "gz")


class MotionError(Exception):
    """A recording has no usable motion, or the axes cannot be told apart."""


def gyro_bias(samples):
    """Mean gyro (deg/s) while the toy is resting."""
    if not samples:
        return (0.0, 0.0, 0.0)
    totals = [0.0, 0.0, 0.0]
    for sample in samples:
        for index in range(3):
            totals[index] += sample[3 + index]
    count = float(len(samples))
    return tuple(total / count for total in totals)


def gyro_noise(samples, bias):
    """Typical resting gyro magnitude after bias removal, in deg/s."""
    if not samples:
        return 0.0
    total = 0.0
    for sample in samples:
        total += _corrected_mag(sample, bias)
    return total / float(len(samples))


def axis_means(samples, bias, onset_dps):
    """Mean bias-subtracted gyro during samples that are actually moving."""
    totals = [0.0, 0.0, 0.0]
    count = 0
    for sample in samples:
        corrected = _corrected(sample, bias)
        if _mag(corrected) < onset_dps:
            continue
        for index in range(3):
            totals[index] += corrected[index]
        count += 1
    if count == 0:
        return None
    return [total / float(count) for total in totals]


def detection_thresholds(noise_dps):
    """Onset and release thresholds in deg/s from measured resting noise.

    Onset is higher than release so a brief dip does not end the movement.
    The onset is capped so a slow intentional move is not rejected just
    because the resting noise was quiet, and floored so rest jitter is not
    treated as a move.
    """
    onset = max(8.0, min(35.0, float(noise_dps) * 5.0))
    release = max(5.0, onset * 0.45)
    return onset, release


def motion_features(samples, bias, hz, onset_dps, times=None):
    """Features for one take. samples are raw CSV tuples.

    When times is present, angular displacement uses the real gap between
    samples. A steady 50 Hz stream and a slightly uneven one then describe
    the same physical turn.
    """
    if not samples:
        raise MotionError("No samples were recorded.")
    if hz <= 0:
        raise MotionError("Sample rate is not valid.")
    nominal = 1.0 / float(hz)
    rates = []
    accels = []
    displacement = [0.0, 0.0, 0.0]
    for index, sample in enumerate(samples):
        if not _finite_sample(sample):
            raise MotionError("A sample contained an invalid number.")
        if times is None or index == 0:
            dt = nominal
        else:
            dt = times[index] - times[index - 1]
            if dt <= 0.0 or dt > 0.1:
                dt = nominal
        corrected = _corrected(sample, bias)
        rates.append(_mag(corrected))
        accels.append(accel_mag(sample))
        for axis in range(3):
            displacement[axis] += corrected[axis] * dt
    onset, end = _active_bounds(rates, onset_dps)
    active = rates[onset:end] if end > onset else []
    peak_rate = max(rates) if rates else 0.0
    mean_rate = _mean(active) if active else 0.0
    peak_accel = max(accels) if accels else 0.0
    mean_accel = _mean(accels)
    travel = _mag(displacement)
    return {
        "duration_s": len(samples) * dt,
        "onset_index": onset,
        "end_index": end,
        "peak_angular_velocity_dps": peak_rate,
        "mean_angular_velocity_dps": mean_rate,
        "angular_displacement_deg": displacement,
        "travel_deg": travel,
        "peak_acceleration_g": peak_accel,
        "mean_acceleration_g": mean_accel,
        "smoothness": _smoothness(active),
        "intentional": peak_rate >= onset_dps,
        "axis_means_dps": axis_means(samples, bias, onset_dps),
    }


def learn_mount(recordings, bias, onset_dps, min_confidence=0.55):
    """Pick the gyro axis for left/right and the gyro axis for up/down.

    recordings maps "left", "right", "up", and "down" to sample lists.
    Right becomes positive pointer X. Up becomes pointer-up, which is
    negative screen Y, so the vertical axis is inverted when the chip's
    positive direction points up.
    """
    means = {}
    for name in ("left", "right", "up", "down"):
        samples = recordings.get(name) or []
        found = axis_means(samples, bias, onset_dps)
        if found is None:
            raise MotionError("The %s recording did not contain a movement." % name)
        means[name] = found
    horizontal, right_sign, h_confidence = _opposing_axis(means["left"], means["right"])
    vertical, down_sign, v_confidence = _opposing_axis(means["up"], means["down"])
    if horizontal is None or vertical is None:
        raise MotionError(
            "Left/right or up/down did not land on opposite gyro directions. "
            "Repeat those four moves more cleanly."
        )
    if horizontal == vertical:
        raise MotionError(
            "Left/right and up/down both showed up on %s. "
            "The sensor cannot tell those directions apart in this mounting. "
            "Reposition the toy and run look again."
            % AXES[horizontal]
        )
    if h_confidence < min_confidence or v_confidence < min_confidence:
        raise MotionError(
            "The movement was spread across more than one axis "
            "(horizontal confidence %.0f%%, vertical %.0f%%). "
            "Move in a straighter line and run look again."
            % (h_confidence * 100.0, v_confidence * 100.0)
        )
    return {
        "horizontal_axis": AXES[horizontal],
        "vertical_axis": AXES[vertical],
        "invert_horizontal": right_sign < 0.0,
        "invert_vertical": down_sign < 0.0,
        "horizontal_confidence": h_confidence,
        "vertical_confidence": v_confidence,
    }


def classify_direction(horizontal_dps, vertical_dps, neutral_dps):
    """Screen-relative direction after axis mapping. neutral, left, right, up, down, or a diagonal."""
    horizontal_hot = abs(horizontal_dps) >= neutral_dps
    vertical_hot = abs(vertical_dps) >= neutral_dps
    if not horizontal_hot and not vertical_hot:
        return "neutral"
    horizontal_name = "right" if horizontal_dps > 0.0 else "left"
    vertical_name = "down" if vertical_dps > 0.0 else "up"
    if horizontal_hot and vertical_hot:
        return vertical_name + "-" + horizontal_name
    if horizontal_hot:
        return horizontal_name
    return vertical_name


def size_label(travel_deg, small_deg, large_deg):
    if small_deg is None or large_deg is None or large_deg <= small_deg:
        return "unscaled"
    midpoint = 0.5 * (small_deg + large_deg)
    return "small" if travel_deg < midpoint else "large"


def speed_label(peak_dps, slow_dps, fast_dps):
    if slow_dps is None or fast_dps is None or fast_dps <= slow_dps:
        return "unscaled"
    midpoint = 0.5 * (slow_dps + fast_dps)
    return "slow" if peak_dps < midpoint else "fast"


def _opposing_axis(negative_means, positive_means):
    """Axis where the two moves point opposite ways. Sign is the positive move's mean."""
    best = None
    best_score = 0.0
    best_sign = 0.0
    for index in range(3):
        if negative_means[index] * positive_means[index] >= 0.0:
            continue
        score = min(abs(negative_means[index]), abs(positive_means[index]))
        if score > best_score:
            best = index
            best_score = score
            best_sign = positive_means[index]
    if best is None:
        return None, 0.0, 0.0
    spread = abs(positive_means[0]) + abs(positive_means[1]) + abs(positive_means[2])
    confidence = 0.0 if spread <= 0.0 else abs(positive_means[best]) / spread
    return best, best_sign, confidence


def _corrected(sample, bias):
    return [sample[3] - bias[0], sample[4] - bias[1], sample[5] - bias[2]]


def _corrected_mag(sample, bias):
    return _mag(_corrected(sample, bias))


def _mag(vector):
    return math.sqrt(vector[0] * vector[0] + vector[1] * vector[1] + vector[2] * vector[2])


def _mean(values):
    if not values:
        return 0.0
    return sum(values) / float(len(values))


def _active_bounds(rates, onset_dps):
    start = 0
    while start < len(rates) and rates[start] < onset_dps:
        start += 1
    end = len(rates)
    while end > start and rates[end - 1] < onset_dps:
        end -= 1
    return start, end


def _smoothness(rates):
    if len(rates) < 3:
        return 1.0
    deltas = [abs(rates[index] - rates[index - 1]) for index in range(1, len(rates))]
    average = _mean(rates)
    if average <= 1.0:
        return 0.0
    return 1.0 / (1.0 + (_mean(deltas) / average))


def _finite_sample(sample):
    if len(sample) < 6:
        return False
    for value in sample[:6]:
        if value != value or value in (float("inf"), float("-inf")):
            return False
    return True


def raw_gyro_mag(sample):
    return gyro_mag(sample)
