# Wiring

The active sketch, `firmware/gamegrip/gamegrip.ino`, uses one ESP32 and one MPU6050. SDA is GPIO 21, SCL is GPIO 22, and AD0 is GND so the address is `0x68`. `GAMEGRIP_BUTTON_PIN` is `-1` until a button is actually wired. Set it to that GPIO only after the button connects the pin to GND; the sketch then uses `INPUT_PULLUP`, treats LOW as pressed, debounces for 25 ms, and adds a 7th CSV column of `0` or `1`. Do not reuse GPIO 25 or GPIO 26 from the old lower board unless that is the pin you wired. The two-button wiring and the second board below belong to the previous firmware.

Each ESP32 in that older setup has its own MPU6050 on the same pins. The lower board also has the two buttons. The battery divider is optional and stays off until `BATTERY_ENABLED` is 1.

## MPU6050

SDA is GPIO 21. SCL is GPIO 22. AD0 tied to GND selects address `0x68`. Leave INT unconnected.

| MPU6050 | ESP32 |
| --- | --- |
| SDA | GPIO 21 |
| SCL | GPIO 22 |
| AD0 | GND |
| GND | GND |
| INT | not connected |

The chip itself runs at 3.3 V. On a GY-521 style breakout, the pin marked VCC feeds the onboard regulator. Connect that pin to the ESP32 VIN (5 V while USB-powered) and connect GND to the ESP32 GND. The breakout pulls SDA and SCL up to its own 3.3 V rail, so those two wires go straight to GPIO 21 and GPIO 22. Do not connect SDA or SCL to 5 V.

A board with no regulator, or a pin already marked 3.3 V, takes VCC from the ESP32 3V3 pin instead of VIN.

The sketch lists every address that ACKs. `0x68` means this wiring answered. `0x69` means AD0 is high. An empty scan means power, GND, SDA, or SCL is open.

## Battery divider

GPIO 34 is ADC1 channel 6, input only, and it does not fight Wi-Fi. Leave it unconnected and keep `BATTERY_ENABLED` at 0 until the divider is in place. A floating pin is not a cell voltage.

Use two 100 kΩ resistors. The firmware constant `kBatteryDividerRatio` is 2.0, which is `(R_top + R_bottom) / R_bottom`.

| Node | Connects to |
| --- | --- |
| Cell positive | one side of R_top |
| GPIO 34 and the other side of R_top | one side of R_bottom |
| The other side of R_bottom | GND |
| Cell negative | ESP32 GND |

A full cell near 4.2 V then sits near 2.1 V on the pin. Do not tie the cell straight to GPIO 34. The pin is treated as 0 to 3.3 V at 12-bit resolution with 11 dB attenuation, and the sketch multiplies that by 2. `LOW_BATT` (flags bit3) is set when the cell is below 3.4 V. At 3.4 V or above the bit stays clear.

## Lower-board buttons

Use the internal pull-ups. No external resistor.

| Button | ESP32 pin | Other leg |
| --- | --- | --- |
| btn0 | GPIO 25 | GND |
| btn1 | GPIO 26 | GND |

The pin reads LOW while the button is held. The sketch inverts that, so the packet field is 1 when pressed and 0 when released. Each button is debounced for 25 ms.

Holding button 1 for 1.5 s after that debounce calls `reset_orientation()` once per press. Later quaternions from the real IMU are relative to the orientation at that moment. Releasing the button arms the next long press.

An unconnected button pin is pulled HIGH, which the firmware reports as not pressed. A missing button does not look like a held button.
