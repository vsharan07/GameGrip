"""Sports demos with one working strap: the other player uses the keyboard."""

import math
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import demo_motion
import dtw_detector as dtw
import gamegrip_server as bridge


class Port:
    def __init__(self, name):
        self.port = name
        self.timeout = 0.05
        self.close = Mock()


def still(n=100):
    return [(0.3 * math.sin(i), 0.2 * math.cos(i), 0.1) for i in range(n)]


def swing(n=50):
    return [(0.0, 0.0, 0.0)] * 10 + [(0.0, 0.0, 400.0)] * 15 + [(0.0, 0.0, 0.0)] * (n - 25)


def session_with(monkeypatch, record):
    """A demos Session whose recordings come from record(call_index, ports, spare, lost)."""
    session = bridge.Session("demos")
    session.ser = Port("COM3")
    session.extra_sers = [Port("COM6")]
    session.devices = ["esp1", "esp2"]
    session.roles = demo_motion.BoardRoles()
    session.roles.assign("COM3", "esp1")
    session.roles.assign("COM6", "esp2")
    sent = []
    monkeypatch.setattr(session, "send", sent.append)
    monkeypatch.setattr(dtw, "countdown", lambda *a, **k: None)
    monkeypatch.setattr(bridge.time, "sleep", lambda *_: None)
    calls = []

    def fake_record(serials, seconds, spare=(), lost=None):
        calls.append([ser.port for ser in serials])
        return {}, record(len(calls) - 1, [ser.port for ser in serials], spare, lost)

    monkeypatch.setattr(demo_motion, "record_windows", fake_record)
    return session, sent, calls


def test_a_silent_second_strap_is_dropped_and_calibration_finishes(monkeypatch):
    def record(i, ports, spare, lost):
        out = {"COM3": still() if i == 0 else swing()}
        if "COM6" in ports:
            out["COM6"] = []  # opened, but sends nothing
        return out

    session, sent, calls = session_with(monkeypatch, record)
    assert session._calibrate_demos() is True
    assert set(session.swingers) == {"COM3"}
    assert session.extra_sers == [] and session.devices == ["esp1"]
    assert {"type": "straps", "count": 1} in sent
    # Every swing window after the drop records only the working strap.
    assert all(ports == ["COM3"] for ports in calls[1:])


def test_a_second_strap_that_errors_mid_calibration_is_dropped(monkeypatch):
    def record(i, ports, spare, lost):
        assert "COM3" not in spare  # the main strap is never optional
        out = {"COM3": still() if i == 0 else swing()}
        if "COM6" in ports:
            if i == 2:  # unplugged during the second swing
                lost.add("COM6")
            out["COM6"] = still() if i == 0 else swing()
        return out

    session, sent, _calls = session_with(monkeypatch, record)
    assert session._calibrate_demos() is True
    assert set(session.swingers) == {"COM3"}
    assert session.roles.device_for("COM3") == "esp1"
    assert {"type": "straps", "count": 1} in sent


def test_the_main_strap_failing_still_stops_calibration(monkeypatch):
    session, _sent, _calls = session_with(monkeypatch, lambda i, ports, spare, lost: {"COM3": [], "COM6": still()})
    with pytest.raises(RuntimeError, match="COM3 sent too few samples"):
        session._calibrate_demos()


def test_one_strap_mode_never_opens_a_second_board(monkeypatch):
    session = bridge.Session("demos")
    session.ser = Port("COM3")
    sent = []
    monkeypatch.setattr(session, "send", sent.append)
    monkeypatch.setattr(bridge, "ONE_STRAP", True)
    monkeypatch.setattr(demo_motion, "extra_usb_ports", Mock(side_effect=AssertionError("scanned")))
    monkeypatch.setattr(session, "_open_extra", Mock(side_effect=AssertionError("opened")))
    assert session._open_players() is True
    assert session.extra_sers == [] and session.devices == ["esp1"]
    assert session.roles.device_for("COM3") == "esp1"
    assert sent == [{"type": "straps", "count": 1}]


def test_two_straps_report_a_count_of_two(monkeypatch):
    session = bridge.Session("demos")
    session.ser = Port("COM3")
    sent = []
    monkeypatch.setattr(session, "send", sent.append)
    monkeypatch.setattr(demo_motion, "extra_usb_ports", lambda taken: ["COM6"])
    monkeypatch.setattr(session, "_open_extra", lambda path: Port(path))
    assert session._open_players() is True
    assert session.devices == ["esp1", "esp2"]
    assert sent == [{"type": "straps", "count": 2}]


class BrokenPort:
    port = "COM6"
    timeout = 0

    @property
    def in_waiting(self):
        raise OSError("gone")

    def read(self, size=1):
        raise OSError("ClearCommError failed")


class Streaming:
    port = "COM3"
    timeout = 0
    in_waiting = 0

    def read(self, size=1):
        return b"0,0,1,1,2,3\n"


def test_record_windows_keeps_recording_when_a_spare_board_errors():
    lost = set()
    _mags, gyros = demo_motion.record_windows([Streaming(), BrokenPort()], 0.05,
                                              spare=["COM6"], lost=lost)
    assert lost == {"COM6"}
    assert len(gyros["COM3"]) > 0 and gyros["COM6"] == []


def test_record_windows_still_raises_for_the_main_board():
    with pytest.raises(OSError):
        demo_motion.record_windows([Streaming(), BrokenPort()], 0.05)
