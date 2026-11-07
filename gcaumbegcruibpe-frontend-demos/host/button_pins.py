"""Show the raw button-test lines. Close the Serial Monitor first.

    .venv/bin/python button_pins.py
"""

import serial

PORT = "/dev/cu.usbserial-0001"


def main():
    port = serial.Serial(PORT, 115200, timeout=1)
    print("Reading %s. Press the button. Ctrl+C to stop." % PORT)
    try:
        while True:
            raw = port.readline()
            if not raw:
                continue
            text = raw.decode("utf-8", errors="ignore").strip()
            if text:
                print(text)
    except KeyboardInterrupt:
        print()
    finally:
        port.close()


if __name__ == "__main__":
    raise SystemExit(main())
