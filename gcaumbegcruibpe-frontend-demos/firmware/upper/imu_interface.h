#ifndef GAMEGRIP_IMU_INTERFACE_H
#define GAMEGRIP_IMU_INTERFACE_H

#include "fake_imu.h"

// Scans I2C, then brings up the MPU6050 unless FORCE_FAKE_IMU is 1.
// True only when 0x68 answers and WHO_AM_I matches. A missing chip returns
// false and the following reads use fake_imu.
bool imu_begin();

// Fills out from the MPU6050 filter, or from fake_imu after a failed begin.
// False only when the real chip was selected and this read did not complete.
bool read_imu(ImuSample *out);

bool imu_using_real();
bool imu_fusion_ok();

#endif
