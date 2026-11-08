"""The live button-equipped board must work in the browser's motion detector."""

from types import SimpleNamespace

import pytest

import dtw_detector as detector


@pytest.mark.parametrize("suffix", ["", ",0", ",1"])
def test_detector_accepts_motion_with_or_without_button(suffix):
    line = "0.27,0.13,1.11,-6.04,2.01,-0.55" + suffix + "\r\n"
    port = SimpleNamespace(readline=lambda: line.encode())
    assert detector.read_sample(port) == [0.27, 0.13, 1.11, -6.04, 2.01, -0.55]


@pytest.mark.parametrize("line", [
    "status imu=real samples=50 read_failures=0", "", "1,2,3", "1,2,3,4,5,6,2",
    "1,2,3,4,5,6,0,1", "1,2,3,nan,5,6,0", "1,2,3,4,inf,6",
])
def test_detector_rejects_diagnostics_and_invalid_samples(line):
    assert detector.parse_sample_line(line) is None
