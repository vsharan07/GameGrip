"""One calibration for the single board: hold still, turn right, tilt up.

    .venv/bin/python calibrate.py --port /dev/cu.usbserial-0001

Close the Serial Monitor and the play host first. The port can only be open
in one program.
"""

import argparse

from controls import ControlMap
from look_calibration import LookCalibrationError, apply_to_aim, run_grouped, save_document
from sensor.csv_stream import open_serial
from swing_calibration import load_serial_settings


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Calibrate the right turn and the up turn.")
    parser.add_argument("--port", required=True, help="Serial port, such as /dev/cu.usbserial-0001.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    baud, read_timeout_s, _settings = load_serial_settings()
    try:
        source = open_serial(args.port, baud, read_timeout_s)
    except Exception as exc:
        print("Could not open %s: %s" % (args.port, exc))
        return 1
    print("One calibration. Hold still, turn right, then tilt up.")
    try:
        try:
            document = run_grouped(source, print)
        except LookCalibrationError as exc:
            print(exc)
            print("Nothing was saved.")
            return 1
    finally:
        source.close()
    path = save_document(document)
    controls = ControlMap.load()
    apply_to_aim(document, controls.aim)
    controls.save()
    print("Saved %s." % path)
    print("Mouse look is saved. Do not run this file again unless you want to replace it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
