"""Three-swing calibration for the rotation detector.

The saved file is versioned. Recalibration replaces that file and the live
tracker. It does not require a new process. This is not a DTW template.
"""

import json
from pathlib import Path

from recognition.rotation import (
    DetectorSettings,
    RotationTracker,
    flat_top,
    main_stroke,
    smoothed_speeds,
    vmean,
    vnorm,
    vscale,
    vstd_speed,
    vadd,
)

FORMAT_VERSION = 1
GYRO_SLICE = slice(3, 6)
DEFAULT_PATH = Path(__file__).resolve().parent / "data" / "swing_calibration.json"

# Three swing directions agree when their average unit vector is at least this long.
_AXIS_CONSISTENCY = 0.5
# Rest must be clearly quieter than the weakest calibration swing.
_REST_TO_SWING = 3.0
_SMOOTH_SKIP = 5


class SwingCalibrationError(Exception):
    """The three swings could not produce a usable detector."""


class CalibrationFormatError(Exception):
    """The saved calibration is missing or from another version."""


def gyro_of(sample):
    return tuple(sample[GYRO_SLICE])


def rest_noise(gyro_rows, bias, alpha):
    speeds = smoothed_speeds(gyro_rows, bias, alpha)
    usable = speeds[_SMOOTH_SKIP:] if len(speeds) > _SMOOTH_SKIP else speeds
    if not usable:
        return 0.0
    return (sum(usable) / float(len(usable))) + 4.0 * vstd_speed(usable)


def build_calibration(rest_rows, swing_rows, hz, settings):
    """rest_rows and each swing are lists of gyro triples in deg/s."""
    if hz <= 0.0:
        raise SwingCalibrationError("Sample rate must be positive.")
    if len(swing_rows) != 3:
        raise SwingCalibrationError("Calibration needs exactly 3 swings.")
    bias = vmean(rest_rows)
    noise = rest_noise(rest_rows, bias, settings.alpha)
    peaks = []
    for rows in swing_rows:
        speeds = smoothed_speeds(rows, bias, settings.alpha)
        peaks.append(max(speeds) if speeds else 0.0)
    speed_gate = max(noise, settings.slow_fraction * _median(peaks))
    strokes = [
        main_stroke(rows, bias, speed_gate, hz, settings.alpha) for rows in swing_rows
    ]
    angles = [stroke[0] for stroke in strokes]
    stuck = [value for value in (flat_top(rows) for rows in swing_rows) if value is not None]
    swing_axis = _swing_axis(strokes, settings)
    if min(peaks) < noise * _REST_TO_SWING:
        raise SwingCalibrationError(
            "The sensor is almost as busy sitting still as it is swinging "
            "(still noise %.0f vs weakest swing %.0f)." % (noise, min(peaks))
        )
    full_swing = _median(angles)
    if full_swing <= 0.0:
        raise SwingCalibrationError("The calibration swings had no measurable rotation.")
    return {
        "version": FORMAT_VERSION,
        "units": {"accel": "g", "gyro": "deg/s"},
        "hz": float(hz),
        "bias_dps": list(bias),
        "noise_dps": float(noise),
        "speed_gate_dps": float(speed_gate),
        "full_swing_deg": float(full_swing),
        "swing_axis": None if swing_axis is None else list(swing_axis),
        "peaks_dps": [float(peak) for peak in peaks],
        "swing_rotations_deg": [float(angle) for angle in angles],
        "gyro_saturated_dps": None if not stuck else float(max(stuck)),
        "sensitivity": settings.sensitivity,
        "ignore_backswing": settings.ignore_backswing,
        "backswing_angle_deg": settings.backswing_angle_deg,
    }


def tracker_from_calibration(data, settings, output=None):
    _require_version(data)
    return RotationTracker(
        bias=data["bias_dps"],
        speed_gate=data["speed_gate_dps"],
        full_swing_deg=data["full_swing_deg"],
        hz=data["hz"],
        swing_axis=data.get("swing_axis"),
        settings=settings,
        output=output,
    )


def save_calibration(data, path=None):
    _require_version(data)
    target = Path(path) if path is not None else DEFAULT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, sort_keys=True)
    target.write_text(text + "\n", encoding="utf-8")
    return target


def load_calibration(path=None):
    target = Path(path) if path is not None else DEFAULT_PATH
    if not target.is_file():
        raise CalibrationFormatError("No saved calibration at %s." % target)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CalibrationFormatError("Calibration file is not valid JSON: %s" % target) from exc
    _require_version(data)
    return data


def record_samples(read_sample, seconds, clock):
    """Collect samples until clock() has moved by `seconds`. Returns (rows, hz)."""
    rows = []
    start = clock()
    while clock() - start < seconds:
        sample = read_sample()
        if sample is not None:
            rows.append(sample)
    elapsed = clock() - start
    if elapsed <= 0.0:
        elapsed = seconds
    if len(rows) < 10:
        raise SwingCalibrationError(
            "Only got %d samples in %.1fs. The ESP32 should print "
            "ax,ay,az,gx,gy,gz lines." % (len(rows), seconds)
        )
    return rows, len(rows) / elapsed


def _swing_axis(strokes, settings):
    if not settings.ignore_backswing:
        return None
    directions = [axis for _, axis in strokes if axis is not None]
    if len(directions) < 3:
        return None
    total = (0.0, 0.0, 0.0)
    for direction in directions:
        total = vadd(total, direction)
    consistency = vnorm(total) / 3.0
    if consistency < _AXIS_CONSISTENCY:
        return None
    return vscale(total, 1.0 / vnorm(total))


def _median(values):
    ordered = sorted(float(value) for value in values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


def _require_version(data):
    if not isinstance(data, dict) or data.get("version") != FORMAT_VERSION:
        raise CalibrationFormatError(
            "Calibration version %s is not supported. Expected %s."
            % (None if not isinstance(data, dict) else data.get("version"), FORMAT_VERSION)
        )
    for key in ("bias_dps", "speed_gate_dps", "full_swing_deg", "hz"):
        if key not in data:
            raise CalibrationFormatError("Calibration is missing %s." % key)


def load_serial_settings(path=None):
    """Detector knobs. Missing keys keep the DetectorSettings defaults."""
    defaults = DetectorSettings()
    if path is None:
        path = Path(__file__).resolve().parent / "config" / "serial.json"
    target = Path(path)
    raw = {}
    if target.is_file():
        raw = json.loads(target.read_text(encoding="utf-8"))
    baud = int(raw.get("baud", 115200))
    settings = DetectorSettings(
        sensitivity=raw.get("sensitivity", defaults.sensitivity),
        ignore_backswing=raw.get("ignore_backswing", defaults.ignore_backswing),
        backswing_angle_deg=raw.get("backswing_angle_deg", defaults.backswing_angle_deg),
        slow_fraction=raw.get("slow_fraction", defaults.slow_fraction),
        forget_seconds=raw.get("forget_seconds", defaults.forget_seconds),
        min_gap_seconds=raw.get("min_gap_seconds", defaults.min_gap_seconds),
        alpha=raw.get("alpha", defaults.alpha),
        rest_seconds=raw.get("rest_seconds", defaults.rest_seconds),
        rest_max_noise_dps=raw.get("rest_max_noise_dps", defaults.rest_max_noise_dps),
        rest_attempts=raw.get("rest_attempts", defaults.rest_attempts),
        swing_window_seconds=raw.get("swing_window_seconds", defaults.swing_window_seconds),
        debug=raw.get("debug", defaults.debug),
        debug_min_fraction=raw.get("debug_min_fraction", defaults.debug_min_fraction),
        stale_timeout_s=raw.get("stale_timeout_s", defaults.stale_timeout_s),
    )
    read_timeout_s = float(raw.get("read_timeout_s", 0.05))
    return baud, read_timeout_s, settings
