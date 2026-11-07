"""Calibration and DTW recognition. No serial port and no input injection."""

import math

from recognition.gestures import (
    GestureLibrary,
    RecognitionSettings,
    Recognizer,
    RecordingError,
    build_gesture,
    validate_recording,
)


def _settings():
    return RecognitionSettings()


def _pulse(axis, count=16, peak=180.0):
    rows = []
    for index in range(count):
        phase = index / float(count - 1)
        magnitude = peak * math.sin(math.pi * phase)
        gyro = [0.0, 0.0, 0.0]
        gyro[axis] = magnitude
        rows.append((0.0, 0.0, 1.0, gyro[0], gyro[1], gyro[2]))
    return rows


def _still(count=8):
    return [(0.0, 0.0, 1.0, 0.0, 0.0, 0.0) for _ in range(count)]


def _library_with(*gestures):
    library = GestureLibrary()
    for gesture in gestures:
        library.add(gesture)
    return library


def test_three_repetitions_save_and_load(tmp_path):
    settings = _settings()
    recordings = [_pulse(0, count) for count in (12, 20, 28)]
    gesture = build_gesture("Swing", recordings, hz=50, settings=settings, created="2026-11-04T00:00:00Z")
    assert gesture["id"] == "swing"
    assert gesture["repetition_lengths"] == [12, 20, 28]
    assert len(gesture["template"]) == settings.resample_length
    assert gesture["threshold"] >= settings.min_threshold
    library = _library_with(gesture)
    path = tmp_path / "gestures.json"
    library.save(path)
    loaded = GestureLibrary.load(path)
    assert loaded.get("swing")["name"] == "Swing"
    assert loaded.get("swing")["created"] == "2026-11-04T00:00:00Z"
    loaded.rename("swing", "Attack")
    assert loaded.get("swing")["name"] == "Attack"
    loaded.delete("swing")
    assert loaded.get("swing") is None


def test_recordings_are_rejected_for_clear_reasons():
    settings = _settings()
    try:
        validate_recording(_pulse(0, count=4), settings)
    except RecordingError as exc:
        assert "samples" in str(exc)
    else:
        raise AssertionError("a short recording was accepted")
    try:
        validate_recording(_still(20), settings)
    except RecordingError as exc:
        assert "still" in str(exc)
    else:
        raise AssertionError("a still recording was accepted")
    broken = _pulse(0)
    broken[3] = (float("nan"), 0.0, 1.0, 0.0, 0.0, 0.0)
    try:
        validate_recording(broken, settings)
    except RecordingError as exc:
        assert "invalid" in str(exc)
    else:
        raise AssertionError("an invalid sample was accepted")
    try:
        validate_recording(_pulse(0), settings, dropped=20)
    except RecordingError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("a recording with missing samples was accepted")


def test_inconsistent_repetitions_are_rejected():
    settings = _settings()
    try:
        build_gesture("Mixed", [_pulse(0), _pulse(0), _pulse(1)], hz=50, settings=settings)
    except RecordingError as exc:
        assert "same movement" in str(exc)
    else:
        raise AssertionError("different axes were stored as one gesture")


def test_calibrated_gesture_matches_and_other_motion_does_not():
    settings = _settings()
    gesture = build_gesture("Swing", [_pulse(0), _pulse(0, 24), _pulse(0, 18)], hz=50, settings=settings)
    recognizer = Recognizer(_library_with(gesture), settings)
    match = recognizer.consider(_pulse(0, 22), now_ms=1000)
    assert match.matched
    assert match.name == "swing"
    assert match.distance <= match.threshold
    other = recognizer.consider(_pulse(1), now_ms=2000)
    assert other.matched is False
    assert other.reason == "no match"
    noise = []
    for index in range(16):
        sign = 1.0 if index % 2 == 0 else -1.0
        noise.append((0.0, 0.0, 1.0, sign * 90.0, sign * -70.0, sign * 40.0))
    rejected = recognizer.consider(noise, now_ms=3000)
    assert rejected.matched is False


def test_similar_gestures_do_not_force_a_winner():
    settings = _settings()
    first = build_gesture("Swing", [_pulse(0)] * 3, hz=50, settings=settings)
    second = build_gesture("Attack", [_pulse(0)] * 3, hz=50, settings=settings)
    recognizer = Recognizer(_library_with(first, second), settings)
    decision = recognizer.consider(_pulse(0), now_ms=0)
    assert decision.matched is False
    assert decision.reason == "ambiguous"


def test_refractory_blocks_the_same_movement_until_it_expires():
    settings = _settings()
    gesture = build_gesture("Swing", [_pulse(0)] * 3, hz=50, settings=settings)
    recognizer = Recognizer(_library_with(gesture), settings)
    assert recognizer.consider(_pulse(0), now_ms=0).matched
    held = recognizer.consider(_pulse(0), now_ms=100)
    assert held.matched is False
    assert held.reason == "refractory"
    again = recognizer.consider(_pulse(0), now_ms=500)
    assert again.matched


def test_no_template_is_no_match():
    decision = Recognizer(GestureLibrary(), _settings()).consider(_pulse(0), now_ms=0)
    assert decision.matched is False
    assert decision.reason == "no template"


def test_segmenter_ignores_noise_and_returns_one_window():
    settings = _settings()
    segmenter = settings.segmenter(50)
    assert all(segmenter.push(sample) is None for sample in _still(30))
    window = None
    for sample in _pulse(0) + _still(20):
        finished = segmenter.push(sample)
        if finished is not None:
            window = finished
    assert window is not None
    assert len(window) >= settings.min_gesture_samples
