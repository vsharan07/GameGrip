"""Rotation-threshold detector. This is not a DTW test."""

from recognition.rotation import DetectorSettings, RotationTracker


def _tracker(**kwargs):
    settings = DetectorSettings(debug=False, sensitivity=1.0, ignore_backswing=False, forget_seconds=10.0)
    for name, value in kwargs.items():
        setattr(settings, name, value)
    return RotationTracker(
        bias=(0.0, 0.0, 0.0),
        speed_gate=5.0,
        full_swing_deg=100.0,
        hz=50.0,
        swing_axis=None,
        settings=settings,
    )


def test_still_gyro_does_not_fire():
    tracker = _tracker()
    assert tracker.update((0.0, 0.0, 0.0)) is False
    assert tracker.update((1.0, 0.0, 0.0)) is False


def test_a_sustained_rotation_fires_and_then_waits():
    tracker = _tracker(min_gap_seconds=0.5)
    fires = []
    for index in range(80):
        if tracker.update((200.0, 0.0, 0.0)):
            fires.append(index)
    assert fires
    assert fires[0] > 5
    if len(fires) > 1:
        assert fires[1] - fires[0] >= tracker.gap_samples


def test_reverse_rotation_is_ignored_when_an_axis_is_set():
    settings = DetectorSettings(debug=False, sensitivity=1.0, ignore_backswing=True, forget_seconds=10.0)
    tracker = RotationTracker(
        bias=(0.0, 0.0, 0.0),
        speed_gate=5.0,
        full_swing_deg=100.0,
        hz=50.0,
        swing_axis=(1.0, 0.0, 0.0),
        settings=settings,
    )
    fires = sum(1 for _ in range(40) if tracker.update((-200.0, 0.0, 0.0)))
    assert fires == 0
