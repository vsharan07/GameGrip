"""Suggest which actions a gesture or button should trigger.

SuggestionProvider is the interface. StaticTableSuggestionProvider reads
host/games/<game>.json. A language model is not implemented.
"""

import json
from pathlib import Path

GAMES_DIR = Path(__file__).resolve().parent / "games"


class SuggestionError(Exception):
    """The game table could not be turned into suggestions."""


class Suggestions(object):
    def __init__(self, game, actions, default_keys, gesture_to_action, button_to_action, action_sets, gesture_safe, button_safe):
        self.game = game
        self.actions = actions
        self.default_keys = default_keys
        self.gesture_to_action = gesture_to_action
        self.button_to_action = button_to_action
        self.action_sets = action_sets
        self.gesture_safe = gesture_safe
        self.button_safe = button_safe

    def for_set(self, name):
        """Gesture and button mappings for one named action set."""
        if name in self.action_sets:
            return self.action_sets[name]
        return {
            "gestures": self.gesture_to_action,
            "buttons": self.button_to_action,
        }

    def suggested_actions(self):
        names = []
        names.extend(self.gesture_to_action.values())
        names.extend(self.button_to_action.values())
        for mapping in self.action_sets.values():
            names.extend(mapping["gestures"].values())
            names.extend(mapping["buttons"].values())
        return names


class SuggestionProvider(object):
    """Interface: suggest(game) -> Suggestions.

    A language model is not implemented. Subclasses that call a model should
    override suggest() later. Do not add that call here.
    """

    def suggest(self, game):
        raise NotImplementedError("A language model is not implemented.")


class StaticTableSuggestionProvider(SuggestionProvider):
    """Suggestions from the JSON tables in host/games/."""

    def __init__(self, games_dir=None):
        self._games_dir = Path(games_dir) if games_dir is not None else GAMES_DIR

    def suggest(self, game):
        path = self._games_dir / (game + ".json")
        if not path.is_file():
            raise SuggestionError("No game table for %s." % game)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SuggestionError("Game table %s is not valid JSON." % path) from exc
        return _suggestions_from_table(data)


def _suggestions_from_table(data):
    actions = []
    default_keys = {}
    gesture_safe = set()
    button_safe = set()
    for entry in data.get("actions", []):
        name = entry["name"]
        actions.append(name)
        default_keys[name] = entry["key"]
        if entry.get("gesture_safe"):
            gesture_safe.add(name)
        if entry.get("button_safe"):
            button_safe.add(name)
    action_sets = {}
    for set_name, mapping in data.get("action_sets", {}).items():
        action_sets[set_name] = {
            "gestures": dict(mapping.get("gestures", {})),
            "buttons": dict(mapping.get("buttons", {})),
        }
    primary = action_sets.get("combat") or next(iter(action_sets.values()), {"gestures": {}, "buttons": {}})
    return Suggestions(
        game=data.get("game"),
        actions=actions,
        default_keys=default_keys,
        gesture_to_action=dict(primary["gestures"]),
        button_to_action=dict(primary["buttons"]),
        action_sets=action_sets,
        gesture_safe=gesture_safe,
        button_safe=button_safe,
    )
