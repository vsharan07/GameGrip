"""Non-blocking UDP listener for GameGrip samples.

Tracks upper (module 0) and lower (module 1) separately. Pass a fake clock
and bind=False in tests so nothing opens a socket or sleeps.
"""

import select
import socket
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, Optional

from config import load_config
from gg_packet import FLAG_STILL, MODULE_LOWER, MODULE_UPPER, GGPacket, low_batt, unpack

_CONFIG = load_config()

DEFAULT_PORT = _CONFIG.udp_port
BIND_ADDR = "0.0.0.0"
DOWN_AFTER_S = _CONFIG.stale_timeout_ms / 1000.0
HZ_WINDOW_S = 1.0
RECV_BYTES = 2048

# seq is a uint32. The next sample is +1, including the wrap to 0.
_SEQ_MASK = 0xFFFFFFFF

_LINK_NAME = {
    MODULE_UPPER: "upper",
    MODULE_LOWER: "lower",
}


@dataclass
class ModuleState:
    last_packet: Optional[GGPacket] = None
    packet_count: int = 0
    gap_count: int = 0
    last_arrival: Optional[float] = None
    last_seq: Optional[int] = None
    arrivals: Deque[float] = field(default_factory=deque)


class Receiver:
    def __init__(
        self,
        port: int = DEFAULT_PORT,
        clock: Optional[Callable[[], float]] = None,
        bind: bool = True,
    ) -> None:
        self.port = port
        self.clock = clock if clock is not None else _monotonic
        self.bad_count = 0
        self._modules: Dict[int, ModuleState] = {
            MODULE_UPPER: ModuleState(),
            MODULE_LOWER: ModuleState(),
        }
        self._sock: Optional[socket.socket] = _open_socket(port) if bind else None
        # A few seconds of samples so a calibrator can see every packet
        # without the status loop storing an unbounded history.
        self._inbox = deque(maxlen=int(_CONFIG.sample_rate_hz * 4) * 2)

    def ingest(self, data: bytes) -> None:
        """Accept one datagram. Unpack failures increment bad_count."""
        try:
            packet = unpack(data)
        except ValueError:
            self.bad_count += 1
            return
        state = self._modules.get(packet.module_id)
        if state is None:
            return
        now = self.clock()
        if state.last_seq is not None:
            expected = (state.last_seq + 1) & _SEQ_MASK
            if (packet.seq & _SEQ_MASK) != expected:
                state.gap_count += 1
        state.last_seq = packet.seq & _SEQ_MASK
        state.last_packet = packet
        state.last_arrival = now
        state.packet_count += 1
        state.arrivals.append(now)
        self._inbox.append((packet, now))
        self._prune(state, now)

    def take_packets(self):
        """Return packets accepted since the last call, oldest first."""
        items = list(self._inbox)
        self._inbox.clear()
        return items

    def poll(self, timeout: float = 0.0) -> int:
        """Drain datagrams that are ready. timeout is how long select may wait."""
        sock = self._sock
        if sock is None:
            return 0
        readable, _, _ = select.select([sock], [], [], max(0.0, timeout))
        if not readable:
            return 0
        received = 0
        while True:
            try:
                data, _addr = sock.recvfrom(RECV_BYTES)
            except BlockingIOError:
                break
            self.ingest(data)
            received += 1
        return received

    def is_down(self, module_id: int) -> bool:
        """True when this module has been silent for DOWN_AFTER_S, or never seen."""
        last = self._modules[module_id].last_arrival
        if last is None:
            return True
        return (self.clock() - last) >= DOWN_AFTER_S

    def hz(self, module_id: int) -> int:
        """Packets whose arrival time falls inside the last HZ_WINDOW_S."""
        state = self._modules[module_id]
        self._prune(state, self.clock())
        return len(state.arrivals)

    def gap_count(self, module_id: int) -> int:
        return self._modules[module_id].gap_count

    def packet_count(self, module_id: int) -> int:
        return self._modules[module_id].packet_count

    def status_line(self) -> str:
        return " | ".join(self._format_module(module_id) for module_id in _LINK_NAME)

    def close(self) -> None:
        sock = self._sock
        self._sock = None
        if sock is not None:
            sock.close()

    def _format_module(self, module_id: int) -> str:
        name = _LINK_NAME[module_id]
        if self.is_down(module_id):
            return "%s DOWN" % name
        packet = self._modules[module_id].last_packet
        still = 1 if packet is not None and (packet.flags & FLAG_STILL) else 0
        text = "%s %dHz gaps=%d still=%d" % (
            name,
            self.hz(module_id),
            self.gap_count(module_id),
            still,
        )
        if packet is not None and low_batt(packet.flags):
            text += " low_batt=1"
        return text

    @staticmethod
    def _prune(state: ModuleState, now: float) -> None:
        # Drop arrivals on the far edge so the window is (now - 1s, now].
        cutoff = now - HZ_WINDOW_S
        arrivals = state.arrivals
        while arrivals and arrivals[0] <= cutoff:
            arrivals.popleft()


def _monotonic() -> float:
    return time.monotonic()


def _open_socket(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((BIND_ADDR, port))
        sock.setblocking(False)
    except Exception:
        sock.close()
        raise
    return sock
