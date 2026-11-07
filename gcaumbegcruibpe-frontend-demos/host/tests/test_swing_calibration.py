"""Saved three-swing calibration. No serial port."""

from recognition.rotation import DetectorSettings
from swing_calibration import (
    FORMAT_VERSION,
    CalibrationFormatError,
    build_calibration,
    load_calibration,
    save_calibration,
    tracker_from_calibration,
)

import pytest


def _settings():
    return DetectorSettings(debug=False, sensitivity=1.0, ignore_backswing=True)


def test_three_swings_round_trip_and_then_fire(tmp_path):
    rest = [(0.0, 0.0, 0.0)] * 40
    swing = [(180.0, 0.0, 0.0)] * 40
    data = build_calibration(rest, [swing, swing, swing], 50.0, _settings())
    assert data["version"] == FORMAT_VERSION
    assert data["units"]["gyro"] == "deg/s"
    assert data["full_swing_deg"] > 0.0
    path = tmp_path / "swing_calibration.json"
    save_calibration(data, path)
    loaded = load_calibration(path)
    tracker = tracker_from_calibration(loaded, _settings())
    assert any(tracker.update((180.0, 0.0, 0.0)) for _ in range(80))


def test_old_calibration_version_is_rejected(tmp_path):
    path = tmp_path / "swing_calibration.json"
    path.write_text('{"version": 0}\n', encoding="utf-8")
    with pytest.raises(CalibrationFormatError):
        load_calibration(path)


def test_a_still_swing_is_rejected():
    rest = [(20.0, 0.0, 0.0)] * 40
    swing = [(20.0, 0.0, 0.0)] * 20
    with pytest.raises(Exception):
        build_calibration(rest, [swing, swing, swing], 50.0, _settings())
