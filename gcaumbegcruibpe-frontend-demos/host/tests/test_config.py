"""Config file loading."""

import json

import pytest

from config import ConfigError, load_config


def test_default_config_loads():
    config = load_config()
    assert config.udp_port > 0
    assert config.stale_timeout_ms > 0
    assert config.alignment_window_ms > 0
    assert config.sample_rate_hz > 0
    assert config.dtw_window_samples > 0


def test_missing_key_names_the_key(tmp_path):
    data = {
        "udp_port": 1,
        "stale_timeout_ms": 1,
        "alignment_window_ms": 1,
        "sample_rate_hz": 1,
        "still_gyro_threshold": 1,
        "dtw_window_samples": 1,
        "min_gesture_samples": 1,
        "max_gesture_samples": 1,
        "refractory_ms": 1,
    }
    path = tmp_path / "partial.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert "match_distance_margin" in str(caught.value)
