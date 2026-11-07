"""Calibration and matching on synthetic swings, not the UDP stream."""

import math

from calibration import Calibrator
from config import load_config
from matcher import GestureTemplate, Matcher


def _swing(count, peak, threshold):
    """`count` moving samples, with still samples on both ends."""
    values = [0.0]
    for index in range(count):
        unit = (index + 1) / (count + 1)
        values.append(threshold + (peak - threshold) * math.sin(math.pi * unit))
    values.append(0.0)
    return values


def _feed(calibrator, values):
    for value in values:
        calibrator.push_sample(value, (value,))


def _moving_count(config):
    count = config.min_gesture_samples + 6
    if count > config.max_gesture_samples:
        count = config.max_gesture_samples
    return count


def test_three_similar_swings_produce_a_template_and_threshold():
    config = load_config()
    count = _moving_count(config)
    calibrator = Calibrator(config)
    for peak in (1.0, 0.97, 1.03):
        _feed(calibrator, _swing(count, peak, config.still_gyro_threshold))
    template = calibrator.build()
    assert len(template.samples) == count
    assert template.threshold > 0
    assert template.medoid_index in (0, 1, 2)


def test_similar_swing_matches_and_flat_or_unrelated_does_not():
    config = load_config()
    count = _moving_count(config)
    calibrator = Calibrator(config)
    for peak in (1.0, 0.97, 1.03):
        _feed(calibrator, _swing(count, peak, config.still_gyro_threshold))
    template = calibrator.build()
    matcher = Matcher(
        [GestureTemplate("swing", template.samples, template.threshold)],
        config,
    )
    similar = _swing(count, 1.01, config.still_gyro_threshold)[1:-1]
    flat = [0.0] * count
    unrelated = [config.still_gyro_threshold + 1.0] * (count // 2) + [0.5] * (
        count - count // 2
    )
    later = config.refractory_ms * 4
    assert matcher.match(similar, now_ms=0) == "swing"
    assert matcher.match(flat, now_ms=later) is None
    assert matcher.match(unrelated, now_ms=later + config.refractory_ms) is None


def test_second_match_inside_refractory_period_does_not_fire():
    config = load_config()
    count = _moving_count(config)
    calibrator = Calibrator(config)
    for peak in (1.0, 0.97, 1.03):
        _feed(calibrator, _swing(count, peak, config.still_gyro_threshold))
    template = calibrator.build()
    matcher = Matcher(
        [GestureTemplate("swing", template.samples, template.threshold)],
        config,
    )
    similar = _swing(count, 1.01, config.still_gyro_threshold)[1:-1]
    assert matcher.match(similar, now_ms=0) == "swing"
    assert matcher.match(similar, now_ms=config.refractory_ms / 2.0) is None
    assert matcher.match(similar, now_ms=config.refractory_ms) == "swing"


def test_down_module_does_not_match():
    config = load_config()
    count = _moving_count(config)
    calibrator = Calibrator(config)
    for peak in (1.0, 0.97, 1.03):
        _feed(calibrator, _swing(count, peak, config.still_gyro_threshold))
    template = calibrator.build()
    matcher = Matcher(
        [GestureTemplate("swing", template.samples, template.threshold)],
        config,
    )
    similar = _swing(count, 1.01, config.still_gyro_threshold)[1:-1]
    assert matcher.match(similar, now_ms=0, lower_down=True) is None
