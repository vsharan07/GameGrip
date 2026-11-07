"""Pair upper and lower packets by host arrival time."""

import math

from alignment import Aligner, TimedPacket, align
from config import load_config
from features import relative_twist_deg
from gg_packet import FLAG_STILL, MAGIC, MODULE_LOWER, MODULE_UPPER, VERSION, GGPacket


def _packet(module_id, gyro_mag, qw=1.0, qx=0.0, qy=0.0, qz=0.0, btn0=0, btn1=0, flags=0):
    return GGPacket(
        magic=MAGIC,
        ver=VERSION,
        module_id=module_id,
        seq=0,
        t_ms=0,
        qw=qw,
        qx=qx,
        qy=qy,
        qz=qz,
        gyro_mag=gyro_mag,
        accel_mag=9.81,
            btn0=btn0,
            btn1=btn1,
            flags=flags,
            rssi=0,
        )


def _window_s():
    return load_config().alignment_window_ms / 1000.0


def test_alignment_pairs_inside_window_and_rejects_outside():
    window_s = _window_s()
    upper = _packet(MODULE_UPPER, gyro_mag=0.1, flags=FLAG_STILL)
    lower = _packet(MODULE_LOWER, gyro_mag=0.4, btn0=1, btn1=0)
    paired = align(
        TimedPacket(upper, 1.0),
        TimedPacket(lower, 1.0 + window_s),
        window_s,
    )
    assert paired.missing_module is None
    assert paired.gyro_upper == upper.gyro_mag
    assert paired.gyro_lower == lower.gyro_mag
    assert paired.btn0 == 1
    assert paired.btn1 == 0
    assert paired.t_host_ms == pytest_ms(1.0 + window_s)
    assert paired.twist_deg == pytest_approx(relative_twist_deg(
        (upper.qw, upper.qx, upper.qy, upper.qz),
        (lower.qw, lower.qx, lower.qy, lower.qz),
    ))

    too_late = align(
        TimedPacket(upper, 1.0),
        TimedPacket(lower, 1.0 + window_s + 0.001),
        window_s,
    )
    assert too_late.missing_module == "upper"
    assert too_late.gyro_upper is None
    assert too_late.accel_upper is None
    assert too_late.still_upper is None
    assert too_late.twist_deg is None
    assert too_late.gyro_lower == lower.gyro_mag
    assert too_late.btn0 == 1

    too_early = align(
        TimedPacket(upper, 2.0),
        TimedPacket(lower, 2.0 - window_s - 0.001),
        window_s,
    )
    assert too_early.missing_module == "lower"
    assert too_early.gyro_lower is None
    assert too_early.accel_lower is None
    assert too_early.btn0 is None
    assert too_early.btn1 is None
    assert too_early.twist_deg is None
    assert too_early.gyro_upper == upper.gyro_mag


def test_missing_module_is_marked_and_not_filled():
    upper = _packet(MODULE_UPPER, gyro_mag=0.25, flags=FLAG_STILL)
    frame = align(TimedPacket(upper, 0.5), None, _window_s())
    assert frame.missing_module == "lower"
    assert frame.gyro_upper == 0.25
    assert frame.still_upper is True
    assert frame.gyro_lower is None
    assert frame.accel_lower is None
    assert frame.still_lower is None
    assert frame.btn0 is None
    assert frame.btn1 is None
    assert frame.twist_deg is None

    lower = _packet(MODULE_LOWER, gyro_mag=1.5, btn0=0, btn1=1)
    only_lower = Aligner(window_s=_window_s()).push(lower, 0.8)
    assert only_lower.missing_module == "upper"
    assert only_lower.gyro_lower == 1.5
    assert only_lower.btn1 == 1
    assert only_lower.gyro_upper is None
    assert only_lower.accel_upper is None
    assert only_lower.still_upper is None
    assert only_lower.twist_deg is None


def pytest_ms(t_host_s):
    return t_host_s * 1000.0


def pytest_approx(value):
    import pytest

    return pytest.approx(value, abs=1e-6)


def test_swing_pair_reports_about_thirty_degrees():
    half = math.radians(30.0) * 0.5
    upper = _packet(MODULE_UPPER, gyro_mag=1.0, qw=math.cos(half), qz=math.sin(half))
    lower = _packet(MODULE_LOWER, gyro_mag=0.0, flags=FLAG_STILL)
    frame = align(TimedPacket(upper, 0.0), TimedPacket(lower, 0.0), _window_s())
    assert frame.missing_module is None
    assert frame.twist_deg == pytest_approx(30.0)
