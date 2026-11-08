"""Mouse aim math and the dry-run gate. pynput is not called."""

import pytest

from input.mouse_aim import AimSettings, MouseController, aim_delta, mapped_rates


def _sample(gx=0.0, gy=0.0, gz=0.0):
    return (0.0, 0.0, 1.0, gx, gy, gz)


def _settings(**values):
    settings = AimSettings(
        enabled=True,
        smoothing=0.0,
        deadzone_dps=0.0,
        sensitivity=1.0,
        max_pixels_per_second=10000.0,
        acceleration=1.0,
    )
    for key, value in values.items():
        setattr(settings, key, value)
    return settings


def test_relative_movement_uses_sensitivity_and_time():
    dx, dy, _smooth = aim_delta(_sample(gx=100.0, gy=-50.0), 0.1, _settings())
    assert abs(dx - 10.0) < 1e-6
    assert abs(dy - -5.0) < 1e-6


def test_axis_inversion_and_axis_choice():
    dx, dy, _smooth = aim_delta(_sample(gx=100.0, gz=40.0), 0.1, _settings(invert_horizontal=True, vertical_axis="gz"))
    assert abs(dx - -10.0) < 1e-6
    assert abs(dy - 4.0) < 1e-6


@pytest.mark.parametrize("rotation,expected", [
    (0, (4.0, -2.0)), (90, (2.0, 4.0)),
    (180, (-4.0, 2.0)), (270, (-2.0, -4.0)),
])
def test_quarter_turn_mount_rotations(rotation, expected):
    settings = _settings(rotation_deg=rotation)
    dx, dy, _ = aim_delta(_sample(gx=40, gy=-20), 0.1, settings)
    assert (dx, dy) == pytest.approx(expected)
    horizontal, vertical = mapped_rates(_sample(gx=40, gy=-20), settings)
    assert (horizontal * 0.1, vertical * 0.1) == pytest.approx(expected)


def test_sideways_mount_turns_former_left_movement_into_up():
    moves = []
    controller = MouseController(live=True, move=lambda dx, dy: moves.append((dx, dy)))
    controller.aim(_sample(gx=-100), 0.1, _settings(rotation_deg=90))
    assert moves == [(0, -10)]


def test_rotation_keeps_sensitivities_on_screen_axes_and_applies_bias_first():
    settings = _settings(rotation_deg=90, horizontal_sensitivity=2.0,
                         vertical_sensitivity=0.5, bias_dps=(10, 5, 0), invert_horizontal=True)
    dx, dy, _ = aim_delta(_sample(gx=110, gy=55), 0.1, settings)
    assert (dx, dy) == pytest.approx((-10.0, -5.0))


def test_saved_rotation_is_optional_for_old_profiles_and_round_trips(tmp_path):
    from controls import ControlMap

    assert AimSettings.from_dict({"enabled": True}).rotation_deg == 0
    controls = ControlMap(aim={"rotation_deg": 270, "horizontal_sensitivity": 18,
                               "vertical_sensitivity": 14})
    path = tmp_path / "controls.json"
    controls.save(path)
    loaded = ControlMap.load(path)
    assert loaded.aim.rotation_deg == 270
    assert loaded.aim.horizontal_sensitivity == 18 and loaded.aim.vertical_sensitivity == 14


def test_rotation_command_saves_setting_and_clears_old_motion(tmp_path, capsys):
    from console import build_app
    from controls import ControlMap
    from safety import Safety

    controls_path = tmp_path / "controls.json"
    gesture_path = tmp_path / "gestures.json"
    app = build_app(Safety(), gesture_path=gesture_path, controls_path=controls_path)
    app.session.mouse.smooth = (10, 20)
    app.command("aim rotate 90")
    assert ControlMap.load(controls_path).aim.rotation_deg == 90
    assert app.session.mouse.smooth == (0, 0)
    assert not gesture_path.exists()
    app.command("aim rotate 45")
    assert app.controls.aim.rotation_deg == 90
    assert "0, 90, 180, or 270" in capsys.readouterr().out


@pytest.mark.parametrize("rotation", [45, -90, 90.5, "90", True])
def test_invalid_mount_rotation_is_rejected(rotation):
    with pytest.raises(ValueError, match="rotation"):
        AimSettings(rotation_deg=rotation)


def test_deadzone_smoothing_and_speed_cap():
    dx, dy, _smooth = aim_delta(_sample(gx=10.0), 0.1, _settings(deadzone_dps=12.0))
    assert dx == 0.0 and dy == 0.0
    dx, dy, smooth = aim_delta(_sample(gx=100.0), 0.1, _settings(smoothing=0.5), smooth=(0.0, 0.0))
    assert abs(smooth[0] - 50.0) < 1e-6
    assert abs(dx - 5.0) < 1e-6
    dx, dy, _smooth = aim_delta(_sample(gx=1000.0), 1.0, _settings(max_pixels_per_second=10.0))
    assert abs(dx - 10.0) < 1e-6


def test_separate_sensitivity_curve_bias_and_pause():
    dx, dy, _smooth = aim_delta(
        _sample(gx=100.0, gy=50.0),
        0.1,
        _settings(horizontal_sensitivity=2.0, vertical_sensitivity=0.5),
    )
    assert abs(dx - 20.0) < 1e-6
    assert abs(dy - 2.5) < 1e-6
    slow, _dy, _smooth = aim_delta(_sample(gx=50.0), 1.0, _settings(acceleration=2.0, horizontal_sensitivity=1.0))
    fast, _dy, _smooth = aim_delta(_sample(gx=200.0), 1.0, _settings(acceleration=2.0, horizontal_sensitivity=1.0))
    assert fast / slow > (200.0 / 50.0)
    dx, _dy, _smooth = aim_delta(_sample(gx=20.0), 0.1, _settings(bias_dps=(20.0, 0.0, 0.0)))
    assert dx == 0.0
    dx, dy, _smooth = aim_delta(_sample(gx=100.0, gy=80.0), 0.1, _settings(paused=True))
    assert dx == 0.0 and dy == 0.0
    dx, dy, _smooth = aim_delta((0.0, 0.0, 1.0, float("nan"), 0.0, 0.0), 0.1, _settings())
    assert dx == 0.0 and dy == 0.0


def test_dry_run_does_not_move_or_click():
    def move(dx, dy):
        raise AssertionError("mouse moved %s %s" % (dx, dy))

    def press(button):
        raise AssertionError("mouse pressed %s" % button)

    controller = MouseController(live=False, move=move, press=press)
    controller.aim(_sample(gx=200.0), 0.05, _settings())
    assert controller.tap("left") is False


def test_live_move_and_stop_releases_buttons():
    moves = []
    pressed = []
    released = []
    controller = MouseController(live=True, move=lambda dx, dy: moves.append((dx, dy)), press=pressed.append, release=released.append)
    controller.aim(_sample(gx=100.0), 0.1, _settings())
    assert moves
    controller.press("left")
    controller.set_live(False)
    assert released == ["left"]
    controller.release_all()
    assert "left" not in controller._held


def test_windows_relative_input_uses_move_events_and_checks_failure():
    import ctypes
    import pytest
    from input.windows_mouse import Input, WindowsMouse

    events = []

    def send(count, pointer, size):
        value = ctypes.cast(pointer, ctypes.POINTER(Input)).contents
        assert count == 1 and size == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        events.append((value.value.mi.dx, value.value.mi.dy, value.value.mi.dwFlags))
        return 1

    WindowsMouse(send_input=send).move(7, -3)
    assert events == [(7, -3, 1)]
    with pytest.raises(OSError):
        WindowsMouse(send_input=lambda *args: 0).move(1, 1)


def test_windows_mouse_dispatch_does_not_import_quartz(monkeypatch):
    from types import SimpleNamespace
    from input import mouse_aim

    moves = []
    monkeypatch.setattr(mouse_aim.sys, "platform", "win32")
    monkeypatch.setattr(mouse_aim, "_windows_mouse", SimpleNamespace(move=lambda x, y: moves.append((x, y))))
    mouse_aim.post_relative_delta(8, -2)
    assert moves == [(8, -2)]
