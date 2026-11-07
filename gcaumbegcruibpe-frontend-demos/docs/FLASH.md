# Previous two-board firmware

The active sketch is `firmware/gamegrip/gamegrip.ino`. It is one ESP32, one MPU6050, and a serial CSV stream. Flash that sketch using the steps in the repository README. This page is the previous two-board UDP firmware.

The sketch is `firmware/upper/upper.ino`. Arduino compiles only the files in that folder. Open that `.ino`; do not move it.

Board package: **esp32 by Espressif Systems** (this machine has core 3.3.12). In the Boards Manager, install that package if the ESP32 Dev Module board is not already listed.

## config.h

`config.h` is not in git. From the GameGrip folder:

```bash
cp firmware/upper/config.h.example firmware/upper/config.h
```

Edit `firmware/upper/config.h`:

| Define | What to put |
| --- | --- |
| `WIFI_SSID` | The Wi-Fi network name, in quotes |
| `WIFI_PASS` | That network's password, in quotes |
| `HOST_IP` | The IPv4 address of the computer running `host/main.py`, in quotes |
| `UDP_PORT` | `5005` |
| `MODULE_ID` | `0` for this upper-arm board |
| `FORCE_FAKE_IMU` | `0` to use the MPU6050 when it answers. `1` always sends the fake stream |
| `BATTERY_ENABLED` | `0` until the divider in `docs/WIRING.md` is connected. `1` reads GPIO 34 |

`HOST_IP` must be the computer's address on that same Wi-Fi network, such as `"192.168.1.42"`. `192.168.x.x` in the example is only a placeholder.

## Board settings

Tools menu for **ESP32 Dev Module**. These are the core 3.3.12 defaults:

| Tools menu | Setting |
| --- | --- |
| Board | ESP32 Dev Module |
| Upload Speed | 921600 |
| CPU Frequency | 240MHz (WiFi/BT) |
| Flash Frequency | 80MHz |
| Flash Mode | QIO |
| Flash Size | 4MB (32Mb) |
| Partition Scheme | Default 4MB with spiffs (1.2MB APP/1.5MB SPIFFS) |
| Core Debug Level | None |
| PSRAM | Disabled |
| Arduino Runs On | Core 1 |
| Events Run On | Core 1 |
| Erase All Flash Before Sketch Upload | Disabled |
| JTAG Adapter | Disabled |
| Zigbee Mode | Disabled |
| Port | The ESP32 serial port |

If upload fails at 921600, set Upload Speed to 115200 and try again. Leave the other rows as listed.

## Upload

1. Connect the board with a data USB cable.
2. Select its port in Tools → Port.
3. Click Upload.
4. Open the Serial Monitor at **115200** baud.

The onboard LED (GPIO 2 on the ESP32 Dev Module, active high) blinks while the board is associating and stays on once Wi-Fi is up. Each attempt waits 15 seconds, then starts over. The sequence counter is not cleared if the link drops.

The boot lines below are printed before Wi-Fi connects. The `sending` line starts only after the board has joined, then once a second. `seq` is the last packet handed to UDP. The first of those lines is `seq=0`, and it then climbs by about 50 a second.

Chip answering at `0x68`:

```text
I2C scan SDA=21 SCL=22
I2C device at 0x68
I2C scan done, 1 device(s)
MPU6050 ready at 0x68. WHO_AM_I=0x68.
sending seq=0 imu=real fusion=ok batt=off
```

Nothing on the bus:

```text
I2C scan SDA=21 SCL=22
no I2C devices found
MPU6050 not found at 0x68. WHO_AM_I check failed. Falling back to fake IMU. FUSION_OK=0.
sending seq=0 imu=fake fusion=fail batt=off
```

`FORCE_FAKE_IMU` set to `1` still scans, then prints this instead of the ready or not-found line:

```text
FORCE_FAKE_IMU is 1. Using fake IMU. FUSION_OK=0.
```

The following `sending` line is `imu=fake fusion=fail`. With `BATTERY_ENABLED` at `0` the battery field is the word `off`. With it at `1` that field is the cell voltage to two decimal places, such as `batt=3.72`. Below 3.4 V the packet sets `LOW_BATT`. The host status line gains `low_batt=1` while that bit is set.

On the computer, run `python3 host/main.py` and the upper column should leave `DOWN`.

## Test I2C

Do this on each board after it is wired as in `docs/WIRING.md`. This does not change the upload steps above.

1. Tie AD0 to GND and connect SDA to GPIO 21, SCL to GPIO 22, and GND to GND. Power the breakout as described in the wiring note.
2. In that board's `config.h`, set `FORCE_FAKE_IMU` to `0`. Leave `BATTERY_ENABLED` at `0` until the divider is soldered.
3. Upload the sketch for this board and open the Serial Monitor at **115200**.
4. A line `I2C device at 0x68`, then `MPU6050 ready at 0x68. WHO_AM_I=0x68.`, means the chip answered and passed WHO_AM_I. The once-a-second line should read `imu=real fusion=ok batt=off`.
5. An empty scan means 3V3 or VIN, GND, SDA, or SCL is open, or AD0 is not at GND. The sketch does not stop. It prints the not-found line and continues with `imu=fake fusion=fail`.
6. `I2C device at 0x69` means AD0 is high. Move AD0 to GND.
7. Set `FORCE_FAKE_IMU` back to `1` and upload again to confirm the fake path. The scan still runs, then the monitor prints `FORCE_FAKE_IMU is 1. Using fake IMU. FUSION_OK=0.` and `imu=fake`.
8. Only after the divider is connected, set `BATTERY_ENABLED` to `1` and upload again. `batt=` should show volts instead of `off`.

## Lower module

Use the same board settings, I2C test, and upload steps. Open `firmware/lower/lower.ino` instead.

```bash
cp firmware/lower/config.h.example firmware/lower/config.h
```

Set `MODULE_ID` to `1`. Use the same `WIFI_SSID`, `WIFI_PASS`, `HOST_IP`, and `UDP_PORT` as the upper board. `FORCE_FAKE_IMU` and `BATTERY_ENABLED` follow the same rules as the upper board. Wire GPIO 25 and GPIO 26 as described in `docs/WIRING.md`.

The boot text and the `sending` line match the upper board. This board adds one line when button 1 has been held for 1.5 s after the 25 ms debounce:

```text
orientation reset
```

That line is printed once per hold. Releasing the button arms it again. With a real IMU, later quaternions are relative to the pose at that press.

When this board is connected, the listener's lower column leaves `DOWN`. The sequence counter on this board is separate from the upper board.
