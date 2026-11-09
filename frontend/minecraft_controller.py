"""The website's directional calibration and the existing desktop controller.

The bridge owns the serial port; this adapter never opens a second reader.
Nothing is injected until the player presses Launch and its countdown finishes.
"""

import sys
import threading
from pathlib import Path

import numpy as np

import dtw_detector as dtw

HOST_DIR = Path(__file__).resolve().parents[1] / "host"
if str(HOST_DIR) not in sys.path:
    sys.path.insert(0, str(HOST_DIR))

from console import build_app
from look_calibration import LookCalibrationError, apply_to_aim, build_document, save_document
from safety import Safety
from sensor.csv_stream import parse_stream_line

LAUNCH_SECONDS = 5
STALE_SECONDS = 1.0


# How much of a turn must land on one sensor axis. 0.7 accepts a sensor
# mounted up to about 45 degrees off; below that the turn is truly diagonal.
MIN_AXIS_SHARE = 0.7
MIN_STROKE_DEG = 5.0
# Why the most recent stroke was rejected, for a useful error message.
last_stroke_problem = None


def stroke_axis(samples, bias, floor_dps):
    """Use the outward stroke, so returning to centre cannot cancel its sign."""
    global last_stroke_problem
    last_stroke_problem = None
    if len(samples) < 4:
        last_stroke_problem = "only %d samples were recorded" % len(samples)
        return None
    gyro = np.asarray(samples, dtype=float)[:, 3:6]
    angle, direction = dtw.main_stroke(gyro, np.asarray(bias), floor_dps, 50.0)
    if direction is None or angle < MIN_STROKE_DEG:
        last_stroke_problem = "it measured only %.0f degrees" % angle
        return None
    axis = int(np.argmax(np.abs(direction)))
    # An ambiguous diagonal should be retried rather than silently mis-mapped.
    if abs(direction[axis]) < MIN_AXIS_SHARE:
        last_stroke_problem = (
            "it was diagonal: %.0f degrees split across gx/gy/gz as %s. "
            "Straighten the sensor on the toy, or turn without twisting"
            % (angle, "/".join("%d%%" % round(abs(v) * 100) for v in direction)))
        return None
    return axis, float(direction[axis])


class MinecraftController:
    def __init__(self, send, app=None, safety=None, controls_path=None, look_path=None):
        self.send = send
        # The existing listener logs every key by default. The website only
        # needs its emergency-stop latch, so ordinary keys are not logged.
        self.safety = safety if safety is not None else Safety(output=lambda _: None)
        self.app = app if app is not None else build_app(self.safety, controls_path=controls_path)
        self.look_path = look_path
        self.lock = threading.RLock()
        self.closed = False
        self.calibrated = False
        self.launch_at = None
        self.last_count = None
        self.last_sample = None

    @property
    def live(self):
        return self.app.session.live and not self.closed

    def calibrate(self, rest, right, down):
        # dtw.record returns NumPy arrays; the host's recording functions use
        # ordinary sequences (including truth checks in gyro_bias/gyro_noise).
        rest, right, down = ([tuple(row) for row in rows] for rows in (rest, right, down))
        try:
            document = build_document(rest, right, down, vertical_direction="down", axis_picker=stroke_axis)
        except LookCalibrationError as error:
            if last_stroke_problem:
                raise LookCalibrationError("%s (%s.)" % (error, last_stroke_problem)) from error
            raise
        with self.lock:
            if self.closed:
                return
            apply_to_aim(document, self.app.controls.aim)
            self.app.controls.game = "minecraft"
            save_document(document, self.look_path)
            self.app.controls.save(self.app.controls_path)
            self.calibrated = True
        return document

    def launch(self, now):
        with self.lock:
            if self.closed or not self.calibrated or self.safety.stopped:
                raise RuntimeError("Recalibrate before launching the controller.")
            if self.live or self.launch_at is not None:
                return
            if self.last_sample is None or now - self.last_sample >= STALE_SECONDS:
                raise RuntimeError("No fresh sensor data. Reconnect the ESP32 and recalibrate.")
            try:
                listening = self.safety.listen()
            except Exception as exc:
                self.safety.stop()
                raise RuntimeError("The emergency-stop key listener could not start: %s" % exc) from exc
            if not listening:
                self.safety.stop()
                raise RuntimeError("The emergency-stop key listener could not start.")
            self.launch_at = now + LAUNCH_SECONDS
            self.last_count = None
            self.send({"type": "controller-starting", "seconds": LAUNCH_SECONDS})

    def tick(self, now, line):
        """Run on every serial read, including timeouts and diagnostic lines."""
        frame = parse_stream_line(line)
        with self.lock:
            if self.closed:
                return
            if self.safety.stopped:
                self._stop_locked("Emergency stop: Esc pressed three times.")
                return
            if frame is not None:
                self.last_sample = now
            if self.last_sample is None or now - self.last_sample >= STALE_SECONDS:
                message = "The sensor stream stopped. Inputs have been released; reconnect and recalibrate."
                self._stop_locked(message)
                raise RuntimeError(message)
            if self.launch_at is not None:
                remaining = max(0, int(np.ceil(self.launch_at - now)))
                if remaining:
                    if remaining != self.last_count:
                        self.last_count = remaining
                        self.send({"type": "controller-count", "n": remaining})
                elif frame is not None:
                    self.app.session.mouse.reset_motion()
                    self.app.session.set_live(True)
                    self.launch_at = None
                    self.send({"type": "controller-live"})
            if self.live and frame is not None:
                sample, button = frame
                self.app.session.push(sample, now, button=button)
                if not self.app.session.mouse.live:
                    message = "Mouse output failed. Inputs have been released; run calibration again."
                    self._stop_locked(message)
                    raise RuntimeError(message)

    def _stop_locked(self, reason):
        self.closed = True
        self.launch_at = None
        try:
            self.app.session.set_live(False)
            self.app.session.mouse.reset_motion()
        finally:
            self.safety.stop()
        self.send({"type": "controller-stopped", "message": reason})

    def stop(self, reason="Controller stopped."):
        with self.lock:
            if not self.closed:
                self._stop_locked(reason)
