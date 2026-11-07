"""Three recordings become one DTW template.

The matcher in host/matcher.py supplies the Sakoe-Chiba distance. Templates
are resampled to one length first, so a longer recording is not rejected
only because it lasted more samples. A window that loses to every threshold
is no match.
"""

import json
import time
from pathlib import Path

from matcher import dtw_distance
from recognition.segment import MovementSegmenter
from sensor.features import FEATURE_NAMES, feature_vector, gyro_mag, resample

FORMAT_VERSION = 1
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "gestures.json"


class RecordingError(Exception):
    """One calibration take cannot be used."""


class GestureLibraryError(Exception):
    """The gesture file is missing a field or is the wrong version."""


def active_motion(samples, settings):
    """Drop the still samples before and after the movement.

    The segmenter stores the same part of a gesture: from the onset until
    the toy is still again. Comparing that part, rather than the idle time
    around it, is what lets a short take match a long one.
    """
    if not samples:
        return []
    start = 0
    while start < len(samples) and gyro_mag(samples[start]) < settings.onset_dps:
        start += 1
    end = len(samples)
    while end > start and gyro_mag(samples[end - 1]) < settings.still_dps:
        end -= 1
    return list(samples[start:end])


def validate_recording(samples, settings, dropped=0):
    """Reject a take that is empty, corrupt, or basically still."""
    if not samples:
        raise RecordingError("No samples were recorded.")
    total = len(samples) + int(dropped)
    if total > 0 and float(dropped) / float(total) > settings.max_missing_fraction:
        raise RecordingError(
            "Too many missing samples (%d of %d). Check the serial cable."
            % (dropped, total)
        )
    if len(samples) < settings.min_gesture_samples:
        raise RecordingError(
            "Only %d samples. Make one full movement, then hold still."
            % len(samples)
        )
    peak = 0.0
    for sample in samples:
        if len(sample) != 6:
            raise RecordingError("A sample did not have ax, ay, az, gx, gy, gz.")
        for value in sample:
            if value != value or value in (float("inf"), float("-inf")):
                raise RecordingError("A sample contained an invalid number.")
        peak = max(peak, gyro_mag(sample))
    if peak < settings.onset_dps:
        raise RecordingError(
            "That take was mostly still (peak %.0f deg/s, need %.0f)."
            % (peak, settings.onset_dps)
        )
    return peak


def build_gesture(name, recordings, hz, settings, created=None):
    """Return a stored gesture dict. recordings are three raw sample lists."""
    gesture_id = slug(name)
    if not gesture_id:
        raise RecordingError("The gesture needs a name.")
    if len(recordings) != 3:
        raise RecordingError("Three recordings are required, got %d." % len(recordings))
    prepared = []
    lengths = []
    for recording in recordings:
        trimmed = active_motion(recording, settings)
        validate_recording(trimmed, settings)
        rows = [feature_vector(sample) for sample in trimmed]
        prepared.append(resample(rows, settings.resample_length))
        lengths.append(len(recording))
    pairs = {}
    for left, right in ((0, 1), (0, 2), (1, 2)):
        pairs[(left, right)] = dtw_distance(prepared[left], prepared[right], settings.dtw_band)
    distances = list(pairs.values())
    if any(distance == float("inf") for distance in distances):
        raise RecordingError(
            "The three takes could not be aligned. Repeat the same movement."
        )
    smallest = min(distances)
    largest = max(distances)
    if largest > settings.min_threshold and largest > smallest * settings.consistency_ratio:
        raise RecordingError(
            "The three takes do not look like the same movement "
            "(distances %.0f to %.0f). Repeat them more alike."
            % (smallest, largest)
        )
    totals = []
    for index in range(3):
        total = 0.0
        for other in range(3):
            if other == index:
                continue
            key = (index, other) if index < other else (other, index)
            total += pairs[key]
        totals.append(total)
    medoid = min(range(3), key=lambda index: (totals[index], index))
    threshold = largest * settings.match_margin
    if threshold < settings.min_threshold:
        threshold = settings.min_threshold
    return {
        "id": gesture_id,
        "name": name.strip(),
        "version": FORMAT_VERSION,
        "feature_names": list(FEATURE_NAMES),
        "template": [list(row) for row in prepared[medoid]],
        "threshold": float(threshold),
        "pair_distances": [float(distance) for distance in distances],
        "repetition_lengths": lengths,
        "sample_rate_hz": float(hz),
        "resample_length": settings.resample_length,
        "dtw_band": settings.dtw_band,
        "created": created if created is not None else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


class GestureLibrary(object):
    def __init__(self, gestures=None):
        self.gestures = {}
        for gesture in gestures or []:
            self.gestures[gesture["id"]] = gesture

    def add(self, gesture):
        self.gestures[gesture["id"]] = gesture

    def get(self, gesture_id):
        return self.gestures.get(gesture_id)

    def rename(self, gesture_id, name):
        gesture = self.gestures.get(gesture_id)
        if gesture is None:
            raise GestureLibraryError("No gesture named %s." % gesture_id)
        gesture["name"] = name.strip()
        return gesture

    def delete(self, gesture_id):
        if gesture_id not in self.gestures:
            raise GestureLibraryError("No gesture named %s." % gesture_id)
        del self.gestures[gesture_id]

    def ids(self):
        return sorted(self.gestures)

    def save(self, path=None):
        target = Path(path) if path is not None else DEFAULT_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": FORMAT_VERSION, "gestures": list(self.gestures.values())}
        target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return target

    @classmethod
    def load(cls, path=None):
        target = Path(path) if path is not None else DEFAULT_PATH
        if not target.is_file():
            return cls()
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise GestureLibraryError("Gesture file is not valid JSON.") from exc
        if payload.get("version") != FORMAT_VERSION:
            raise GestureLibraryError("Gesture file version is not %s." % FORMAT_VERSION)
        return cls(payload.get("gestures") or [])


class Decision(object):
    def __init__(self, name, distance, threshold, runner_up, runner_up_distance, reason):
        self.name = name
        self.distance = distance
        self.threshold = threshold
        self.runner_up = runner_up
        self.runner_up_distance = runner_up_distance
        self.reason = reason

    @property
    def matched(self):
        return self.name is not None


class Recognizer(object):
    def __init__(self, library, settings):
        self.library = library
        self.settings = settings
        self._last_fire_ms = {}

    def consider(self, samples, now_ms):
        """Classify one finished window. None of the fields are a quaternion."""
        if not samples or not self.library.gestures:
            return Decision(None, None, None, None, None, "no template")
        trimmed = active_motion(samples, self.settings)
        if len(trimmed) < self.settings.min_gesture_samples:
            return Decision(None, None, None, None, None, "too short")
        rows = resample(
            [feature_vector(sample) for sample in trimmed],
            self.settings.resample_length,
        )
        ranked = []
        for gesture in self.library.gestures.values():
            distance = dtw_distance(rows, gesture["template"], self.settings.dtw_band)
            ranked.append((distance, gesture))
        ranked.sort(key=lambda item: (item[0], item[1]["id"]))
        distance, best = ranked[0]
        runner_up = None
        runner_distance = None
        if len(ranked) > 1:
            runner_distance, runner = ranked[1]
            runner_up = runner["id"]
        if distance > best["threshold"]:
            return Decision(None, distance, best["threshold"], runner_up, runner_distance, "no match")
        if (
            runner_up is not None
            and runner_distance <= ranked[1][1]["threshold"]
            and runner_distance <= distance * self.settings.ambiguity_ratio
        ):
            return Decision(
                None, distance, best["threshold"], runner_up, runner_distance, "ambiguous"
            )
        previous = self._last_fire_ms.get(best["id"])
        if previous is not None and (now_ms - previous) < self.settings.refractory_ms:
            return Decision(
                None, distance, best["threshold"], runner_up, runner_distance, "refractory"
            )
        self._last_fire_ms[best["id"]] = now_ms
        return Decision(best["id"], distance, best["threshold"], runner_up, runner_distance, "match")


def slug(name):
    cleaned = []
    for character in name.strip().lower():
        if character.isalnum():
            cleaned.append(character)
        elif character in (" ", "-", "_"):
            cleaned.append("_")
    text = "".join(cleaned).strip("_")
    while "__" in text:
        text = text.replace("__", "_")
    return text


class RecognitionSettings(object):
    def __init__(self, **values):
        defaults = dict(
            onset_dps=40.0,
            still_dps=20.0,
            min_gesture_samples=8,
            max_gesture_samples=150,
            end_still_s=0.25,
            resample_length=32,
            dtw_band=8,
            match_margin=1.5,
            min_threshold=20.0,
            consistency_ratio=4.0,
            ambiguity_ratio=1.35,
            refractory_ms=400.0,
            max_missing_fraction=0.4,
            assumed_hz=50.0,
        )
        defaults.update(values)
        for key, value in defaults.items():
            setattr(self, key, value)

    def end_still_samples(self, hz):
        return max(int(float(self.end_still_s) * float(hz)), 1)

    def segmenter(self, hz):
        return MovementSegmenter(
            self.onset_dps,
            self.still_dps,
            self.min_gesture_samples,
            self.max_gesture_samples,
            self.end_still_samples(hz),
        )


def load_recognition_settings(path=None):
    if path is None:
        path = Path(__file__).resolve().parent.parent / "config" / "serial.json"
    raw = {}
    target = Path(path)
    if target.is_file():
        raw = json.loads(target.read_text(encoding="utf-8"))
    keys = (
        "onset_dps",
        "still_dps",
        "min_gesture_samples",
        "max_gesture_samples",
        "end_still_s",
        "resample_length",
        "dtw_band",
        "match_margin",
        "min_threshold",
        "consistency_ratio",
        "ambiguity_ratio",
        "refractory_ms",
        "max_missing_fraction",
        "assumed_hz",
    )
    values = {key: raw[key] for key in keys if key in raw}
    return RecognitionSettings(**values)

