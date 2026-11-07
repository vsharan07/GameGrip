"""Injection gates. pynput is not imported."""

import sys

from injector import Injector
from safety import Safety


def _inject(
    injector,
    key,
    safety,
    matched=True,
    upper_down=False,
    lower_down=False,
    depends_on=("upper", "lower"),
    sensor_down=False,
):
    return injector.inject(
        key,
        matched=matched,
        upper_down=upper_down,
        lower_down=lower_down,
        safety=safety,
        depends_on=depends_on,
        sensor_down=sensor_down,
    )


def test_dry_run_never_calls_pynput_press(capsys):
    def press(key):
        raise AssertionError("pynput key press was called for %s" % key)

    loaded_before = "pynput" in sys.modules
    injector = Injector(live=False, press=press)
    assert _inject(injector, "a", Safety()) is True
    assert "dry-run: would press a" in capsys.readouterr().out
    if not loaded_before:
        assert "pynput" not in sys.modules


class _Key(object):
    def __init__(self, name=None, char=None):
        self.name = name
        self.char = char


def test_escape_presses_are_printed_and_block_injection():
    messages = []
    pressed = []
    safety = Safety(output=messages.append)
    injector = Injector(live=True, press=pressed.append, output=messages.append)
    safety.on_key_press(_Key(char="a"))
    safety.on_key_press(_Key(name="esc"), now=0.0)
    safety.on_key_press(_Key(name="esc"), now=0.2)
    safety.on_key_press(_Key(name="esc"), now=0.4)
    assert messages[:4] == [
        "key press: a",
        "key press: esc",
        "key press: esc",
        "key press: esc",
    ]
    assert "restart" in messages[4].lower()
    assert safety.stopped is True
    assert _inject(injector, "z", safety) is False
    assert messages[-1] == "blocked: z"
    assert pressed == []


def test_emergency_stop_blocks_all_keys_after_activation():
    messages = []
    pressed = []
    safety = Safety(output=messages.append)
    injector = Injector(live=True, press=pressed.append)

    safety.note_escape(0.0)
    safety.note_escape(0.4)
    assert safety.stopped is False
    assert _inject(injector, "a", safety) is True

    safety.note_escape(0.9)
    assert safety.stopped is True
    assert messages
    assert "restart" in messages[-1].lower()
    assert _inject(injector, "b", safety) is False
    assert _inject(injector, "c", safety) is False
    assert pressed == ["a"]

    spaced = Safety()
    spaced.note_escape(0.0)
    spaced.note_escape(0.4)
    spaced.note_escape(1.5)
    assert spaced.stopped is False


def test_down_module_blocks_gestures_that_depend_on_it():
    pressed = []
    safety = Safety()
    injector = Injector(live=True, press=pressed.append)

    assert _inject(injector, "a", safety, upper_down=True, depends_on=("upper", "lower")) is False
    assert _inject(injector, "b", safety, lower_down=True, depends_on=("lower",)) is False
    assert _inject(injector, "c", safety, upper_down=True, depends_on=("lower",)) is True
    assert pressed == ["c"]


def test_missing_sensor_does_not_inject_even_in_live_mode():
    def press(key):
        raise AssertionError("injected %s while the sensor was down" % key)

    injector = Injector(live=True, press=press)
    assert _inject(injector, "left_click", Safety(), depends_on=("sensor",), sensor_down=True) is False


def test_dry_run_prints_a_mouse_click_without_sending_it(capsys):
    def press(key):
        raise AssertionError("injected %s in dry-run" % key)

    injector = Injector(live=False, press=press)
    assert _inject(injector, "left_click", Safety(), depends_on=("sensor",)) is True
    assert "dry-run: would press left_click" in capsys.readouterr().out


def test_invalid_sample_does_not_inject():
    from sensor.csv_stream import ArrivalRate, StreamQuality
    from main import handle_sample

    pressed = []
    injector = Injector(live=True, press=pressed.append)
    quality = StreamQuality(1.0)

    class AlwaysFires(object):
        def update(self, gyro):
            return True

    assert handle_sample(None, 0.0, quality, ArrivalRate(), AlwaysFires(), injector, "a", Safety()) is False
    assert pressed == []
    assert handle_sample((0, 0, 1, 10, 0, 0), 0.0, quality, ArrivalRate(), AlwaysFires(), injector, "a", Safety()) is True
    assert pressed == ["a"]


def test_injection_is_skipped_when_no_match_occurred():
    pressed = []
    injector = Injector(live=True, press=pressed.append)
    assert _inject(injector, "a", Safety(), matched=False) is False
    assert pressed == []
