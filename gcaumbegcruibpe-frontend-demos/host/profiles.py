"""Action sets layered on a stored calibration profile.

Gesture templates stay in the profile file from host/store.py. Switching
the active action set only changes which action a gesture or button
triggers. It does not recalibrate.
"""

from store import load_profile, save_profile

DEFAULT_ACTION_SETS = ("combat", "build")
DEFAULT_CONNECTED_BUTTONS = ("btn0", "btn1")


class BindingError(Exception):
    """A gesture or button binding was not accepted."""


class ActionSet(object):
    def __init__(self, name):
        self.name = name
        self.gestures = {}
        self.buttons = {}
        self.confirmed = {}
        self.overrides = {}


class GameProfile(object):
    def __init__(self, user, game, connected_buttons=None, gesture_templates=None):
        self.user = user
        self.game = game
        if connected_buttons is None:
            connected_buttons = DEFAULT_CONNECTED_BUTTONS
        self.connected_buttons = set(connected_buttons)
        self.gesture_templates = gesture_templates if gesture_templates is not None else {}
        self.action_sets = {name: ActionSet(name) for name in DEFAULT_ACTION_SETS}
        self.active_set = DEFAULT_ACTION_SETS[0]

    def switch(self, name):
        """Select an action set. Templates are left as they are."""
        if name not in self.action_sets:
            raise BindingError("No action set named %s." % name)
        self.active_set = name

    def active_gestures(self):
        return dict(self.action_sets[self.active_set].gestures)

    def active_buttons(self):
        return dict(self.action_sets[self.active_set].buttons)

    def apply_suggestions(self, suggestions, set_name):
        """Fill one action set. A stored override is not replaced."""
        mapping = suggestions.for_set(set_name)
        action_set = self.action_sets[set_name]
        for gesture, action in mapping["gestures"].items():
            key = "gesture:" + gesture
            if key in action_set.overrides:
                action_set.gestures[gesture] = action_set.overrides[key]
            else:
                action_set.gestures[gesture] = action
        for button, action in mapping["buttons"].items():
            if button not in self.connected_buttons:
                continue
            key = "button:" + button
            if key in action_set.overrides:
                action_set.buttons[button] = action_set.overrides[key]
            else:
                action_set.buttons[button] = action

    def confirm_gesture(self, set_name, gesture, action, suggestions):
        self._bind_gesture(set_name, gesture, action, suggestions)
        action_set = self.action_sets[set_name]
        key = "gesture:" + gesture
        action_set.confirmed[key] = action
        action_set.overrides.pop(key, None)

    def override_gesture(self, set_name, gesture, action, suggestions):
        self._bind_gesture(set_name, gesture, action, suggestions)
        action_set = self.action_sets[set_name]
        key = "gesture:" + gesture
        action_set.overrides[key] = action
        action_set.confirmed[key] = action

    def confirm_button(self, set_name, button, action, suggestions):
        self._bind_button(set_name, button, action, suggestions)
        action_set = self.action_sets[set_name]
        key = "button:" + button
        action_set.confirmed[key] = action
        action_set.overrides.pop(key, None)

    def override_button(self, set_name, button, action, suggestions):
        self._bind_button(set_name, button, action, suggestions)
        action_set = self.action_sets[set_name]
        key = "button:" + button
        action_set.overrides[key] = action
        action_set.confirmed[key] = action

    def bind_button(self, set_name, button, action, suggestions):
        self._bind_button(set_name, button, action, suggestions)

    def _bind_gesture(self, set_name, gesture, action, suggestions):
        action_set = self._require_set(set_name)
        if action not in suggestions.gesture_safe:
            raise BindingError("%s is not safe to bind to a gesture." % action)
        action_set.gestures[gesture] = action

    def _bind_button(self, set_name, button, action, suggestions):
        action_set = self._require_set(set_name)
        if button not in self.connected_buttons:
            raise BindingError("Button %s is not connected." % button)
        if action not in suggestions.button_safe:
            raise BindingError("%s is not safe to bind to a button." % action)
        action_set.buttons[button] = action

    def _require_set(self, name):
        if name not in self.action_sets:
            raise BindingError("No action set named %s." % name)
        return self.action_sets[name]

    def to_dict(self):
        sets = {}
        for name, action_set in self.action_sets.items():
            sets[name] = {
                "gestures": dict(action_set.gestures),
                "buttons": dict(action_set.buttons),
                "confirmed": dict(action_set.confirmed),
                "overrides": dict(action_set.overrides),
            }
        return {
            "user": self.user,
            "game": self.game,
            "gestures": self.gesture_templates,
            "active_action_set": self.active_set,
            "connected_buttons": sorted(self.connected_buttons),
            "action_sets": sets,
        }


def load_game_profile(user, game, root=None, connected_buttons=None):
    data = load_profile(user, game, root)
    profile = GameProfile(
        user,
        game,
        connected_buttons=connected_buttons if connected_buttons is not None else data.get("connected_buttons"),
        gesture_templates=data.get("gestures") or {},
    )
    saved_sets = data.get("action_sets") or {}
    for name, raw in saved_sets.items():
        if name not in profile.action_sets:
            profile.action_sets[name] = ActionSet(name)
        action_set = profile.action_sets[name]
        action_set.gestures = dict(raw.get("gestures") or {})
        action_set.buttons = dict(raw.get("buttons") or {})
        action_set.confirmed = dict(raw.get("confirmed") or {})
        action_set.overrides = dict(raw.get("overrides") or {})
    active = data.get("active_action_set")
    if active in profile.action_sets:
        profile.active_set = active
    return profile


def save_game_profile(profile, root=None):
    return save_profile(profile.user, profile.game, profile.to_dict(), root)
