"""Single-IMU GameGrip host.

The ESP32 prints ax,ay,az,gx,gy,gz. This process records a named gesture
three times, matches later movements with DTW, and can turn a match into a
mouse click, a key, or relative mouse aim.

Dry-run is the default. Nothing is sent unless --live is passed or the
live command is confirmed. The older two-board UDP application is
host/legacy_udp_main.py. The rotation-threshold detector remains in
host/recognition/rotation.py and is not the classifier.
"""

import argparse
import time

from console import build_app, run_loop
from safety import Safety
from sensor.csv_stream import open_serial
from store import ProfileError
from suggestions import StaticTableSuggestionProvider, SuggestionError
from swing_calibration import gyro_of, load_serial_settings

SENSOR_DEPENDS = ("sensor",)
SWING_GESTURE = "swing"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Read one GameGrip IMU over serial, recognize calibrated gestures, and optionally inject input."
    )
    parser.add_argument("--port", required=True, help="Serial port, such as /dev/cu.usbserial-0001 or COM10.")
    parser.add_argument("--game", default=None, help="Game table name, such as minecraft or roblox.")
    parser.add_argument("--user", default=None, help="Saved profile user. Used with --game.")
    parser.add_argument("--set", dest="action_set", default="combat", help="Action set (default combat).")
    parser.add_argument("--live", action="store_true", help="Send keys and mouse input. The default only prints them.")
    return parser.parse_args(argv)


def resolve_key(game, action_set, user):
    """Key for the swing gesture, or None when no game is selected.

    Kept for the older injection tests and for --game without a controls file.
    """
    if not game:
        return None
    suggestions = StaticTableSuggestionProvider().suggest(game)
    mapping = suggestions.for_set(action_set)
    action = mapping["gestures"].get(SWING_GESTURE)
    if user:
        from profiles import load_game_profile

        try:
            profile = load_game_profile(user, game)
            if action_set:
                profile.switch(action_set)
            saved = profile.active_gestures().get(SWING_GESTURE)
            if saved:
                action = saved
        except (ProfileError, OSError):
            pass
    if action is None:
        return None
    return suggestions.default_keys.get(action)


def handle_sample(sample, now_s, quality, rate, tracker, injector, key, safety):
    """Update quality and the rotation detector. Inject only for a real swing on a live stream."""
    quality.observe(sample, now_s)
    if sample is None or tracker is None:
        return False
    if quality.is_down(now_s):
        return False
    rate.add(now_s)
    if not tracker.update(gyro_of(sample)):
        return False
    if key is None:
        return False
    return injector.inject(
        key,
        matched=True,
        upper_down=False,
        lower_down=False,
        safety=safety,
        depends_on=SENSOR_DEPENDS,
        sensor_down=False,
    )


def main(argv=None):
    args = parse_args(argv)
    baud, read_timeout_s, settings = load_serial_settings()
    safety = Safety()
    try:
        listening = safety.listen()
    except Exception as exc:
        listening = False
        print("Emergency stop could not start (%s)." % exc)
    if args.live and not listening:
        print("Live mode will not send input.")
        safety.stop()
        return 1

    try:
        source = open_serial(args.port, baud, read_timeout_s)
    except Exception as exc:
        print("Could not open %s: %s" % (args.port, exc))
        safety.stop()
        return 1

    app = build_app(safety, window_s=settings.swing_window_seconds)
    app.listening = listening
    if args.game:
        app.command("game %s %s" % (args.game, args.action_set))
        _apply_saved_profile(app, args.game, args.action_set, args.user)
    if args.live:
        app.command("live", confirmed=True)
    else:
        print("Dry-run. Gestures are printed and not sent. Pass --live, or type live, to send input.")

    time.sleep(2.0)

    def reopen():
        return open_serial(args.port, baud, read_timeout_s)

    try:
        source = run_loop(source, app, safety, settings.stale_timeout_s, reopen)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        app.session.release_inputs()
        try:
            source.close()
        except Exception:
            pass
        safety.stop()
    return 0


def _apply_saved_profile(app, game, action_set, user):
    """A saved profile key for swing overrides the table, when one exists."""
    if not user:
        return
    try:
        key = resolve_key(game, action_set, user)
    except SuggestionError as exc:
        print(exc)
        return
    if key:
        app.controls.bind(SWING_GESTURE, key)
        app.save()


if __name__ == "__main__":
    raise SystemExit(main())
