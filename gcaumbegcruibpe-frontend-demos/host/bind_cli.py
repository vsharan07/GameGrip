"""Confirm or override suggested bindings and save them on the profile.

    python3 host/bind_cli.py --user NAME --game minecraft

Enter accepts the suggestion shown on the line above the prompt.
Ctrl+C quits without saving.
"""

import argparse

from profiles import BindingError, load_game_profile, save_game_profile
from suggestions import StaticTableSuggestionProvider


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Bind GameGrip gestures and buttons for a game.")
    parser.add_argument("--user", required=True)
    parser.add_argument("--game", required=True)
    parser.add_argument(
        "--set",
        dest="action_set",
        default=None,
        help="Action set to bind. Default: combat and build.",
    )
    parser.add_argument(
        "--connected",
        default="btn0,btn1",
        help="Connected buttons, comma-separated. Default: btn0,btn1.",
    )
    return parser.parse_args(argv)


def run(user, game, action_set=None, connected="btn0,btn1", input_fn=None, output_fn=None, root=None):
    if input_fn is None:
        input_fn = input
    if output_fn is None:
        output_fn = print
    connected_buttons = [part.strip() for part in connected.split(",") if part.strip()]
    suggestions = StaticTableSuggestionProvider().suggest(game)
    profile = load_game_profile(user, game, root=root, connected_buttons=connected_buttons)
    set_names = (action_set,) if action_set else tuple(suggestions.action_sets)
    try:
        for set_name in set_names:
            output_fn("Action set: %s" % set_name)
            mapping = suggestions.for_set(set_name)
            for gesture, action in mapping["gestures"].items():
                _ask(
                    "gesture",
                    gesture,
                    action,
                    suggestions.default_keys.get(action, ""),
                    input_fn,
                    output_fn,
                    lambda choice, gesture=gesture, action=action: _apply_gesture(
                        profile, set_name, gesture, action, choice, suggestions
                    ),
                )
            for button, action in mapping["buttons"].items():
                if button not in profile.connected_buttons:
                    output_fn("Skipping %s because that button is not connected." % button)
                    continue
                _ask(
                    "button",
                    button,
                    action,
                    suggestions.default_keys.get(action, ""),
                    input_fn,
                    output_fn,
                    lambda choice, button=button, action=action: _apply_button(
                        profile, set_name, button, action, choice, suggestions
                    ),
                )
    except KeyboardInterrupt:
        output_fn("")
        return 1
    if set_names:
        profile.switch(set_names[-1])
    path = save_game_profile(profile, root=root)
    output_fn("Saved bindings for %s / %s to %s" % (user, game, path))
    return 0


def _ask(kind, source, action, key, input_fn, output_fn, apply_choice):
    output_fn("Suggested: %s %s -> %s (key %s)" % (kind, source, action, key))
    while True:
        typed = input_fn("Press Enter to confirm, or type an action to override: ")
        if typed is None:
            typed = ""
        choice = typed.strip()
        try:
            is_override = apply_choice(choice)
        except BindingError as exc:
            output_fn(str(exc))
            continue
        return choice or action, is_override


def _apply_gesture(profile, set_name, gesture, suggested, choice, suggestions):
    if choice == "":
        profile.confirm_gesture(set_name, gesture, suggested, suggestions)
        return False
    profile.override_gesture(set_name, gesture, choice, suggestions)
    return True


def _apply_button(profile, set_name, button, suggested, choice, suggestions):
    if choice == "":
        profile.confirm_button(set_name, button, suggested, suggestions)
        return False
    profile.override_button(set_name, button, choice, suggestions)
    return True


def main(argv=None):
    args = parse_args(argv)
    return run(args.user, args.game, args.action_set, args.connected)


if __name__ == "__main__":
    raise SystemExit(main())
