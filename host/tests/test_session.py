"""Sensor samples become a dry-run action, and a live action only after enable."""

import math

import pytest

from controls import ControlMap
from input.keyboard_control import KeyboardController
from input.mouse_aim import MouseController
from recognition.gestures import GestureLibrary, RecognitionSettings, Recognizer, build_gesture
from safety import Safety
from session import PlaySession


def _pulse(axis, count=16, peak=180.0):
    rows = []
    for index in range(count):
        phase = index / float(count - 1)
        magnitude = peak * math.sin(math.pi * phase)
        gyro = [0.0, 0.0, 0.0]
        gyro[axis] = magnitude
        rows.append((0.0, 0.0, 1.0, gyro[0], gyro[1], gyro[2]))
    return rows


def _still(count):
    return [(0.0, 0.0, 1.0, 0.0, 0.0, 0.0) for _ in range(count)]


def _session(binding, live_mouse=False):
    settings = RecognitionSettings()
    gesture = build_gesture("Swing", [_pulse(0)] * 3, hz=50, settings=settings)
    library = GestureLibrary([gesture])
    controls = ControlMap(game="minecraft", gestures={"swing": binding})
    pressed = []
    keys = []
    mouse = MouseController(live=False, press=pressed.append, release=lambda button: None)
    keyboard = KeyboardController(live=False, press=keys.append, release=lambda key: None)
    session = PlaySession(
        library,
        controls,
        Recognizer(library, settings),
        settings.segmenter(50),
        mouse,
        keyboard,
        Safety(),
    )
    session.set_live(live_mouse)
    return session, pressed, keys


def _play(session, samples, start=0.0, buttons=None):
    events = []
    for index, sample in enumerate(samples):
        button = None if buttons is None else buttons[index]
        events.extend(session.push(sample, start + index * 0.02, button=button))
    return events


def test_gesture_alone_does_not_click():
    session, pressed, _keys = _session("left_click", live_mouse=True)
    events = _play(session, _still(5) + _pulse(0) + _still(20))
    assert any(event["type"] == "gesture" for event in events)
    assert pressed == []
    assert not any(event.get("input") == "right_click" for event in events)


def test_unrelated_movement_does_not_inject_when_live():
    session, pressed, _keys = _session("left_click", live_mouse=True)
    events = _play(session, _still(5) + _pulse(1) + _still(20))
    assert any(event["type"] == "gesture" and event["name"] is None for event in events)
    assert pressed == []


def test_stop_releases_and_blocks_injection():
    session, pressed, _keys = _session("space", live_mouse=True)
    session.keyboard.press("space")
    session.safety.stopped = True
    events = _play(session, _still(5) + _pulse(0) + _still(20))
    assert any(event["type"] == "stopped" for event in events)
    assert session.keyboard._held == set()
    assert pressed == []


def _fast():
    return (0.0, 0.0, 1.0, 250.0, 0.0, 0.0)


def test_one_click_then_a_large_swing_is_a_left_click():
    session, pressed, _keys = _session("left_click", live_mouse=True)
    samples = _still(2) + _still(8) + [_fast()] * 18
    buttons = [1, 0] + [0] * (len(samples) - 2)
    events = _play(session, samples, buttons=buttons)
    assert session._look_paused is False
    assert [event["input"] for event in events if event["type"] == "injected"] == ["left_click"]
    assert pressed == ["left"]


def test_two_clicks_then_a_large_swing_is_a_right_click():
    session, pressed, _keys = _session("left_click", live_mouse=True)
    samples = _still(4) + _still(8) + [_fast()] * 18
    buttons = [1, 0, 1, 0] + [0] * (len(samples) - 4)
    events = _play(session, samples, buttons=buttons)
    clicks = [event["input"] for event in events if event["type"] == "injected"]
    assert clicks == ["right_click"]
    assert pressed == ["right"]


@pytest.mark.parametrize("movement", [
    [_fast()],  # one fast reading is enough
    [(0, 0, 1, 1500, 0, 0)],  # a very fast spike
    [(0, 0, 1, 0, 120, 0)],  # any axis counts
], ids=["flick", "spike", "other-axis"])
def test_one_fast_reading_after_still_fires_the_armed_click(movement):
    session, pressed, _keys = _session("left_click", live_mouse=True)
    samples = _still(5) + movement + _still(5)
    events = _play(session, samples, buttons=[1, 0] + [0] * (len(samples) - 2))
    assert [e["input"] for e in events if e["type"] == "injected"] == ["left_click"]
    assert pressed == ["left"] and not session._look_paused


@pytest.mark.parametrize("movement", [
    [(0, 0, 1, 90, 0, 0)] * 100,  # a slow turn stays under 100 deg/s
    [(0, 0, 1, 50, 40, 0)] * 50,  # combined speed is about 64 deg/s
], ids=["slow-turn", "slow-diagonal"])
def test_slow_movements_do_not_spend_the_click_opportunity(movement):
    session, pressed, _keys = _session("left_click", live_mouse=True)
    samples = _still(5) + movement + _still(5)
    events = _play(session, samples, buttons=[1, 0] + [0] * (len(samples) - 2))
    assert not any(e["type"] == "injected" for e in events)
    assert pressed == [] and session._armed == "left" and session._look_paused
    events = _play(session, [_fast()] * 3, start=len(samples) * .02, buttons=[0] * 3)
    assert [e["input"] for e in events if e["type"] == "injected"] == ["left_click"]
    assert pressed == ["left"] and not session._look_paused


@pytest.mark.parametrize("hz", [25, 50, 100])
@pytest.mark.parametrize("axis,sign", [(0, 1), (1, -1), (2, 1)])
def test_large_swing_uses_elapsed_time_and_calibrated_bias(hz, axis, sign):
    session, pressed, _keys = _session("left_click", live_mouse=True)
    bias = (80.0, -60.0, 40.0)
    session.controls.aim.bias_dps = bias
    neutral = (0, 0, 1, *bias)
    # Arm using the real button path, with a nonzero sensor resting bias.
    for i, button in enumerate([1, 0, 0, 0, 0]):
        session.push(neutral, i / hz, button=button)
    gyro = list(bias)
    gyro[axis] += sign * 300
    for i in range(int(.4 * hz)):
        session.push((0, 0, 1, *gyro), (5 + i) / hz, button=0)
    assert pressed == ["left"]


def test_a_swing_after_a_sample_gap_must_start_from_still_again():
    session, pressed, _keys = _session("left_click", live_mouse=True)
    _play(session, _still(5), buttons=[1, 0, 0, 0, 0])
    # After a dropout, motion already in progress is not treated as a fresh swing.
    session.push(_fast(), .7, button=0)
    _play(session, [_fast()] * 5, start=.72, buttons=[0] * 5)
    assert pressed == [] and session._armed == "left"
    _play(session, _still(2) + [_fast()], start=.9, buttons=[0] * 3)
    assert pressed == ["left"]


def test_dry_run_large_swing_logs_once_without_clicking():
    session, pressed, _keys = _session("left_click")
    samples = _still(5) + [_fast()] * 25
    events = _play(session, samples, buttons=[1, 0] + [0] * (len(samples) - 2))
    assert [e["input"] for e in events if e["type"] == "dry-run"] == ["left_click"]
    assert not pressed and session._armed is None


def test_disconnecting_while_armed_cancels_the_click():
    session, pressed, _keys = _session("left_click", live_mouse=True)
    _play(session, _still(5), buttons=[1, 0, 0, 0, 0])
    assert session._armed == "left" and not pressed
    session.release_inputs()
    _play(session, _still(5) + [_fast()] * 3, start=.3, buttons=[0] * 8)
    assert not pressed and session._armed is None


def test_one_click_pauses_the_mouse():
    session, pressed, _keys = _session("left_click", live_mouse=True)
    moved = []
    session.mouse._move = moved.append
    session.controls.aim.enabled = True
    session.controls.aim.smoothing = 0.0
    session.controls.aim.deadzone_dps = 0.0
    session.controls.aim.horizontal_sensitivity = 20.0
    _play(session, _still(2), buttons=[1, 0])
    assert session._look_paused is True
    _play(session, [(0.0, 0.0, 1.0, 80.0, 0.0, 0.0)], start=1.0, buttons=[0])
    assert moved == []
    assert pressed == []


def test_a_short_click_without_a_swing_does_not_click():
    session, pressed, _keys = _session("left_click", live_mouse=True)
    samples = _still(10)
    _play(session, samples, buttons=[1, 1, 1, 0, 0, 0, 0, 0, 0, 0])
    assert pressed == []


def test_holding_the_button_holds_w_until_release():
    session, _pressed, keys = _session("left_click", live_mouse=True)
    released = []
    session.keyboard._release = released.append
    samples = _still(50)
    buttons = [1] * 40 + [0] * 10
    events = _play(session, samples, buttons=buttons)
    assert keys == ["w"]
    assert released == ["w"]
    assert session.keyboard._held == set()
    assert sum(1 for event in events if event["type"] == "injected" and event["input"] == "w") == 1


def test_w_stays_pressed_during_hold_and_emergency_stop_releases_it():
    session, _pressed, keys = _session("left_click", live_mouse=True)
    released = []
    session.keyboard._release = released.append
    _play(session, _still(30), buttons=[1] * 30)
    assert keys == ["w"]
    assert released == []
    assert session.keyboard._held == {"w"}
    session.safety.stopped = True
    session.push(_still(1)[0], 0.7, button=1)
    assert released == ["w"]
    assert not session._walking
    assert not session._button_down


def test_releasing_inputs_on_disconnect_cancels_hold_and_armed_click():
    session, pressed, keys = _session("left_click", live_mouse=True)
    released = []
    session.keyboard._release = released.append
    _play(session, _still(30), buttons=[1] * 30)
    session.release_inputs()
    assert keys == ["w"] and released == ["w"]
    assert session.keyboard._held == set()
    _play(session, _still(2), start=1.0, buttons=[1, 0])
    assert session._armed == "left"
    session.release_inputs()
    session.push(_fast(), 2.0, button=0)
    assert session._armed is None and pressed == []


def test_saved_template_round_trip_reaches_the_session(tmp_path):
    settings = RecognitionSettings()
    gesture = build_gesture("Block", [_pulse(2)] * 3, hz=50, settings=settings)
    path = tmp_path / "gestures.json"
    GestureLibrary([gesture]).save(path)
    loaded = GestureLibrary.load(path)
    controls = ControlMap(gestures={"block": "space"})
    pressed = []
    keys = []
    session = PlaySession(
        loaded,
        controls,
        Recognizer(loaded, settings),
        settings.segmenter(50),
        MouseController(live=True, press=pressed.append, release=lambda button: None),
        KeyboardController(live=True, press=keys.append, release=lambda key: None),
        Safety(),
    )
    session.set_live(True)
    events = _play(session, _still(5) + _pulse(2) + _still(20))
    assert any(event["type"] == "injected" and event["input"] == "space" for event in events)
    assert keys == ["space"]
    assert pressed == []
