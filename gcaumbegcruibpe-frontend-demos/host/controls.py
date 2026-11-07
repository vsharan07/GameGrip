"""Which gesture or button produces which input.

Bindings are data. Selecting a game fills defaults from host/games; the
user can replace any of them without editing Python.
"""

import json
from pathlib import Path

from input.mouse_aim import AimSettings
from suggestions import StaticTableSuggestionProvider

FORMAT_VERSION = 1
DEFAULT_PATH = Path(__file__).resolve().parent / "data" / "controls.json"
MOUSE_BUTTONS = {"left_click": "left", "right_click": "right", "middle_click": "middle"}


class ControlMap(object):
    def __init__(self, game=None, action_set="combat", gestures=None, button_input=None, aim=None):
        self.game = game
        self.action_set = action_set
        self.gestures = dict(gestures or {})
        self.button_input = button_input
        self.aim = aim if isinstance(aim, AimSettings) else AimSettings.from_dict(aim)

    def binding(self, gesture_id):
        return self.gestures.get(gesture_id)

    def bind(self, gesture_id, input_name):
        self.gestures[gesture_id] = input_name

    def unbind(self, gesture_id):
        self.gestures.pop(gesture_id, None)

    def apply_game(self, game, action_set="combat"):
        """Copy suggested inputs for gesture ids the table already names."""
        suggestions = StaticTableSuggestionProvider().suggest(game)
        mapping = suggestions.for_set(action_set)
        self.game = game
        self.action_set = action_set
        for gesture_id, action in mapping["gestures"].items():
            key = suggestions.default_keys.get(action)
            if key is not None:
                self.gestures[gesture_id] = key
        button_action = mapping["buttons"].get("btn0")
        if button_action is not None:
            self.button_input = suggestions.default_keys.get(button_action)

    def to_dict(self):
        return {
            "version": FORMAT_VERSION,
            "game": self.game,
            "action_set": self.action_set,
            "gestures": self.gestures,
            "button_input": self.button_input,
            "aim": self.aim.to_dict(),
        }

    def save(self, path=None):
        target = Path(path) if path is not None else DEFAULT_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return target

    @classmethod
    def load(cls, path=None):
        target = Path(path) if path is not None else DEFAULT_PATH
        if not target.is_file():
            return cls()
        payload = json.loads(target.read_text(encoding="utf-8"))
        if payload.get("version") != FORMAT_VERSION:
            raise ValueError("Controls file version is not %s." % FORMAT_VERSION)
        return cls(
            game=payload.get("game"),
            action_set=payload.get("action_set") or "combat",
            gestures=payload.get("gestures") or {},
            button_input=payload.get("button_input"),
            aim=payload.get("aim"),
        )


def is_mouse_input(name):
    return name in MOUSE_BUTTONS


def mouse_button(name):
    return MOUSE_BUTTONS[name]
