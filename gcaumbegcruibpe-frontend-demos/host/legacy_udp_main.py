"""Previous two-board UDP host. Not the active GameGrip application.

The running host is host/main.py, which reads one IMU over serial.
Start this file only when you still want the older upper/lower UDP pipeline:

    python3 legacy_udp_main.py

It listens, aligns the two streams, matches, checks safety, and injects.
Dry-run prints keys and sends nothing. Pass --live to send keys.
Esc three times within one second stops injection until this process starts again.
"""

import argparse

from alignment import Aligner
from config import load_config
from gg_packet import MODULE_LOWER, MODULE_UPPER
from injector import Injector
from matcher import GestureTemplate, Matcher
from profiles import load_game_profile
from receiver import DEFAULT_PORT, Receiver
from safety import Safety
from suggestions import StaticTableSuggestionProvider, SuggestionError

PRINT_INTERVAL_S = 1.0
# A calibrated gesture uses both arms. Buttons are not injected here.
GESTURE_MODULES = ("upper", "lower")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Listen for GameGrip packets, match gestures, and inject keys."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="UDP port to bind (default %(default)s)",
    )
    parser.add_argument("--user", default=None, help="Profile user name.")
    parser.add_argument("--game", default=None, help="Profile game name.")
    parser.add_argument(
        "--set",
        dest="action_set",
        default=None,
        help="Action set to use. Default: the set saved on the profile.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Send keys. Without this flag the host only prints the keys.",
    )
    return parser.parse_args(argv)


class GestureSegmenter(object):
    """One window per swing. Limits come from config, not from literals here."""

    def __init__(self, config):
        self._config = config
        self._active = []
        self._in_motion = False
        self._wait_for_still = False

    def push(self, frame):
        """Return a finished window, or None while the arm is still or mid-swing."""
        if frame.missing_module is not None:
            return None
        if frame.gyro_upper is None or frame.gyro_lower is None or frame.twist_deg is None:
            return None
        gyro_mag = max(frame.gyro_upper, frame.gyro_lower)
        sample = (
            frame.twist_deg,
            frame.gyro_upper,
            frame.gyro_lower,
            frame.accel_upper,
            frame.accel_lower,
        )
        moving = gyro_mag > self._config.still_gyro_threshold
        if self._wait_for_still:
            if not moving:
                self._wait_for_still = False
            return None
        if not self._in_motion:
            if moving:
                self._in_motion = True
                self._active = [sample]
            return None
        if moving:
            self._active.append(sample)
            if len(self._active) > self._config.max_gesture_samples:
                self._active = []
                self._in_motion = False
                self._wait_for_still = True
            return None
        samples = list(self._active)
        self._active = []
        self._in_motion = False
        if len(samples) < self._config.min_gesture_samples:
            return None
        return samples


def run(receiver, aligner, segmenter, matcher, keys, safety, injector):
    next_status = receiver.clock()
    while True:
        now = receiver.clock()
        if now >= next_status:
            print(receiver.status_line(), flush=True)
            next_status = now + PRINT_INTERVAL_S
        remaining = next_status - receiver.clock()
        receiver.poll(0.0 if remaining < 0.0 else remaining)
        _dispatch(receiver, aligner, segmenter, matcher, keys, safety, injector)


def _dispatch(receiver, aligner, segmenter, matcher, keys, safety, injector):
    upper_down = receiver.is_down(MODULE_UPPER)
    lower_down = receiver.is_down(MODULE_LOWER)
    for packet, t_host in receiver.take_packets():
        frame = aligner.push(packet, t_host)
        window = segmenter.push(frame)
        if window is None:
            continue
        name = matcher.match(
            window,
            frame.t_host_ms,
            upper_down=upper_down,
            lower_down=lower_down,
        )
        if name is None:
            continue
        key = keys.get(name)
        if key is None:
            continue
        if safety.injection_blocked(GESTURE_MODULES, upper_down, lower_down):
            continue
        injector.inject(
            key,
            matched=True,
            upper_down=upper_down,
            lower_down=lower_down,
            safety=safety,
            depends_on=GESTURE_MODULES,
        )


def _matcher_and_keys(user, game, action_set, config):
    if user is None and game is None:
        return Matcher([], config), {}
    if user is None or game is None:
        raise RuntimeError("Pass both --user and --game, or neither.")
    profile = load_game_profile(user, game)
    if action_set:
        profile.switch(action_set)
    templates = [
        GestureTemplate(name, spec["template"], spec["threshold"])
        for name, spec in profile.gesture_templates.items()
    ]
    suggestions = StaticTableSuggestionProvider().suggest(game)
    keys = {}
    for gesture, action in profile.active_gestures().items():
        key = suggestions.default_keys.get(action)
        if key is not None:
            keys[gesture] = key
    return Matcher(templates, config), keys


def main(argv=None):
    args = parse_args(argv)
    config = load_config()
    try:
        matcher, keys = _matcher_and_keys(args.user, args.game, args.action_set, config)
    except (RuntimeError, SuggestionError) as exc:
        print(exc)
        return 1
    receiver = Receiver(port=args.port)
    safety = Safety()
    injector = Injector(live=args.live)
    if args.live:
        print("Live mode. Keys will be sent. Press Esc three times within one second to stop.", flush=True)
    else:
        print("Dry-run. Keys are printed and not sent. Pass --live to send keys.", flush=True)
    try:
        listening = safety.listen()
    except Exception as exc:
        listening = False
        print("Emergency stop could not start (%s)." % exc)
    if not listening and args.live:
        print("Live mode will not send keys.")
        receiver.close()
        safety.stop()
        return 1
    try:
        run(
            receiver,
            Aligner(),
            GestureSegmenter(config),
            matcher,
            keys,
            safety,
            injector,
        )
    except KeyboardInterrupt:
        pass
    finally:
        safety.stop()
        injector.close()
        receiver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
