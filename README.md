# GameGrip

GameGrip is a clip-on motion controller: one ESP32, one MPU6050, and a Python host. The website learns mouse-look from a still recording, a right turn, and a downward tilt. The terminal also supports recording gesture templates three times. Desktop input starts only after **LAUNCH MINECRAFT**, `--live`, or the terminal's `live` confirmation.

The active path is serial. The older two-board UDP design is still in the tree (`firmware/upper`, `firmware/lower`, `host/legacy_udp_main.py`) and is not what `python3 main.py` runs.

## Project layout

The project lives directly at the root of the [GameGrip repository](https://github.com/vsharan07/GameGrip).

| Folder | Contents |
| --- | --- |
| [frontend/](frontend/) | Calibration website and the Python bridge server |
| [frontend/gamegrip/](frontend/gamegrip/) | GameGrip calibration page |
| [frontend/Demos/](frontend/Demos/) | Baseball and boxing demos, including the offline HTML build |
| [host/](host/) | Single-sensor controller, calibration, bindings, and tests |
| [firmware/gamegrip/](firmware/gamegrip/) | Active ESP32 sensor sketch |
| [docs/](docs/) | Hardware, wiring, flashing, and packet documentation |
| [scripts/](scripts/) | Input checks and the legacy UDP simulator |

## Run the GameGrip website and demos

For Minecraft, close the separate `host/main.py` controller first: only one app
can use the ESP32's serial port. Start the server below, open Minecraft and a
world, then choose **MINECRAFT** on the website:

1. **HOLD STILL:** keep the sword steady for the baseline.
2. **MOVE RIGHT:** turn it once to your right, then hold. Re-centre before the next recording.
3. **MOVE DOWN:** tilt it down once, then hold.
4. Press **LAUNCH MINECRAFT** and switch to your Minecraft world during the
   five-second countdown. This enables the controller; it does not open the game executable.

These recordings learn the gyro axes, their signs and the resting bias, including
a sideways mounting. They save to `host/data/look.json` and
`host/data/controls.json`. A new calibration resets the extra manual rotation to
`0` so a previous `90`-degree correction is not applied twice. Failed recordings
do not overwrite the previous calibration. Use **RUN IT BACK** to stop and retry.

Press **Esc three times within one second** to stop. Closing or reloading the
Minecraft setup page, disconnecting the sensor, a one-second stale stream, or
shutting down the server releases held inputs. After a stop, recalibrate before
launching again. Calibration itself sends no desktop input. Button controls use
the existing host implementation and require a working button signal.

On Windows, open PowerShell and run:

```powershell
git clone https://github.com/vsharan07/GameGrip.git
cd GameGrip
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe frontend\gamegrip_server.py --port COM3
```

The calibration site opens at **http://127.0.0.1:8765/**. The sports demos are at
**http://127.0.0.1:8765/demos/**. Pick **DEMOS**, calibrate, and use **LAUNCH DEMOS**
to play with the sensor. Replace `COM3` with your ESP32's serial port.
If `--port` is omitted, the server uses `PORT` in
[frontend/dtw_detector.py](frontend/dtw_detector.py), currently `COM10`.
Close Arduino Serial Monitor so the server can open the port. Both six-column
motion streams and seven-column streams with the button state are accepted.

On macOS or Linux, use `python3 -m venv .venv`, `.venv/bin/python -m pip install -r requirements.txt`,
and `.venv/bin/python frontend/gamegrip_server.py` after cloning.

To open the sports demo without the server, open [frontend/Demos/index.html](frontend/Demos/index.html)
locally and use its keyboard controls. The existing calibration page, demo visuals,
music, and assets are included in their original folders.

## Run the controller on Windows

After installing the dependencies above, run from the repository root:

```powershell
.\.venv\Scripts\python.exe host\main.py --port COM3 --game minecraft
```

Replace `COM3` with your board's port. This starts in dry-run. Add `--live` to send
game input. The calibration and control commands are described below.

To run all host tests from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Hardware

| Part | Role |
| --- | --- |
| ESP32 dev board | Reads the IMU and prints CSV on USB serial |
| MPU6050 | One accelerometer and gyroscope |
| USB data cable | Power and the serial link |

The button is GPIO 26, the other leg to GND. `GAMEGRIP_BUTTON_PIN` is `26`. The sketch uses `INPUT_PULLUP`, treats LOW as pressed, debounces for 25 ms, and adds a 7th CSV column: `0` released, `1` pressed.

The onboard LED (GPIO 2) stays on while the MPU6050 is streaming and stays off when it is missing.

## Wiring

SDA is GPIO 21. SCL is GPIO 22. Tie AD0 to GND so the address is `0x68`. Leave INT unconnected. Share GND with the ESP32.

The MPU6050 runs at 3.3 V. On a GY-521 breakout, the pin marked VCC feeds the onboard regulator: connect that pin to the ESP32 VIN (5 V while the board is on USB) and connect the breakout GND to the ESP32 GND. SDA and SCL go to GPIO 21 and GPIO 22. Do not connect SDA or SCL to 5 V.

A board with no regulator takes VCC from the ESP32 3V3 pin instead.

## What the sensor actually sends

The sketch does not estimate orientation. Each sample is one line:

```text
ax,ay,az,gx,gy,gz
```

`ax,ay,az` are acceleration in **g**. `gx,gy,gz` are angular velocity in **deg/s**. They are not a quaternion, and the host does not integrate them into position. The gyro range is ±2000 deg/s (16.4 LSB per deg/s) and the accelerometer range is ±8 g (4096 LSB per g). The sketch reads the latest sample about every 20 ms. The button column is `,0` or `,1`.

Boot text is not CSV. An empty bus prints:

```text
I2C scan SDA=21 SCL=22
no I2C devices found
MPU6050 did not answer at 0x68. Not streaming sensor data.
status imu=missing
```

Every address that ACKs is printed, for example `I2C device at 0x68`. A chip that passes WHO_AM_I then prints `MPU6050 ready at 0x68. gyro +-2000 dps, accel +-8 g.` and starts the CSV lines. A missing chip does not print fake zeros.

Once a second the sketch also prints `status imu=real samples=<n> read_failures=<n>` or `status imu=missing`. Those lines have no six-value CSV, so the host ignores them.

## Flash

Library: the ESP32 **Wire** library that ships with the board package. Do not install an MPU6050 or fusion library for this sketch.

1. Install the **esp32 by Espressif Systems** board package. This machine has core 3.3.12.
2. Open `firmware/gamegrip/gamegrip.ino`. Leave it in that folder.
3. Board: **ESP32 Dev Module**. Upload speed 921600 (115200 if the upload fails). CPU 240 MHz. Flash 4 MB, QIO, 80 MHz. Partition scheme: Default 4MB with spiffs. Port: the board's serial port.
4. Upload. Open the Serial Monitor at **115200**.

`firmware/gamegrip/config.h` holds the pins, the I2C address, and the baud rate. It has no Wi-Fi settings.

## Python

From `host/`:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Requirements are pytest, pynput, and pyserial. The host's own DTW is in `host/matcher.py`. It does not need NumPy.

Find the port. On macOS it is usually `/dev/cu.usbserial-…` or `/dev/cu.SLAB_USBtoUART`. On Windows it looks like `COM10`.

```bash
cd host
.venv/bin/python main.py --port /dev/cu.usbserial-0001
```

The process starts in **dry-run**. Close the Serial Monitor first so the port is free. The host prints `stream <n> Hz accepted=<n> rejected=<n> mode=DRY-RUN` about once a second. `accepted` should climb. `imu=missing`, `no I2C devices found`, or a stream that stays at 0 Hz means the chip is unwired or the port is wrong.

`--game minecraft` or `--game roblox` copies that game's suggested bindings into `host/data/controls.json`. The default action set is `combat` (a gesture id `swing` suggests attack, which is `left_click`). `--set build` switches the set. `--user NAME` applies a swing binding saved by the older profile tools when one exists. You can change any binding afterward without restarting.

## Calibrate a gesture

Type a command and press Enter while the host is running:

```text
calibrate Swing
```

The host counts down, then gives you one window (about 3 seconds, `swing_window_seconds`) for each of three repetitions. Do the movement once and hold still. A take is rejected, with a reason, when it is too short, mostly still, full of missing lines, or not the same movement as the other takes. Nothing is saved in that case. Run `calibrate` again.

A saved gesture stores the id, display name, feature names (`gx`, `gy`, `gz`, gyro magnitude in deg/s, acceleration magnitude in g), one resampled template, a distance threshold, the sample rate, the repetition lengths, and the time it was created. The file is `host/data/gestures.json`, version 1.

```text
list
show Swing
rename Swing Attack
delete Attack
```

`rename` keeps the id, so the binding stays. Recalibrate by running `calibrate` with the same name. Calibration does not turn live mode on.

## Mouse look

Type `look` and press Enter. Three steps, and you do not press Enter between them.

1. Hold still. The average gyro reading is the zero. It is subtracted afterwards so the view does not drift.
2. Turn the toy to the right for about a second. That chooses the horizontal axis and its sign.
3. Tilt the toy up for about a second. That chooses the vertical axis. Up looks up.

One degree of that turn is 18 horizontal mouse pixels, or 14 vertical pixels. Faster turns are multiplied a bit more. `aim horizontal 30` makes it stronger without another `look`.

The status line shows `gx gy gz` and `dx dy`. At rest, `dx` and `dy` stay near 0. Dry-run does not move the pointer. Type `live`, then `LIVE`. Click the Minecraft window first. The host sends relative mouse deltas, which is what Minecraft reads, and it also moves the desktop pointer so you can see the same motion outside the game. Minecraft's own sensitivity slider still scales the camera.

`stop`, or Esc three times within one second, releases input and latches the stop.

One calibration does the whole look setup. Close the Serial Monitor first.

```bash
cd host
.venv/bin/python calibrate.py --port /dev/cu.usbserial-0001
```

Hold still, turn right, then tilt up. Do not run it again unless you want to replace that mounting.

Then play:

```bash
.venv/bin/python main.py --port /dev/cu.usbserial-0001 --live
```

Wait for `mode=LIVE` and click the window you want to control.

- Turning the toy moves the mouse.
- Holding the button for at least 0.3 seconds holds W continuously until you let go.
- One button click pauses the mouse. A large, quick swing then left-clicks, and the mouse can move again.
- Two quick clicks pause the mouse. A large, quick swing then right-clicks.

An armed click requires about **60 degrees of net rotation**, a peak speed of
at least **200 degrees/second**, and at least **0.12 seconds** of movement,
completed within **0.8 seconds**. Start from still. A small flick, a single
sensor spike, a slow turn, or repeated small back-and-forth shakes will not
consume the click opportunity. These initial thresholds live in
`host/recognition/swing.py`; they still need hands-on tuning once the button works.
The detector uses the calibrated gyro bias and actual sample times. It shares
the existing sensor loop with mouse-look and never sleeps to wait for a swing.

## Recognition

A movement starts when gyro magnitude crosses `onset_dps` and ends after the toy is still. That window is separate from naming it. The window is resampled and compared with every saved template using Sakoe-Chiba DTW (`dtw_band` in `host/config/serial.json`). The closest template wins only when its distance is within its own threshold and the next template is not also close (`ambiguity_ratio`). Otherwise the host prints `no match`. The same gesture cannot fire again until `refractory_ms` has passed.

`host/recognition/rotation.py` is still the rotation-threshold detector. `main.py` does not use it to name gestures.

## Aiming

Aim is off until you turn it on. The default map is a starting guess, not a claim about how the board is mounted: gyro X moves the pointer horizontally and gyro Y moves it vertically. Change it if the toy does not point the way you expect.

```text
aim on
aim axis gx gy
aim sensitivity 0.15
aim deadzone 12
aim invert horizontal
aim rotate 90
aim off
```

`aim sensitivity` is pixels per degree. Motion is relative. There is a dead zone, an exponential smoother, and a maximum pointer speed. Aim is paused while a gesture is in progress so a swing is not also a mouse sweep. In dry-run the host computes the motion and does not move the pointer.

If the sensor is mounted sideways on a sword, use `aim rotate 90` to rotate its
mouse-look directions clockwise by one quarter turn. Choose `270` for the
opposite direction, `180` for upside down, or `0` for the original mapping.
This setting is saved as `aim.rotation_deg` in `host/data/controls.json`.
Horizontal and vertical sensitivity still refer to the screen directions.
A fresh `look` calibration learns the new mounting and resets this extra rotation to `0`.

You can also choose the setting when starting the controller on Windows:

```powershell
.\.venv\Scripts\python.exe host\main.py --port COM3 --look-rotation 90 --live
```

## Map a gesture to an action

```text
game minecraft
bind swing left_click
bind block right_click
bind special e
```

`left_click`, `right_click`, and `middle_click` are mouse buttons. Any other name is a key (`space`, `left_ctrl`, `a`, `e`). Bindings are stored in `host/data/controls.json`. A physical button, once the firmware pin is set, uses the button input from the game table (combat maps `btn0` to jump, which is `space`). You can replace that by editing `button_input` in the controls file.

## Live input and the emergency stop

Dry-run prints `dry-run: would press …` and sends nothing. To send input:

```text
live
LIVE
```

or start with `--live`. On macOS, System Settings → Privacy & Security must allow this terminal under **Accessibility**. pynput also needs **Input Monitoring** for reading the Esc stop. If the listener cannot start, `--live` exits and the `live` command stays in dry-run.

Stop everything:

- Type `stop` and Enter.
- Or press Esc three times within one second.

Either one latches the emergency stop, releases held keys and mouse buttons, and ignores further gestures until the process exits. `dry` turns injection off without latching the stop. Quitting or an unexpected serial failure also releases held inputs. A dropped port is opened again once; if that fails, the host exits after the release.

## Tests

From the repository root on Windows:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The suite does not open a serial port and does not send mouse or keyboard input. Controllers are mocked.

## Known limits

- The axis map is configurable because the clip's mounting has not been measured. `gx` horizontal and `gy` vertical is only the default.
- There is no orientation fusion and no quaternion in the CSV stream. Position is not estimated.
- No button pin is verified. The firmware will not read one until `GAMEGRIP_BUTTON_PIN` is set to the wired GPIO.
- There is no battery reading and no Wi-Fi or Bluetooth in the active sketch.
- Thresholds in `host/config/serial.json` (`onset_dps`, `min_threshold`, `match_margin`, aim sensitivity) are starting values. They have not been tuned on the toy.
- The old UDP packet in `imuNumbers` mixed accelerometer values into quaternion fields, declared 44 bytes, and wrote 40. That packet is not the host contract.
- USB serial is the transport. A wireless link can replace `open_serial` later.
- Automated checks use synthetic recordings and mock desktop inputs. Verify the physical direction mapping and in-game mouse response with the actual sword after calibration.
