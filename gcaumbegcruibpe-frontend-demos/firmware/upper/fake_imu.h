#ifndef GAMEGRIP_FAKE_IMU_H
#define GAMEGRIP_FAKE_IMU_H

#include <stdint.h>

// Synthetic sample. still is the STILL flag; FAKE_IMU is added by the sketch.
struct ImuSample {
    float qw;
    float qx;
    float qy;
    float qz;
    float gyro_mag;
    float accel_mag;
    bool still;
};

// Deterministic stand-in for the IMU. Must match scripts/fake_udp.py.
void fake_imu(uint32_t t_ms, ImuSample *out);

#endif
