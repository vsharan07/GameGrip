"""Receiver tests. A fake clock stands in for time.monotonic; no sockets, no sleep."""

from gg_packet import (
    FLAG_FAKE_IMU,
    FLAG_LOW_BATT,
    FLAG_STILL,
    MAGIC,
    MODULE_LOWER,
    MODULE_UPPER,
    VERSION,
    GGPacket,
    pack,
)
from receiver import DOWN_AFTER_S, Receiver


class FakeClock(object):
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


def _packet(seq=0, module_id=MODULE_UPPER, flags=FLAG_STILL, t_ms=0, ver=VERSION, magic=MAGIC):
    return pack(
        GGPacket(
            magic=magic,
            ver=ver,
            module_id=module_id,
            seq=seq,
            t_ms=t_ms,
            qw=1.0,
            qx=0.0,
            qy=0.0,
            qz=0.0,
            gyro_mag=0.0,
            accel_mag=9.81,
            btn0=0,
            btn1=0,
            flags=flags,
            rssi=0,
        )
    )


def _receiver(clock):
    return Receiver(bind=False, clock=clock)


def test_sequence_gap_counts_jumps_not_steps():
    clock = FakeClock()
    rx = _receiver(clock)
    for seq in (0, 1, 2):
        rx.ingest(_packet(seq=seq))
    assert rx.gap_count(MODULE_UPPER) == 0

    # One jump, however wide, is one gap. The following +1 is not another.
    rx.ingest(_packet(seq=10))
    rx.ingest(_packet(seq=11))
    assert rx.gap_count(MODULE_UPPER) == 1

    # uint32 wrap is +1, so it is not a gap. A duplicate is.
    rx.ingest(_packet(seq=0xFFFFFFFF))
    assert rx.gap_count(MODULE_UPPER) == 2
    rx.ingest(_packet(seq=0))
    assert rx.gap_count(MODULE_UPPER) == 2
    rx.ingest(_packet(seq=0))
    assert rx.gap_count(MODULE_UPPER) == 3

    rx.ingest(_packet(seq=0, module_id=MODULE_LOWER))
    rx.ingest(_packet(seq=2, module_id=MODULE_LOWER))
    assert rx.gap_count(MODULE_LOWER) == 1
    assert rx.gap_count(MODULE_UPPER) == 3
    assert rx.packet_count(MODULE_UPPER) == 8
    assert rx.packet_count(MODULE_LOWER) == 2


def test_bad_packets_are_rejected():
    clock = FakeClock()
    rx = _receiver(clock)
    rx.ingest(_packet(seq=0))

    rx.ingest(b"\x00" * 10)
    bad_magic = bytearray(_packet(seq=1))
    bad_magic[0] = 0
    bad_magic[1] = 0
    rx.ingest(bytes(bad_magic))
    rx.ingest(_packet(seq=1, ver=9))

    assert rx.bad_count == 3
    assert rx.packet_count(MODULE_UPPER) == 1
    assert rx.gap_count(MODULE_UPPER) == 0
    assert rx.is_down(MODULE_UPPER) is False

    # The rejected datagram is not part of the sequence.
    rx.ingest(_packet(seq=1))
    assert rx.packet_count(MODULE_UPPER) == 2
    assert rx.gap_count(MODULE_UPPER) == 0
    assert rx.bad_count == 3


def test_down_after_200ms():
    clock = FakeClock()
    rx = _receiver(clock)
    assert rx.is_down(MODULE_UPPER)
    assert rx.is_down(MODULE_LOWER)
    assert rx.status_line() == "upper DOWN | lower DOWN"

    # Device t_ms is not the host clock. Silence is measured from arrival.
    rx.ingest(_packet(seq=0, t_ms=999999, flags=FLAG_FAKE_IMU | FLAG_STILL))
    assert rx.is_down(MODULE_UPPER) is False
    assert rx.is_down(MODULE_LOWER) is True
    assert rx.status_line() == "upper 1Hz gaps=0 still=1 | lower DOWN"

    clock.now = DOWN_AFTER_S - 0.001
    assert rx.is_down(MODULE_UPPER) is False

    clock.now = DOWN_AFTER_S
    assert rx.is_down(MODULE_UPPER) is True
    assert rx.status_line() == "upper DOWN | lower DOWN"

    # 2.0 and 2.25 are exact binary values. 250 ms is past the timeout
    # without adding the inexact 0.2 literal to a non-zero base.
    clock.now = 2.0
    rx.ingest(_packet(seq=1, module_id=MODULE_LOWER, flags=FLAG_FAKE_IMU))
    assert rx.is_down(MODULE_LOWER) is False
    assert rx.is_down(MODULE_UPPER) is True
    clock.now = 2.125
    assert rx.is_down(MODULE_LOWER) is False
    clock.now = 2.25
    assert rx.is_down(MODULE_LOWER) is True


def test_low_batt_flag_is_reported():
    clock = FakeClock()
    rx = _receiver(clock)
    rx.ingest(_packet(seq=0, flags=FLAG_STILL | FLAG_LOW_BATT))
    assert rx.status_line() == "upper 1Hz gaps=0 still=1 low_batt=1 | lower DOWN"


def test_hz_window_and_status_line():
    clock = FakeClock()
    rx = _receiver(clock)
    for seq in (0, 1, 2, 4, 5, 6, 7, 8, 9, 10):
        rx.ingest(_packet(seq=seq, flags=FLAG_FAKE_IMU))
    for seq in range(3):
        rx.ingest(_packet(seq=seq, module_id=MODULE_LOWER, flags=FLAG_FAKE_IMU | FLAG_STILL))
    assert rx.status_line() == "upper 10Hz gaps=1 still=0 | lower 3Hz gaps=0 still=1"

    # A sample older than the 1-second window drops out while a newer one keeps the link up.
    clock.now = 0.0
    rx.ingest(_packet(seq=0, module_id=MODULE_UPPER))
    clock.now = 0.9
    rx.ingest(_packet(seq=1, module_id=MODULE_UPPER))
    clock.now = 1.0
    assert rx.is_down(MODULE_UPPER) is False
    assert rx.hz(MODULE_UPPER) == 1
