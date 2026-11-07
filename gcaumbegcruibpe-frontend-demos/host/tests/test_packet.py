"""Tests for the 40-byte GameGrip packet."""

import struct

import pytest

from gg_packet import (
    FLAG_FAKE_IMU,
    FLAG_FUSION_OK,
    FLAG_LOW_BATT,
    GGPacket,
    MAGIC,
    PACKET_SIZE,
    low_batt,
    pack,
    unpack,
)


def _f32(value: float) -> float:
    """Value as it survives a little-endian f32 on the wire."""
    return struct.unpack("<f", struct.pack("<f", value))[0]

# Independent of pack(): struct.pack("<HBBII4fffBBBb", 0x4747, 1, 1,
# 0x01020304, 0x05060708, 1.0, 0.0, -0.5, 0.25, 2.0, 9.81, 1, 0, 0x05, -42)
GOLDEN_HEX = "4747010104030201080706050000803f00000000000000bf0000803e00000040c3f51c41010005d6"


def _sample() -> GGPacket:
    return GGPacket(
        magic=MAGIC,
        ver=1,
        module_id=1,
        seq=0x01020304,
        t_ms=0x05060708,
        qw=1.0,
        qx=0.0,
        qy=-0.5,
        qz=0.25,
        gyro_mag=2.0,
        accel_mag=_f32(9.81),
        btn0=1,
        btn1=0,
        flags=FLAG_FAKE_IMU | FLAG_FUSION_OK,
        rssi=-42,
    )


def test_round_trip():
    original = _sample()
    raw = pack(original)
    assert len(raw) == PACKET_SIZE == 40
    assert unpack(raw) == original


def test_golden_bytes():
    raw = pack(_sample())
    assert raw.hex() == GOLDEN_HEX
    assert len(bytes.fromhex(GOLDEN_HEX)) == 40


def test_short_buffer_rejected():
    with pytest.raises(ValueError):
        unpack(b"\x00" * 10)


def test_bad_magic_rejected():
    raw = bytearray(pack(_sample()))
    raw[0] = 0x00
    raw[1] = 0x00
    with pytest.raises(ValueError):
        unpack(bytes(raw))


def test_low_batt_is_flags_bit3():
    assert FLAG_LOW_BATT == 1 << 3
    original = _sample()
    assert low_batt(original.flags) is False
    original.flags = original.flags | FLAG_LOW_BATT
    parsed = unpack(pack(original))
    assert parsed.flags == FLAG_FAKE_IMU | FLAG_FUSION_OK | FLAG_LOW_BATT
    assert low_batt(parsed.flags) is True
    assert parsed.magic == original.magic
    assert parsed.qw == original.qw
    assert len(pack(parsed)) == PACKET_SIZE


def test_bad_version_rejected():
    raw = bytearray(pack(_sample()))
    raw[2] = 9
    with pytest.raises(ValueError):
        unpack(bytes(raw))
