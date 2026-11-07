"""JSON profiles at profiles/<user>/<game>.json.

A save writes a temporary file in the same directory and renames it into
place. A corrupt file raises ProfileError and is left on disk, so other
gestures are not replaced by an empty profile.
"""

import json
import os
from pathlib import Path

PROFILES_ROOT = Path(__file__).resolve().parents[1] / "profiles"


class ProfileError(Exception):
    """The profile could not be read. Nothing was written."""


def load_profile(user, game, root=None):
    """Return the profile. A missing file is an empty profile, not an error."""
    path = profile_path(user, game, root)
    if not path.exists():
        return {"user": user, "game": game, "gestures": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProfileError(
            "The profile file is corrupted and was left unchanged: %s (%s)"
            % (path, exc.msg)
        ) from exc
    except OSError as exc:
        raise ProfileError("Could not read the profile %s: %s" % (path, exc)) from exc
    if not isinstance(data, dict) or not isinstance(data.get("gestures"), dict):
        raise ProfileError(
            "The profile file is corrupted and was left unchanged: %s" % path
        )
    return data


def save_profile(user, game, profile, root=None):
    """Atomically replace the profile. The previous file stays if the write fails."""
    path = profile_path(user, game, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(profile, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    return path


def save_gesture(user, game, gesture, samples, threshold, root=None):
    """Add one gesture. Other gestures in the file are kept."""
    _plain_name(user, "user")
    _plain_name(game, "game")
    _plain_name(gesture, "gesture")
    profile = load_profile(user, game, root)
    profile["user"] = user
    profile["game"] = game
    profile["gestures"][gesture] = {
        "template": [[float(value) for value in row] for row in samples],
        "threshold": float(threshold),
    }
    return save_profile(user, game, profile, root)


def profile_path(user, game, root=None):
    _plain_name(user, "user")
    _plain_name(game, "game")
    base = Path(root) if root is not None else PROFILES_ROOT
    return base / user / (game + ".json")


def _plain_name(value, label):
    text = str(value)
    if not text or text in (".", "..") or "/" in text or "\\" in text:
        raise ProfileError("%s must be a plain name, not %r" % (label, value))
