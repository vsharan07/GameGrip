"""GameGrip sample packet: 40 bytes, little-endian, no padding.

Wire layout (must match firmware/upper/packet.h):
    magic u16, ver u8, module_id u8, seq u32, t_ms u32,
    qw f32, qx f32, qy f32, qz f32, gyro_mag f32, accel_mag f32,
    btn0 u8, btn1 u8, flags u8, rssi i8
"""

import struct
from dataclasses import dataclass

PACKET_SIZE = 40
MAGIC = 0x4747
VERSION = 1

MODULE_UPPER = 0
MODULE_LOWER = 1

FLAG_FAKE_IMU = 1 << 0
FLAG_STILL = 1 << 1
FLAG_FUSION_OK = 1 << 2
FLAG_LOW_BATT = 1 << 3

# H u16, B u8, B u8, I u32, I u32, 4f quaternion, f gyro, f accel, B B B, b i8
_STRUCT = struct.Struct("<HBBII4fffBBBb")

if _STRUCT.size != PACKET_SIZE:
    raise RuntimeError(
        f"GameGrip packet format is {_STRUCT.size} bytes, expected {PACKET_SIZE}"
    )


@dataclass
class GGPacket:
    magic: int
    ver: int
    module_id: int
    seq: int
    t_ms: int
    qw: float
    qx: float
    qy: float
    qz: float
    gyro_mag: float
    accel_mag: float
    btn0: int
    btn1: int
    flags: int
    rssi: int


def pack(p: GGPacket) -> bytes:
    """Serialize one sample. Always returns PACKET_SIZE bytes."""
    return _STRUCT.pack(
        p.magic,
        p.ver,
        p.module_id,
        p.seq,
        p.t_ms,
        p.qw,
        p.qx,
        p.qy,
        p.qz,
        p.gyro_mag,
        p.accel_mag,
        p.btn0,
        p.btn1,
        p.flags,
        p.rssi,
    )


def low_batt(flags: int) -> bool:
    """Read flags bit3. True when the sender reports a low cell."""
    return bool(flags & FLAG_LOW_BATT)


def unpack(b: bytes) -> GGPacket:
    """Parse one sample. Rejects the wrong length, magic, or version."""
    if len(b) != PACKET_SIZE:
        raise ValueError(f"packet length {len(b)} != {PACKET_SIZE}")
    (
        magic,
        ver,
        module_id,
        seq,
        t_ms,
        qw,
        qx,
        qy,
        qz,
        gyro_mag,
        accel_mag,
        btn0,
        btn1,
        flags,
        rssi,
    ) = _STRUCT.unpack(b)
    if magic != MAGIC:
        raise ValueError(f"bad magic 0x{magic:04x}")
    if ver != VERSION:
        raise ValueError(f"bad version {ver}")
    return GGPacket(
        magic=magic,
        ver=ver,
        module_id=module_id,
        seq=seq,
        t_ms=t_ms,
        qw=qw,
        qx=qx,
        qy=qy,
        qz=qz,
        gyro_mag=gyro_mag,
        accel_mag=accel_mag,
        btn0=btn0,
        btn1=btn1,
        flags=flags,
        rssi=rssi,
    )
