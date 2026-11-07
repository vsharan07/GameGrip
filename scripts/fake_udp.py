"""Send synthetic GameGrip packets to 127.0.0.1.

Port and sample rate come from host/config/default.json.

Idle is identity orientation with FAKE_IMU and STILL set. Every 2.5 seconds
each module takes a 1-second swing. Gyro magnitude and the z-rotation are
both sine pulses specified on their own, so gyro is not the derivative of
the quaternion. Module 1 lags module 0 by 0.5 seconds.
"""

import argparse
import errno
import math
import socket
import sys
import time
from pathlib import Path

HOST_DIR = Path(__file__).resolve().parents[1] / "host"
if str(HOST_DIR) not in sys.path:
    sys.path.insert(0, str(HOST_DIR))

from config import load_config  # noqa: E402
from gg_packet import (  # noqa: E402
    FLAG_FAKE_IMU,
    FLAG_STILL,
    MAGIC,
    MODULE_LOWER,
    MODULE_UPPER,
    VERSION,
    GGPacket,
    pack,
)
from receiver import DEFAULT_PORT  # noqa: E402

DEST_HOST = "127.0.0.1"
SEND_HZ = load_config().sample_rate_hz
SWING_PERIOD_S = 2.5
SWING_DURATION_S = 1.0
LOWER_PHASE_OFFSET_S = 0.5
SWING_MAX_RAD = math.radians(30.0)
SWING_PEAK_GYRO = 1.0
IDLE_ACCEL = 9.81
DROP_LOWER_AFTER_S = 10.0
_SEQ_MASK = 0xFFFFFFFF

_PHASE_OFFSET = {
    MODULE_UPPER: 0.0,
    MODULE_LOWER: LOWER_PHASE_OFFSET_S,
}


class Motion(object):
    def __init__(self, qw, qx, qy, qz, gyro_mag, accel_mag, flags):
        self.qw = qw
        self.qx = qx
        self.qy = qy
        self.qz = qz
        self.gyro_mag = gyro_mag
        self.accel_mag = accel_mag
        self.flags = flags


def motion_at(t_s, module_id):
    """Orientation and gyro for one module at sender time t_s."""
    local = (t_s - _PHASE_OFFSET[module_id]) % SWING_PERIOD_S
    if local >= SWING_DURATION_S:
        return Motion(1.0, 0.0, 0.0, 0.0, 0.0, IDLE_ACCEL, FLAG_FAKE_IMU | FLAG_STILL)
    # u goes 0 → 1 across the swing. sin(pi*u) is 0 at both ends and 1 in the middle.
    pulse = math.sin(math.pi * (local / SWING_DURATION_S))
    half = (SWING_MAX_RAD * pulse) * 0.5
    return Motion(
        math.cos(half),
        0.0,
        0.0,
        math.sin(half),
        SWING_PEAK_GYRO * pulse,
        IDLE_ACCEL,
        FLAG_FAKE_IMU,
    )


def build_packet(module_id, seq, t_ms, motion):
    return pack(
        GGPacket(
            magic=MAGIC,
            ver=VERSION,
            module_id=module_id,
            seq=seq,
            t_ms=t_ms,
            qw=motion.qw,
            qx=motion.qx,
            qy=motion.qy,
            qz=motion.qz,
            gyro_mag=motion.gyro_mag,
            accel_mag=motion.accel_mag,
            btn0=0,
            btn1=0,
            flags=motion.flags,
            rssi=0,
        )
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Send synthetic GameGrip UDP packets.")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="Destination UDP port (default %(default)s)",
    )
    parser.add_argument(
        "--drop-lower",
        action="store_true",
        help="Stop sending module 1 after 10 seconds",
    )
    return parser.parse_args(argv)


def run(port, drop_lower):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dest = (DEST_HOST, port)
    seq = {MODULE_UPPER: 0, MODULE_LOWER: 0}
    period = 1.0 / SEND_HZ
    start = time.monotonic()
    next_tick = start
    note = "sending %d Hz to %s:%d" % (SEND_HZ, DEST_HOST, port)
    if drop_lower:
        note += " (module 1 stops after %gs)" % DROP_LOWER_AFTER_S
    print(note, flush=True)
    try:
        while True:
            elapsed = time.monotonic() - start
            _send(sock, dest, MODULE_UPPER, elapsed, seq)
            if not (drop_lower and elapsed >= DROP_LOWER_AFTER_S):
                _send(sock, dest, MODULE_LOWER, elapsed, seq)
            next_tick += period
            delay = next_tick - time.monotonic()
            if delay > 0.0:
                time.sleep(delay)
            else:
                next_tick = time.monotonic()
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()


def _send(sock, dest, module_id, elapsed, seq):
    motion = motion_at(elapsed, module_id)
    payload = build_packet(
        module_id,
        seq[module_id],
        int(elapsed * 1000.0) & _SEQ_MASK,
        motion,
    )
    try:
        sock.sendto(payload, dest)
    except OSError as exc:
        # BSD reports the previous ICMP unreachable on a later send.
        if exc.errno != errno.ECONNREFUSED:
            raise
    seq[module_id] = (seq[module_id] + 1) & _SEQ_MASK


def main(argv=None):
    args = parse_args(argv)
    run(args.port, args.drop_lower)


if __name__ == "__main__":
    main()
