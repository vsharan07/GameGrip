"""Load GameGrip tunables from host/config/default.json.

Units:
    udp_port: UDP port the host listens on
    stale_timeout_ms: silence before a module is DOWN
    alignment_window_ms: max host-clock gap for pairing upper and lower
    sample_rate_hz: packet rate from each module
    still_gyro_threshold: gyro_mag in rad/s at or below which motion is still
    dtw_window_samples: Sakoe-Chiba band radius, in samples
    match_distance_margin: multiply the largest pairwise calibration distance
        by this to get the match threshold
    min_gesture_samples: shortest accepted swing, in samples
    max_gesture_samples: longest accepted swing, in samples
    refractory_ms: after a gesture fires, ignore that gesture for this long
"""

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "default.json"

_INT_KEYS = (
    "udp_port",
    "stale_timeout_ms",
    "alignment_window_ms",
    "dtw_window_samples",
    "min_gesture_samples",
    "max_gesture_samples",
    "refractory_ms",
)
_FLOAT_KEYS = (
    "sample_rate_hz",
    "still_gyro_threshold",
    "match_distance_margin",
)
_REQUIRED_KEYS = _INT_KEYS + _FLOAT_KEYS


class ConfigError(ValueError):
    """default.json is missing or a required value is unusable."""


@dataclass(frozen=True)
class Config:
    udp_port: int
    stale_timeout_ms: int
    alignment_window_ms: int
    sample_rate_hz: float
    still_gyro_threshold: float
    dtw_window_samples: int
    match_distance_margin: float
    min_gesture_samples: int
    max_gesture_samples: int
    refractory_ms: int


def load_config(path=None):
    """Read a config file. Raises ConfigError naming any missing key."""
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not config_path.is_file():
        raise ConfigError("config file not found: %s" % config_path)
    with config_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ConfigError("config file must be a JSON object: %s" % config_path)
    missing = [key for key in _REQUIRED_KEYS if key not in data]
    if missing:
        raise ConfigError(
            "missing config keys in %s: %s" % (config_path, ", ".join(missing))
        )
    values = {}
    for key in _INT_KEYS:
        values[key] = _as_int(key, data[key])
    for key in _FLOAT_KEYS:
        values[key] = _as_float(key, data[key])
    return Config(**values)


def _as_number(key, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError("config key %s must be a number, got %r" % (key, value))
    return value


def _as_int(key, value):
    number = _as_number(key, value)
    if isinstance(number, float) and not number.is_integer():
        raise ConfigError("config key %s must be an integer, got %r" % (key, value))
    return int(number)


def _as_float(key, value):
    return float(_as_number(key, value))
