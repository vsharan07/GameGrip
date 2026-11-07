"""Record three swings and store the template.

    python3 host/calibrate_cli.py --user NAME --game GAME --gesture GESTURE

Start scripts/fake_udp.py first, or swing the arm while both boards are connected.
Ctrl+C quits without saving.
"""

import argparse

from alignment import Aligner
from calibration import CalibrationError, Calibrator
from config import load_config
from gg_packet import MODULE_LOWER, MODULE_UPPER
from receiver import Receiver
from store import ProfileError, save_gesture


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Calibrate one GameGrip gesture.")
    parser.add_argument("--user", required=True)
    parser.add_argument("--game", required=True)
    parser.add_argument("--gesture", required=True)
    return parser.parse_args(argv)


def run(user, game, gesture):
    config = load_config()
    receiver = Receiver(port=config.udp_port)
    aligner = Aligner()
    calibrator = Calibrator(config)
    captured = 0
    print("Swing now", flush=True)
    try:
        while captured < 3:
            receiver.poll(0.01)
            if receiver.is_down(MODULE_UPPER) or receiver.is_down(MODULE_LOWER):
                continue
            frame = None
            for _packet, t_host_s in receiver.take_packets():
                frame = aligner.push(_packet, t_host_s)
            if frame is None:
                continue
            calibrator.push_frame(frame)
            if calibrator.rejection is not None:
                raise CalibrationError(calibrator.rejection)
            if len(calibrator.repetitions) > captured:
                captured = len(calibrator.repetitions)
                print("Captured repetition %d of 3" % captured, flush=True)
                if captured < 3:
                    print("Swing now", flush=True)
        template = calibrator.build()
    except KeyboardInterrupt:
        print()
        return 1
    finally:
        receiver.close()
    path = save_gesture(
        user,
        game,
        gesture,
        template.samples,
        template.threshold,
    )
    print(
        "Saved %s for %s / %s. Threshold is %g. (%s)"
        % (gesture, user, game, template.threshold, path),
        flush=True,
    )
    return 0


def main(argv=None):
    args = parse_args(argv)
    try:
        return run(args.user, args.game, args.gesture)
    except (CalibrationError, ProfileError) as exc:
        print(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
