"""Relative arm features built from one upper packet and one lower packet.

A missing side stays None. Nothing in this module invents a quaternion,
gyro, acceleration, button, or still flag for a module that was not paired.
"""

import math
from dataclasses import dataclass
from typing import Optional, Sequence

from gg_packet import FLAG_STILL, GGPacket


@dataclass(frozen=True)
class FeatureFrame:
    t_host_ms: float
    twist_deg: Optional[float]
    gyro_upper: Optional[float]
    gyro_lower: Optional[float]
    accel_upper: Optional[float]
    accel_lower: Optional[float]
    btn0: Optional[int]
    btn1: Optional[int]
    still_upper: Optional[bool]
    still_lower: Optional[bool]
    missing_module: Optional[str]


def relative_twist_deg(q_upper: Sequence[float], q_lower: Sequence[float]) -> float:
    """Angle in degrees between two orientations.

    Each quaternion is (w, x, y, z). The relative rotation is
    conjugate(q_upper) * q_lower. q and -q are the same pose.
    """
    relative = _multiply(_conjugate(_normalize(q_upper)), _normalize(q_lower))
    cosine_half = abs(relative[0])
    if cosine_half > 1.0:
        cosine_half = 1.0
    return math.degrees(2.0 * math.acos(cosine_half))


def build_frame(upper, lower, t_host_s, missing_module):
    """Build one frame. Pass None for a module that is not part of the pair."""
    if missing_module == "upper":
        upper = None
    elif missing_module == "lower":
        lower = None
    twist = None
    if upper is not None and lower is not None:
        twist = relative_twist_deg(_quaternion(upper), _quaternion(lower))
    return FeatureFrame(
        t_host_ms=t_host_s * 1000.0,
        twist_deg=twist,
        gyro_upper=None if upper is None else upper.gyro_mag,
        gyro_lower=None if lower is None else lower.gyro_mag,
        accel_upper=None if upper is None else upper.accel_mag,
        accel_lower=None if lower is None else lower.accel_mag,
        btn0=None if lower is None else lower.btn0,
        btn1=None if lower is None else lower.btn1,
        still_upper=None if upper is None else _still(upper),
        still_lower=None if lower is None else _still(lower),
        missing_module=missing_module,
    )


def _quaternion(packet: GGPacket):
    return (packet.qw, packet.qx, packet.qy, packet.qz)


def _still(packet: GGPacket) -> bool:
    return bool(packet.flags & FLAG_STILL)


def _normalize(quaternion: Sequence[float]):
    if len(quaternion) != 4:
        raise ValueError("quaternion must have 4 components")
    norm = math.sqrt(sum(component * component for component in quaternion))
    if norm == 0.0:
        raise ValueError("quaternion has zero length")
    return tuple(component / norm for component in quaternion)


def _conjugate(quaternion):
    w, x, y, z = quaternion
    return (w, -x, -y, -z)


def _multiply(left, right):
    lw, lx, ly, lz = left
    rw, rx, ry, rz = right
    return (
        lw * rw - lx * rx - ly * ry - lz * rz,
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
    )
