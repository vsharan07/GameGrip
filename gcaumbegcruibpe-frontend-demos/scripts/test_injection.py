"""Type a known string through the injector.

Wait 3 seconds so you can click a text field. Dry-run prints each key and
sends nothing. Pass --live to type the string.

    python3 scripts/test_injection.py
    python3 scripts/test_injection.py --live
"""

import argparse
import importlib.util
import os
import sys
import time
from pathlib import Path

HOST_DIR = Path(__file__).resolve().parents[1] / "host"
VENV_PYTHON = HOST_DIR / ".venv" / "bin" / "python3"
if str(HOST_DIR) not in sys.path:
    sys.path.insert(0, str(HOST_DIR))

from injector import Injector  # noqa: E402
from safety import Safety  # noqa: E402

TEST_STRING = "GameGrip"
WAIT_S = 3


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Type %s after a short wait." % TEST_STRING)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Send the keys. Without this flag the keys are only printed.",
    )
    return parser.parse_args(argv)


def _use_venv_when_pynput_is_missing(live):
    """Live mode needs pynput. Conda's python3 does not see the project venv.

    The venv interpreter is a symlink to Anaconda. Running that symlink path
    uses the venv's site-packages. Running Anaconda's own path does not.
    """
    if not live or importlib.util.find_spec("pynput") is not None:
        return
    venv_root = VENV_PYTHON.parent.parent
    if Path(sys.prefix).resolve() == venv_root.resolve() or not VENV_PYTHON.is_file():
        raise SystemExit(
            "pynput is not installed for %s.\nRun: %s -m pip install pynput"
            % (sys.executable, sys.executable)
        )
    print("Using %s, which has pynput." % VENV_PYTHON, flush=True)
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *sys.argv])


def main(argv=None):
    args = parse_args(argv)
    _use_venv_when_pynput_is_missing(args.live)
    safety = Safety()
    injector = Injector(live=args.live)
    if args.live:
        try:
            listening = safety.listen()
        except Exception as exc:
            print("Emergency stop could not start (%s)." % exc, flush=True)
            listening = False
        if not listening:
            print("Live mode will not send keys.", flush=True)
            safety.stop()
            return 1
    print("Click a text field. Typing %r in %d seconds." % (TEST_STRING, WAIT_S), flush=True)
    time.sleep(WAIT_S)
    try:
        for character in TEST_STRING:
            injector.inject(
                character,
                matched=True,
                upper_down=False,
                lower_down=False,
                safety=safety,
                depends_on=(),
            )
    finally:
        safety.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
