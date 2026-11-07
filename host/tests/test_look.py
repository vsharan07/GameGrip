"""Mount learning, motion features, and look versus gesture clicks."""

import math

from controls import ControlMap
from input.keyboard_control import KeyboardController
from input.mouse_aim import AimSettings, MouseController
from look_calibration import (
    MoveRecorder,
    build_document,
    load_document,
    save_document,
    watch_long_press,
    watch_swing,
)
from sensor.motion import detection_thresholds
from recognition.gestures import GestureLibrary, RecognitionSettings, Recognizer, build_gesture
from safety import Safety
from sensor.motion import (
    MotionError,
    classify_direction,
    gyro_bias,
    learn_mount,
    motion_features,
    size_label,
    speed_label,
)
from session import PlaySession


def _hold(gx=0.0, gy=0.0, gz=0.0, count=20):
    return [(0.0, 0.0, 1.0, gx, gy, gz) for _ in range(count)]


def _pulse(axis, count=16, peak=180.0):
    rows = []
    for index in range(count):
        phase = index / float(count - 1)
        magnitude = peak * math.sin(math.pi * phase)
        gyro = [0.0, 0.0, 0.0]
        gyro[axis] = magnitude
        rows.append((0.0, 0.0, 1.0, gyro[0], gyro[1], gyro[2]))
    return rows


def _cardinal():
    return {
        "left": _hold(gx=-80.0),
        "right": _hold(gx=90.0),
        "up": _hold(gy=70.0),
        "down": _hold(gy=-75.0),
    }


def test_mount_learns_axis_and_inversion():
    mount = learn_mount(_cardinal(), (0.0, 0.0, 0.0), onset_dps=40.0)
    assert mount["horizontal_axis"] == "gx"
    assert mount["vertical_axis"] == "gy"
    assert mount["invert_horizontal"] is False
    assert mount["invert_vertical"] is True


def test_same_axis_is_reported_instead_of_guessed():
    recordings = {
        "left": _hold(gx=-80.0),
        "right": _hold(gx=80.0),
        "up": _hold(gx=80.0),
        "down": _hold(gx=-80.0),
    }
    try:
        learn_mount(recordings, (0.0, 0.0, 0.0), onset_dps=40.0)
    except MotionError as exc:
        assert "cannot tell" in str(exc)
    else:
        raise AssertionError("one axis was accepted for both directions")


def test_rest_bias_and_direction_labels():
    rest = _hold(gx=2.0, gy=-1.0, gz=0.5, count=30)
    bias = gyro_bias(rest)
    assert abs(bias[0] - 2.0) < 1e-6
    features = motion_features(_hold(gx=-100.0) + _hold(count=5), bias, hz=50, onset_dps=40.0)
    assert features["intentional"]
    assert features["peak_angular_velocity_dps"] > 90.0
    assert features["travel_deg"] > 20.0
    assert features["onset_index"] == 0
    assert classify_direction(-30.0, 0.0, 12.0) == "left"
    assert classify_direction(0.0, 0.0, 12.0) == "neutral"
    assert classify_direction(40.0, -40.0, 12.0) == "up-right"
    assert size_label(10.0, 15.0, 80.0) == "small"
    assert size_label(90.0, 15.0, 80.0) == "large"
    assert speed_label(30.0, 40.0, 200.0) == "slow"
    assert speed_label(220.0, 40.0, 200.0) == "fast"
    assert size_label(50.0, 40.0, 30.0) == "unscaled"


def test_look_document_persists_and_distinguishes_scale(tmp_path):
    rest = _hold(count=30)
    right = _hold(gx=90.0, count=20)
    up = _hold(gy=70.0, count=20)
    document = build_document(rest, right, up, created="2026-11-04T00:00:00Z")
    assert document["version"] == 3
    assert document["mount"]["horizontal_axis"] == "gx"
    assert document["mount"]["vertical_axis"] == "gy"
    path = tmp_path / "look.json"
    save_document(document, path)
    loaded = load_document(path)
    assert loaded["groups"]["right"]["samples"][0][0] == 0.0
    aim = AimSettings(sensitivity=0.15, horizontal_sensitivity=0.15, vertical_sensitivity=0.15)
    from look_calibration import apply_to_aim

    apply_to_aim(loaded, aim)
    assert aim.mount_learned
    assert aim.horizontal_sensitivity == 18.0
    assert aim.vertical_sensitivity == 14.0
    assert aim.invert_vertical is True
    assert aim.max_pixels_per_second >= 20000.0


def test_recorder_uses_real_gaps_and_survives_a_short_pause():
    onset, release = detection_thresholds(1.5)
    assert onset < 20.0
    assert release < onset
    recorder = MoveRecorder((0, 0, 0), onset, release, end_hold_s=0.3, max_wait_s=1.0, max_move_s=3.0)
    clock = 0.0
    for _ in range(5):
        recorder.push(clock, (0, 0, 1, 0, 0, 0))
        clock += 0.03
    for speed in (20.0, 25.0, 8.0, 22.0, 18.0, 4.0, 3.0) + (2.0,) * 25:
        recorder.push(clock, (0, 0, 1, speed, 0, 0))
        clock += 0.025
    assert recorder.state == "done"
    assert recorder.reason is None
    uneven = motion_features(
        _hold(gx=60.0, count=10),
        (0, 0, 0),
        50,
        8,
        times=[0.0, 0.01, 0.04, 0.05, 0.09, 0.10, 0.14, 0.15, 0.20, 0.22],
    )
    even = motion_features(_hold(gx=60.0, count=10), (0, 0, 0), 50, 8)
    assert uneven["travel_deg"] != even["travel_deg"]
    missed = MoveRecorder((0, 0, 0), onset, release, max_wait_s=0.2, max_move_s=1.0)
    missed.push(0.0, (0, 0, 1, 0, 0, 0))
    missed.push(0.3, (0, 0, 1, 0, 0, 0))
    assert missed.state == "failed"
    assert "No movement started" in missed.reason


def test_fractional_relative_deltas_add_up_and_fast_moves_outrun_slow_ones():
    from input.mouse_aim import AimSettings, MouseController, aim_delta

    sent = []
    controller = MouseController(live=True, move=lambda dx, dy: sent.append((dx, dy)))
    settings = AimSettings(
        enabled=True,
        sensitivity=1.0,
        horizontal_sensitivity=1.0,
        vertical_sensitivity=1.0,
        smoothing=0.0,
        deadzone_dps=0.0,
        acceleration=1.0,
        max_pixels_per_second=10000.0,
    )
    controller.aim((0, 0, 1, 30, 0, 0), 0.02, settings)
    assert sent == []
    controller.aim((0, 0, 1, 30, 0, 0), 0.02, settings)
    assert sent == [(1, 0)]
    slow, _, _ = aim_delta((0, 0, 1, 40, 0, 0), 0.02, AimSettings(
        horizontal_sensitivity=6.0, vertical_sensitivity=6.0, sensitivity=6.0,
        smoothing=0.0, deadzone_dps=0.0, acceleration=1.7, max_pixels_per_second=20000.0,
    ))
    fast, _, _ = aim_delta((0, 0, 1, 300, 0, 0), 0.02, AimSettings(
        horizontal_sensitivity=6.0, vertical_sensitivity=6.0, sensitivity=6.0,
        smoothing=0.0, deadzone_dps=0.0, acceleration=1.7, max_pixels_per_second=20000.0,
    ))
    assert fast / slow > (300.0 / 40.0)
    settings = RecognitionSettings()
    gesture = build_gesture("Swing", [_pulse(0)] * 3, hz=50, settings=settings)
    aim = AimSettings(
        enabled=True,
        horizontal_axis="gx",
        vertical_axis="gy",
        sensitivity=1.0,
        horizontal_sensitivity=1.0,
        vertical_sensitivity=1.0,
        deadzone_dps=0.0,
        smoothing=0.0,
        max_pixels_per_second=10000.0,
    )
    pressed = []
    session = PlaySession(
        GestureLibrary([gesture]),
        ControlMap(gestures={"swing": "left_click"}, aim=aim),
        Recognizer(GestureLibrary([gesture]), settings),
        settings.segmenter(50),
        MouseController(live=False, move=lambda dx, dy: (_ for _ in ()).throw(AssertionError("moved"))),
        KeyboardController(live=False, press=lambda key: None, release=lambda key: None),
        Safety(),
    )
    look_events = []
    for index, sample in enumerate(_hold(count=5) + _pulse(1) + _hold(count=20)):
        look_events.extend(session.push(sample, index * 0.02))
    assert any(event["type"] == "gesture" and event["name"] is None for event in look_events)
    assert not any(event["type"] == "dry-run" for event in look_events)
    assert pressed == []
    swing_events = []
    for index, sample in enumerate(_hold(count=5) + _pulse(0) + _hold(count=20)):
        swing_events.extend(session.push(sample, 5.0 + index * 0.02))
    assert not any(event["type"] == "dry-run" for event in swing_events)
    assert any(event["type"] == "aim" for event in swing_events)


class _Script(object):
    def __init__(self, frames, step):
        self.frames = list(frames)
        self.step = step
        self.t = 0.0

    def clock(self):
        return self.t

    def read_frame(self):
        self.t += self.step
        if not self.frames:
            return None
        return self.frames.pop(0)


def test_long_press_is_detected_once():
    frames = [((0.0, 0.0, 1.0, 0.0, 0.0, 0.0), 1)] * 40
    script = _Script(frames, 0.05)
    assert watch_long_press(script, lambda _message: None, script.clock, hold_s=1.5, wait_s=3.0) == "ok"


def test_missing_button_is_reported():
    frames = [((0.0, 0.0, 1.0, 0.0, 0.0, 0.0), None)] * 6
    script = _Script(frames, 1.0)
    assert watch_long_press(script, lambda _message: None, script.clock, wait_s=4.0) == "missing"


def test_swing_without_a_button_is_reported():
    frames = [((0.0, 0.0, 1.0, 90.0, -90.0, 0.0), 0)] * 12
    script = _Script(frames, 0.2)
    mount = {"horizontal_axis": "gx", "vertical_axis": "gy"}
    result = watch_swing(script, lambda _message: None, script.clock, mount, wait_s=1.5)
    assert result == "no-button"
