"""Profile files survive a bad write and report a corrupt file clearly."""

import pytest

from store import ProfileError, load_profile, save_gesture


def test_save_keeps_other_gestures(tmp_path):
    save_gesture("ada", "minecraft", "attack", [[0.0], [1.0]], 1.5, root=tmp_path)
    save_gesture("ada", "minecraft", "block", [[0.2], [0.4]], 2.0, root=tmp_path)
    profile = load_profile("ada", "minecraft", root=tmp_path)
    assert set(profile["gestures"]) == {"attack", "block"}
    assert profile["gestures"]["attack"]["threshold"] == 1.5


def test_corrupted_profile_gives_a_clear_error(tmp_path):
    path = tmp_path / "ada" / "minecraft.json"
    path.parent.mkdir()
    original = "{not json"
    path.write_text(original, encoding="utf-8")
    with pytest.raises(ProfileError) as caught:
        load_profile("ada", "minecraft", root=tmp_path)
    message = str(caught.value)
    assert "corrupted" in message
    assert "unchanged" in message
    assert path.read_text(encoding="utf-8") == original
    with pytest.raises(ProfileError):
        save_gesture("ada", "minecraft", "attack", [[1.0]], 1.0, root=tmp_path)
    assert path.read_text(encoding="utf-8") == original
