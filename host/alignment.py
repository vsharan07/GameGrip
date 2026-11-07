"""Pair the latest upper and lower packets by host receive time.

Packets further apart than the alignment window are not a pair. The older
module is reported missing and its sample is left out of the frame.
"""

from dataclasses import dataclass
from typing import Optional

from config import load_config
from features import build_frame
from gg_packet import MODULE_LOWER, MODULE_UPPER, GGPacket


@dataclass(frozen=True)
class TimedPacket:
    packet: GGPacket
    t_host_s: float


class Aligner:
    """Remember the latest packet from each module and align those two."""

    def __init__(self, window_s: Optional[float] = None) -> None:
        if window_s is None:
            window_s = load_config().alignment_window_ms / 1000.0
        self.window_s = window_s
        self._upper: Optional[TimedPacket] = None
        self._lower: Optional[TimedPacket] = None

    def push(self, packet: GGPacket, t_host_s: float):
        timed = TimedPacket(packet, t_host_s)
        if packet.module_id == MODULE_UPPER:
            self._upper = timed
        elif packet.module_id == MODULE_LOWER:
            self._lower = timed
        else:
            raise ValueError("unknown module_id %s" % packet.module_id)
        return align(self._upper, self._lower, self.window_s)


def _within_window(t_a_s: float, t_b_s: float, window_s: float) -> bool:
    # Compare in milliseconds. Adding the window onto a host timestamp is not
    # exact in binary floating point, and that dust must not reject a pair
    # that lands on the configured boundary.
    delta_ms = abs(t_a_s - t_b_s) * 1000.0
    window_ms = window_s * 1000.0
    return delta_ms <= window_ms + 1e-6


def align(upper: Optional[TimedPacket], lower: Optional[TimedPacket], window_s: float):
    """Return a full frame, or a partial frame that names the missing module."""
    if upper is None and lower is None:
        raise ValueError("no packets to align")
    if upper is None:
        return build_frame(None, lower.packet, lower.t_host_s, "upper")
    if lower is None:
        return build_frame(upper.packet, None, upper.t_host_s, "lower")
    if _within_window(upper.t_host_s, lower.t_host_s, window_s):
        newer_s = upper.t_host_s if upper.t_host_s >= lower.t_host_s else lower.t_host_s
        return build_frame(upper.packet, lower.packet, newer_s, None)
    if upper.t_host_s >= lower.t_host_s:
        return build_frame(upper.packet, None, upper.t_host_s, "lower")
    return build_frame(None, lower.packet, lower.t_host_s, "upper")
