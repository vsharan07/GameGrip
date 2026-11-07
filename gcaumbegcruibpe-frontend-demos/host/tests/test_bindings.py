"""Game suggestions and action-set bindings."""

import json
from pathlib import Path

import pytest

from bind_cli import run as bind_run
from profiles import BindingError, GameProfile, load_game_profile, save_game_profile
from store import save_gesture
from suggestions import StaticTableSuggestionProvider

GAMES_DIR = Path(__file__).resolve().parents[1] / "games"


def _action_names(game):
    data = json.loads((GAMES_DIR / (game + ".json")).read_text(encoding="utf-8"))
    return {entry["name"] for entry in data["actions"]}


def test_every_suggested_action_exists_in_the_game_file():
    provider = StaticTableSuggestionProvider()
    for game in ("minecraft", "roblox"):
        suggestions = provider.suggest(game)
        known = _action_names(game)
        assert suggestions.actions
        for name in suggestions.actions:
            assert name in known
        for name in suggestions.default_keys:
            assert name in known
        for action in suggestions.suggested_actions():
            assert action in known


def test_switching_profiles_changes_the_active_bindings(tmp_path):
    save_gesture("ada", "minecraft", "swing", [[0.0], [1.0], [0.0]], 1.25, root=tmp_path)
    suggestions = StaticTableSuggestionProvider().suggest("minecraft")
    profile = load_game_profile("ada", "minecraft", root=tmp_path)
    profile.confirm_gesture("combat", "swing", "attack", suggestions)
    profile.confirm_gesture("build", "swing", "place", suggestions)
    templates_before = dict(profile.gesture_templates)

    profile.switch("combat")
    assert profile.active_gestures()["swing"] == "attack"
    profile.switch("build")
    assert profile.active_gestures()["swing"] == "place"
    assert profile.gesture_templates == templates_before

    save_game_profile(profile, root=tmp_path)
    loaded = load_game_profile("ada", "minecraft", root=tmp_path)
    assert loaded.active_set == "build"
    assert loaded.active_gestures()["swing"] == "place"
    assert loaded.gesture_templates["swing"]["threshold"] == 1.25


def test_override_is_kept_when_suggestions_are_regenerated():
    provider = StaticTableSuggestionProvider()
    suggestions = provider.suggest("minecraft")
    profile = GameProfile("ada", "minecraft")
    profile.apply_suggestions(suggestions, "combat")
    assert profile.action_sets["combat"].gestures["swing"] == "attack"

    profile.override_gesture("combat", "swing", "place", suggestions)
    regenerated = provider.suggest("minecraft")
    profile.apply_suggestions(regenerated, "combat")
    assert profile.action_sets["combat"].gestures["swing"] == "place"
    assert regenerated.gesture_to_action["swing"] == "attack"


def test_enter_confirms_the_suggestion_that_was_just_shown(tmp_path):
    events = []

    def output_fn(message):
        events.append(("out", message))

    def input_fn(prompt):
        events.append(("in", prompt))
        return ""

    code = bind_run("ada", "minecraft", input_fn=input_fn, output_fn=output_fn, root=tmp_path)
    assert code == 0
    suggestions_seen = 0
    for index, (kind, text) in enumerate(events):
        if kind != "in":
            continue
        previous = events[index - 1]
        assert previous[0] == "out"
        assert previous[1].startswith("Suggested:")
        suggestions_seen += 1
    assert suggestions_seen > 0
    loaded = load_game_profile("ada", "minecraft", root=tmp_path)
    loaded.switch("combat")
    assert loaded.active_gestures()["swing"] == "attack"
    loaded.switch("build")
    assert loaded.active_gestures()["swing"] == "place"


def test_binding_to_a_disconnected_button_is_rejected():
    suggestions = StaticTableSuggestionProvider().suggest("minecraft")
    profile = GameProfile("ada", "minecraft", connected_buttons=["btn0"])
    with pytest.raises(BindingError) as caught:
        profile.bind_button("combat", "btn1", "jump", suggestions)
    assert "not connected" in str(caught.value).lower()
    assert "btn1" not in profile.action_sets["combat"].buttons
