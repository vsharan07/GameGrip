"""Website calibration and launch use the real host with mocked desktop output."""

import itertools
from types import SimpleNamespace

import numpy as np
import pytest

from console import build_app
from controls import ControlMap
from input.keyboard_control import KeyboardController
from input.mouse_aim import MouseController, mapped_rates
from look_calibration import LookCalibrationError, load_document
from minecraft_controller import MinecraftController
from safety import Safety


BIAS = (3.0, -2.0, 1.0)


def still(n=30):
    return [(0.0, 0.0, 1.0, *BIAS)] * n


def turn(axis, sign):
    gyro = list(BIAS)
    gyro[axis] += sign * 90
    back = list(BIAS)
    back[axis] -= sign * 90
    # Include an equally fast return: signed sums over the entire window cancel.
    return still(10) + [(0, 0, 1, *gyro)] * 30 + still(20) + [(0, 0, 1, *back)] * 30 + still(10)


@pytest.fixture
def controller(tmp_path):
    safety = Safety(output=lambda _: None)
    safety.listen = lambda: True
    safety.stop = lambda: None
    app = build_app(safety, gesture_path=tmp_path / "gestures.json", controls_path=tmp_path / "controls.json")
    moves, keys, released, clicks = [], [], [], []
    app.session.mouse = MouseController(live=False, move=lambda x, y: moves.append((x, y)),
                                        press=clicks.append, release=lambda _: None)
    app.session.keyboard = KeyboardController(live=False, press=keys.append, release=released.append)
    events = []
    c = MinecraftController(events.append, app, safety, look_path=tmp_path / "look.json")
    c.outputs = SimpleNamespace(moves=moves, keys=keys, released=released, clicks=clicks, events=events)
    yield c
    c.stop()


@pytest.mark.parametrize("horizontal,vertical,h_sign,v_sign", [
    (*axes, *signs) for axes in itertools.permutations(range(3), 2)
    for signs in itertools.product((-1, 1), repeat=2)
])
def test_right_down_learns_mount_and_signs(controller, horizontal, vertical, h_sign, v_sign):
    controller.app.controls.aim.rotation_deg = 90
    right, down = turn(horizontal, h_sign), turn(vertical, v_sign)
    controller.calibrate(still(), right, down)
    settings = controller.app.controls.aim
    assert settings.rotation_deg == 0
    assert mapped_rates(right[15], settings)[0] > 0
    assert mapped_rates(down[15], settings)[1] > 0
    saved = ControlMap.load(controller.app.controls_path)
    assert saved.aim.to_dict() == settings.to_dict()
    document = load_document(controller.look_path)
    assert "down" in document["groups"] and "up" not in document["groups"]
    assert document["groups"]["down"]["samples"][15][1:] == list(down[15])
    assert not controller.live and not controller.outputs.moves


def prepare(controller):
    controller.calibrate(still(), turn(2, -1), turn(0, 1))
    controller.tick(10, "0,0,1,3,-2,1,0")


def test_accepts_numpy_recordings_from_the_website(controller):
    document = controller.calibrate(np.asarray(still()), np.asarray(turn(2, -1)), np.asarray(turn(0, 1)))
    assert controller.calibrated and document["mount"]["horizontal_axis"] == "gz"
    assert document["mount"]["vertical_axis"] == "gx"


def test_launch_counts_down_without_blocking_samples(controller):
    prepare(controller)
    controller.launch(10)
    for i in range(250):
        controller.tick(10 + i * .02, "0,0,1,3,-2,-89,0")
    assert not controller.live and not controller.outputs.moves
    assert [e["n"] for e in controller.outputs.events if e["type"] == "controller-count"] == [5, 4, 3, 2, 1]
    controller.tick(15, "0,0,1,3,-2,-89,0")
    assert controller.live and controller.outputs.moves[-1][0] > 0
    assert controller.outputs.events[-1]["type"] == "controller-live"


@pytest.mark.parametrize("reason", ["emergency", "disconnect", "shutdown"])
def test_stops_release_held_inputs_and_prevent_more_output(controller, reason):
    prepare(controller)
    controller.launch(10)
    for i in range(251):
        controller.tick(10 + i * .02, "0,0,1,3,-2,1,0")
    controller.app.session.keyboard.press("w")
    controller.app.session.mouse.press("left")
    if reason == "emergency":
        for stamp in (20, 20.1, 20.2):
            controller.safety.note_escape(stamp)
        controller.tick(15.02, "0,0,1,3,-2,-89,0")
    elif reason == "disconnect":
        with pytest.raises(RuntimeError, match="stream stopped"):
            controller.tick(16.1, "status imu=real")
    else:
        controller.stop()
    assert not controller.live
    assert controller.app.session.keyboard._held == set()
    assert controller.app.session.mouse._held == set()
    assert "w" in controller.outputs.released
    before = len(controller.outputs.moves)
    controller.tick(17, "0,0,1,3,-2,-89,1")
    assert len(controller.outputs.moves) == before


def test_launch_requires_calibration_fresh_sensor_and_stop_listener(controller):
    with pytest.raises(RuntimeError, match="Recalibrate"):
        controller.launch(10)
    prepare(controller)
    with pytest.raises(RuntimeError, match="fresh"):
        controller.launch(12)
    controller.safety.listen = lambda: False
    with pytest.raises(RuntimeError, match="listener"):
        controller.launch(10)
    assert controller.launch_at is None and not controller.live


def test_conflicting_directions_fail_without_overwriting_saved_controls(controller):
    prepare(controller)
    saved = controller.app.controls_path.read_bytes()
    with pytest.raises(LookCalibrationError, match="same axis"):
        controller.calibrate(still(), turn(1, 1), turn(1, -1))
    assert controller.app.controls_path.read_bytes() == saved


def test_stroke_axis_accepts_a_tilted_mount_and_explains_diagonals():
    import minecraft_controller as mc
    still = [(0, 0, 1, 0, 0, 0)] * 5
    # About 40 degrees off the gy axis (77% / 64%), which the old 80% rule rejected.
    tilted = still + [(0, 0, 1, 0, 160, 135)] * 25 + still
    assert mc.stroke_axis(tilted, (0, 0, 0), 8.0)[0] == 1
    # Spread over all three axes (60% at most) is still rejected, with numbers.
    diagonal = still + [(0, 0, 1, 90, 80, 90)] * 25 + still
    assert mc.stroke_axis(diagonal, (0, 0, 0), 8.0) is None
    assert "diagonal" in mc.last_stroke_problem and "%" in mc.last_stroke_problem
    tiny = still + [(0, 0, 1, 0, 20, 0)] * 3 + still
    assert mc.stroke_axis(tiny, (0, 0, 0), 8.0) is None
    assert "degrees" in mc.last_stroke_problem


def test_too_small_error_says_why(controller):
    still = [(0, 0, 1, 0, 0, 0)] * 30
    turn = still[:5] + [(0, 0, 1, 90, 80, 90)] * 25 + still[:5]
    down = still[:5] + [(0, 0, 1, 0, 0, -150)] * 25 + still[:5]
    with pytest.raises(LookCalibrationError, match="right turn.*diagonal"):
        controller.calibrate(still, turn, down)
