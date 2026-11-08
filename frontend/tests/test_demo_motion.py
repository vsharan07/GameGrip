"""Fast swing, short tap, and long hold for the sports demos."""

import gamegrip_server as bridge
import demo_motion as motion


def test_a_soft_swing_counts_and_a_fidget_does_not():
    strap = motion.StrapMotion()
    assert strap.push(0.0, 20, 0) == []
    assert strap.push(0.1, 70, 0) == ["swing"]
    assert strap.push(0.15, 90, 0) == []
    assert strap.push(0.25, 20, 0) == []
    assert strap.push(0.5, 65, 0) == ["swing"]


def test_short_tap_is_a_click_and_a_hold_keeps_repeating():
    strap = motion.StrapMotion()
    assert strap.push(0.0, 0, 1) == []
    assert strap.push(0.2, 0, 0) == ["click"]
    assert strap.push(1.0, 0, 1) == []
    assert strap.push(1.2, 0, 1) == []
    assert strap.push(1.5, 0, 1) == ["hold"]
    assert strap.push(1.6, 0, 1) == []
    assert strap.push(1.62, 0, 1) == ["hold"]
    assert strap.push(1.8, 0, 0) == []


def test_a_slow_move_is_not_a_swing():
    strap = motion.StrapMotion()
    strap.push(0.0, 10, None)
    assert strap.push(0.2, 20, None) == []


def test_two_swings_in_a_row_both_count():
    strap = motion.StrapMotion()
    assert strap.push(0.00, 50, 0) == ["swing"]
    assert strap.push(0.04, 10, 0) == []
    assert strap.push(0.10, 48, 0) == ["swing"]


def test_a_resting_gyro_offset_is_not_movement():
    assert motion.still_spread([(30, -8, 4)] * 12) == 0


class _Port:
    def __init__(self, port, lines):
        self.port = port
        self.lines = [line.encode() if isinstance(line, str) else line for line in lines]
        self.stream = b"".join(self.lines)
        self.timeout = None

    def readline(self):
        return self.lines.pop(0) if self.lines else b""

    @property
    def in_waiting(self):
        return len(self.stream)

    def read(self, n):
        chunk, self.stream = self.stream[:n], self.stream[n:]
        return chunk

    def reset_input_buffer(self):
        pass


def _bump(peak, n, axis=0):
    """One smooth stroke on one gyro axis, n samples long."""
    import math
    out = []
    for i in range(n):
        v = [0.0, 0.0, 0.0]
        v[axis] = peak * math.sin(math.pi * (i + 0.5) / n)
        out.append(tuple(v))
    return out


def _with_bias(samples, bias=(5.0, -3.0, 2.0)):
    return [(s[0] + bias[0], s[1] + bias[1], s[2] + bias[2]) for s in samples]


def _calibrated():
    hz = 50.0
    rest = _with_bias([((i % 3) - 1.0, ((i * 7) % 5) / 2.0 - 1.0, 0.0) for i in range(100)])
    swing = _with_bias([(0.0, 0.0, 0.0)] * 10 + _bump(300, 15) + _bump(-120, 15) + [(0.0, 0.0, 0.0)] * 10)
    return motion.build_swing(rest, [swing, swing, swing], hz)


def _feed(detector, samples, start=0.0, hz=50.0):
    return sum(1 for i, g in enumerate(samples) if detector.push(start + i / hz, g))


def test_one_real_swing_is_exactly_one_bat_swing():
    detector, _bias = _calibrated()
    quiet = _with_bias([(0.0, 0.0, 0.0)] * 20)
    swing = _with_bias(_bump(280, 15) + _bump(-120, 15))
    assert _feed(detector, quiet + swing + quiet) == 1


def test_a_softer_swing_still_counts_for_baseball():
    detector, _bias = _calibrated()
    quiet = _with_bias([(0.0, 0.0, 0.0)] * 20)
    assert _feed(detector, quiet + _with_bias(_bump(150, 15)) + quiet) == 1


def test_a_small_wiggle_is_not_a_bat_swing():
    detector, _bias = _calibrated()
    quiet = _with_bias([(0.0, 0.0, 0.0)] * 20)
    assert _feed(detector, quiet + _with_bias(_bump(35, 10)) + quiet) == 0


def test_two_swings_in_a_row_are_two_bat_swings():
    detector, _bias = _calibrated()
    quiet = _with_bias([(0.0, 0.0, 0.0)] * 20)
    swing = _with_bias(_bump(280, 15))
    assert _feed(detector, quiet + swing + quiet + swing + quiet) == 2


def test_a_busy_arm_still_punches_again():
    strap = motion.StrapMotion()
    assert strap.push(0.00, 80, None) == ["swing"]
    assert strap.push(0.05, 120, None) == []
    assert strap.push(0.10, 60, None) == []
    assert strap.push(0.20, 90, None) == ["swing"]
    assert strap.push(0.25, 95, None) == []
    assert strap.push(0.52, 95, None) == ["swing"]


def test_a_hand_settling_still_passes_the_still_check():
    calm = [((i % 3) - 1.0, 0.0, 0.0) for i in range(40)]
    twitch = [(45.0, 0.0, 0.0)] * 6
    assert motion.still_spread(calm + twitch) <= motion.DEMO_STILL_DPS


def test_half_lines_are_not_read_as_samples():
    port = _Port("/dev/a", [])
    port.stream = b"5,6\n0,0,1,1,2,3\n0,0,1,-12"
    reader = motion.LineReader(port)
    assert reader.lines() == ["0,0,1,1,2,3"]
    port.stream = b"0.5,4,5\n"
    assert reader.lines() == ["0,0,1,-120.5,4,5"]


def test_left_usb_port_is_the_left_player():
    paths = ["/dev/cu.usbserial-5", "/dev/cu.usbserial-0001"]
    locs = {"/dev/cu.usbserial-0001": 1048576, "/dev/cu.usbserial-5": 34603008}
    sides = motion.players_by_side(paths, locs)
    assert sides["/dev/cu.usbserial-0001"] == "esp1"
    assert sides["/dev/cu.usbserial-5"] == "esp2"
    roles = motion.BoardRoles()
    for port, device in sides.items():
        roles.assign(port, device)
    roles.note_button("/dev/cu.usbserial-5", True)
    assert roles.device_for("/dev/cu.usbserial-0001") == "esp1"
    assert roles.device_for("/dev/cu.usbserial-5") == "esp2"


def test_wait_for_button_returns_the_port_that_goes_down():
    quiet = "0,0,1,1,2,3,0\n"
    pressed = "0,0,1,1,2,3,1\n"
    plain = _Port("/dev/cu.usbserial-0001", [quiet, quiet, quiet])
    button = _Port("/dev/cu.usbserial-4", [quiet, pressed])
    found = motion.wait_for_button([plain, button], lambda: False, timeout=1)
    assert found is button


def test_both_boards_share_one_recording_window():
    line = "0,0,1,0,0,0,0\n"
    first = _Port("/dev/a", [line] * 12)
    second = _Port("/dev/b", [line] * 12)
    windows = motion.record_all([first, second], 0.05)
    assert motion.noise90(windows["/dev/a"]) == 0
    assert motion.noise90(windows["/dev/b"]) == 0


def test_a_button_press_does_not_swap_the_players():
    roles = motion.BoardRoles()
    roles.assign("/dev/cu.usbserial-0001", "esp1")
    roles.assign("/dev/cu.usbserial-5", "esp2")
    roles.note_button("/dev/cu.usbserial-5", True)
    assert roles.device_for("/dev/cu.usbserial-0001") == "esp1"
    assert roles.device_for("/dev/cu.usbserial-5") == "esp2"


def test_pumping_the_hand_up_and_down_starts_a_run():
    runner = motion.RunCadence()
    assert runner.push(0.00, (120, 0, 0)) == []
    assert runner.push(0.15, (-120, 0, 0)) == []
    assert runner.push(0.30, (120, 0, 0)) == []
    assert runner.push(0.45, (-130, 0, 0)) == ["run"]
    assert runner.push(0.50, (-130, 0, 0)) == []
    assert runner.push(0.58, (110, 0, 0)) == ["run"]


def test_one_swing_does_not_count_as_running():
    runner = motion.RunCadence()
    assert runner.push(0.0, (20, 0, 0)) == []
    assert runner.push(0.1, (400, 0, 0)) == []
    assert runner.push(0.3, (-80, 0, 0)) == []
    assert runner.active is False


def test_run_and_punch_lines_reach_the_page():
    assert bridge.parse_line("[esp1] RUN") == {"type": "run", "device": "esp1"}
    assert bridge.parse_line("[esp2] PUNCH") == {"type": "punch", "device": "esp2"}
    swing = bridge.parse_line("[esp2] SWING DETECTED! #2 42")
    assert swing["type"] == "swing" and swing["speed"] == 42 and swing["device"] == "esp2"


def test_an_interleaved_debug_line_does_not_hide_a_swing():
    line = "\x1b[90m[move 0.40s] swings=0 | peak 40 dps[esp1] SWING DETECTED! #4"
    swing = bridge.parse_line(line)
    assert swing["type"] == "swing" and swing["device"] == "esp1" and swing["n"] == 4


def test_windows_port_name_is_replaced_on_a_mac(monkeypatch):
    monkeypatch.setattr(motion.os, "name", "posix")
    monkeypatch.setattr(motion, "usb_ports", lambda: [
        "/dev/cu.usbserial-0001", "/dev/cu.usbserial-4"])
    assert motion.choose_port("COM10") == "/dev/cu.usbserial-0001"


def test_server_hears_each_board():
    swing = bridge.parse_line("[esp2] SWING DETECTED! #3")
    assert swing["type"] == "swing" and swing["device"] == "esp2" and swing["n"] == 3
    click = bridge.parse_line("[esp1] CLICK")
    assert click == {"type": "click", "device": "esp1"}
    hold = bridge.parse_line("[esp1] HOLD")
    assert hold["type"] == "hold" and hold["device"] == "esp1"
    assert bridge.parse_line("SWING DETECTED! #1")["type"] == "swing"
