"""Sensor samples become a dry-run action, and a live action only after enable."""

import math

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


def test_one_click_then_a_fast_swing_is_a_left_click():
    session, pressed, _keys = _session("left_click", live_mouse=True)
    samples = _still(2) + _still(8) + [_fast()]
    buttons = [1, 0] + [0] * 8 + [0]
    events = _play(session, samples, buttons=buttons)
    assert session._look_paused is False
    assert [event["input"] for event in events if event["type"] == "injected"] == ["left_click"]
    assert pressed == ["left"]


def test_two_clicks_then_a_fast_swing_is_a_right_click():
    session, pressed, _keys = _session("left_click", live_mouse=True)
    samples = _still(4) + _still(8) + [_fast()]
    buttons = [1, 0, 1, 0] + [0] * 8 + [0]
    events = _play(session, samples, buttons=buttons)
    clicks = [event["input"] for event in events if event["type"] == "injected"]
    assert clicks == ["right_click"]
    assert pressed == ["right"]


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
    assert len(keys) >= 5
    assert set(keys) == {"w"}
    assert set(released) == {"w"}
    assert sum(1 for event in events if event["type"] == "injected" and event["input"] == "w") >= 5


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
