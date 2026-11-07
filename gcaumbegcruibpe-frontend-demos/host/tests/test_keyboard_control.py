"""Keyboard taps stay behind the dry-run gate. pynput is not called."""

from input.keyboard_control import KeyboardController


def test_dry_run_prints_and_does_not_press(capsys):
    def press(key):
        raise AssertionError("key pressed %s" % key)

    controller = KeyboardController(live=False, press=press)
    assert controller.tap("space") is False
    assert "dry-run: would press space" in capsys.readouterr().out


def test_live_tap_presses_and_releases():
    pressed = []
    released = []
    controller = KeyboardController(live=True, press=pressed.append, release=released.append)
    assert controller.tap("a") is True
    assert pressed == ["a"]
    assert released == ["a"]


def test_duplicate_hold_is_released_once_on_stop():
    released = []
    controller = KeyboardController(live=True, press=lambda key: None, release=released.append)
    controller.press("space")
    controller.press("space")
    controller.set_live(False)
    assert released == ["space"]
    assert controller._held == set()
