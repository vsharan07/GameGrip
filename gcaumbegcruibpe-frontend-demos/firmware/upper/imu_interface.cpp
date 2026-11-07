#include "imu_interface.h"

#include "config.h"
#include "fusion.h"
#include "imu_driver.h"

#include <Arduino.h>
#include <math.h>

// Same threshold the host uses for still_gyro_threshold, in rad/s.
static const float kStillGyroRadPerS = 0.2f;
static const float kMillisToSeconds = 0.001f;
static const float kImuSamplePeriodS = 1.0f / 200.0f;
// A Wi-Fi stall must not integrate the gyro across that whole gap.
static const float kFusionDtMaxS = 0.05f;

static bool g_started = false;
static bool g_real = false;
static bool g_fusion_ok = false;
static uint32_t g_last_update_ms = 0;

static float magnitude(const Vec3 &v) {
    return sqrtf(v.x * v.x + v.y * v.y + v.z * v.z);
}

bool imu_using_real() {
    return g_real;
}

bool imu_fusion_ok() {
    return g_fusion_ok;
}

bool imu_begin() {
    if (g_started) {
        return g_real;
    }
    g_started = true;
    g_real = false;
    g_fusion_ok = false;

    bool bus_ok = i2c_scan();
    if (FORCE_FAKE_IMU) {
        Serial.println("FORCE_FAKE_IMU is 1. Using fake IMU. FUSION_OK=0.");
        return false;
    }
    if (!bus_ok) {
        Serial.println("Falling back to fake IMU. FUSION_OK=0.");
        return false;
    }
    if (!imu_driver_begin()) {
        Serial.println(
            "MPU6050 not found at 0x68. WHO_AM_I check failed. Falling back to fake IMU. FUSION_OK=0.");
        return false;
    }

    g_real = true;
    g_fusion_ok = true;
    g_last_update_ms = millis();
    Serial.println("MPU6050 ready at 0x68. WHO_AM_I=0x68.");
    return true;
}

bool read_imu(ImuSample *out) {
    if (out == NULL) {
        return false;
    }
    if (!g_started) {
        imu_begin();
    }
    if (!g_real) {
        fake_imu(millis(), out);
        return true;
    }

    Vec3 accel;
    Vec3 gyro;
    if (!imu_driver_read(&accel, &gyro)) {
        return false;
    }

    uint32_t now_ms = millis();
    float dt = (float)(now_ms - g_last_update_ms) * kMillisToSeconds;
    g_last_update_ms = now_ms;
    if (dt <= 0.0f) {
        dt = kImuSamplePeriodS;
    } else if (dt > kFusionDtMaxS) {
        dt = kFusionDtMaxS;
    }
    fusion_update(gyro, accel, dt);

    Quat q = fusion_quaternion();
    out->qw = q.w;
    out->qx = q.x;
    out->qy = q.y;
    out->qz = q.z;
    out->gyro_mag = magnitude(gyro);
    out->accel_mag = magnitude(accel);
    out->still = out->gyro_mag < kStillGyroRadPerS;
    return true;
}
