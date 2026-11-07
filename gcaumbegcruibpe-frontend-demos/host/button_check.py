"""Print GPIO 26 from the board. Close the Serial Monitor first.

    .venv/bin/python button_check.py
"""

import serial

PORT = "/dev/cu.usbserial-0001"


def main():
    port = serial.Serial(PORT, 115200, timeout=1)
    print("Watching %s. Press the button. Ctrl+C to stop." % PORT)
    print("DOWN means the button wire is touching GND. A press to 3.3V stays up.")
    last = None
    plain = 0
    try:
        while True:
            raw = port.readline()
            if not raw:
                continue
            parts = raw.decode("utf-8", errors="ignore").strip().split(",")
            if len(parts) == 7 and parts[-1] in ("0", "1"):
                pressed = parts[-1] == "1"
                if pressed != last:
                    print("GPIO 26 %s" % ("DOWN" if pressed else "up"))
                    last = pressed
            elif len(parts) == 6:
                plain += 1
                if plain == 20:
                    print("No button column. GPIO 26 is not in the sketch that is running.")
                    return 1
    except KeyboardInterrupt:
        print()
    finally:
        port.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
