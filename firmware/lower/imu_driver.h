#ifndef GAMEGRIP_IMU_DRIVER_H
#define GAMEGRIP_IMU_DRIVER_H

#include "fusion.h"

#include <stdint.h>

// Start Wire on SDA 21 / SCL 22 and print every address that ACKs.
// Returns false when the bus itself cannot start. A missing chip still
// returns true: the address list is the diagnostic.
bool i2c_scan();

// True only when 0x68 ACKs and WHO_AM_I is the MPU6050 value.
bool imu_driver_begin();

// Latest accel (m/s^2) and gyro (rad/s). False when the read does not complete.
bool imu_driver_read(Vec3 *accel, Vec3 *gyro);

#endif
