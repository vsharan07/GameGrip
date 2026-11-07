#pragma once

// Active GameGrip sketch: one ESP32, one MPU6050, serial CSV to the host.
// There is no Wi-Fi block here. The previous two-board sketches keep their own config.h.

#define GAMEGRIP_IMU_SDA 21
#define GAMEGRIP_IMU_SCL 22
#define GAMEGRIP_MPU_ADDR 0x68
#define GAMEGRIP_SERIAL_BAUD 115200

// One button, other leg to GND. INPUT_PULLUP, LOW means pressed.
// Unconnected stays released. The CSV line gains a 7th column, 0 or 1.
#define GAMEGRIP_BUTTON_PIN 26
