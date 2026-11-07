"""Mouse aim math and the dry-run gate. pynput is not called."""

from input.mouse_aim import AimSettings, MouseController, aim_delta


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
