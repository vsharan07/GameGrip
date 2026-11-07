"""Relative twist between the two arm orientations."""

import math

import pytest

from features import relative_twist_deg

_IDENTITY = (1.0, 0.0, 0.0, 0.0)


def _z_rotation(degrees):
    """Unit quaternion for a z-axis rotation, matching scripts/fake_udp.py."""
    half = math.radians(degrees) * 0.5
    return (math.cos(half), 0.0, 0.0, math.sin(half))


def test_relative_twist_is_zero_for_identical_quaternions():
    assert relative_twist_deg(_IDENTITY, _IDENTITY) == pytest.approx(0.0, abs=1e-9)
    swing = _z_rotation(30.0)
    assert relative_twist_deg(swing, swing) == pytest.approx(0.0, abs=1e-9)


def test_relative_twist_matches_fake_udp_swing_amplitude():
    # fake_udp.py rotates about z by up to 30 degrees and back to identity.
    twist = relative_twist_deg(_IDENTITY, _z_rotation(30.0))
    assert twist == pytest.approx(30.0, abs=1e-6)
