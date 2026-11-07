#include "fusion.h"

#include <math.h>

// Accelerometer feedback gain. Larger values follow gravity sooner and
// trust the gyro less. This is the 6-DOF Madgwick IMU beta (no magnetometer).
static const float kMadgwickBeta = 0.1f;

static Quat g_q = {1.0f, 0.0f, 0.0f, 0.0f};
static Quat g_ref = {1.0f, 0.0f, 0.0f, 0.0f};

static Quat quat_normalize(Quat q) {
    float norm = sqrtf(q.w * q.w + q.x * q.x + q.y * q.y + q.z * q.z);
    if (norm <= 0.0f) {
        Quat identity = {1.0f, 0.0f, 0.0f, 0.0f};
        return identity;
    }
    Quat out;
    out.w = q.w / norm;
    out.x = q.x / norm;
    out.y = q.y / norm;
    out.z = q.z / norm;
    return out;
}

static Quat quat_conjugate(Quat q) {
    Quat out;
    out.w = q.w;
    out.x = -q.x;
    out.y = -q.y;
    out.z = -q.z;
    return out;
}

static Quat quat_multiply(Quat left, Quat right) {
    Quat out;
    out.w = left.w * right.w - left.x * right.x - left.y * right.y - left.z * right.z;
    out.x = left.w * right.x + left.x * right.w + left.y * right.z - left.z * right.y;
    out.y = left.w * right.y - left.x * right.z + left.y * right.w + left.z * right.x;
    out.z = left.w * right.z + left.x * right.y - left.y * right.x + left.z * right.w;
    return out;
}

void fusion_update(const Vec3 &gyro, const Vec3 &accel, float dt) {
    if (dt <= 0.0f) {
        return;
    }

    float q0 = g_q.w;
    float q1 = g_q.x;
    float q2 = g_q.y;
    float q3 = g_q.z;
    float gx = gyro.x;
    float gy = gyro.y;
    float gz = gyro.z;

    float q_dot0 = 0.5f * (-q1 * gx - q2 * gy - q3 * gz);
    float q_dot1 = 0.5f * (q0 * gx + q2 * gz - q3 * gy);
    float q_dot2 = 0.5f * (q0 * gy - q1 * gz + q3 * gx);
    float q_dot3 = 0.5f * (q0 * gz + q1 * gy - q2 * gx);

    float ax = accel.x;
    float ay = accel.y;
    float az = accel.z;
    if (!((ax == 0.0f) && (ay == 0.0f) && (az == 0.0f))) {
        float recip = 1.0f / sqrtf(ax * ax + ay * ay + az * az);
        ax *= recip;
        ay *= recip;
        az *= recip;

        float two_q0 = 2.0f * q0;
        float two_q1 = 2.0f * q1;
        float two_q2 = 2.0f * q2;
        float two_q3 = 2.0f * q3;
        float four_q0 = 4.0f * q0;
        float four_q1 = 4.0f * q1;
        float four_q2 = 4.0f * q2;
        float eight_q1 = 8.0f * q1;
        float eight_q2 = 8.0f * q2;
        float q0q0 = q0 * q0;
        float q1q1 = q1 * q1;
        float q2q2 = q2 * q2;
        float q3q3 = q3 * q3;

        float s0 = four_q0 * q2q2 + two_q2 * ax + four_q0 * q1q1 - two_q1 * ay;
        float s1 = four_q1 * q3q3 - two_q3 * ax + 4.0f * q0q0 * q1 - two_q0 * ay - four_q1 +
                   eight_q1 * q1q1 + eight_q1 * q2q2 + four_q1 * az;
        float s2 = 4.0f * q0q0 * q2 + two_q0 * ax + four_q2 * q3q3 - two_q3 * ay - four_q2 +
                   eight_q2 * q1q1 + eight_q2 * q2q2 + four_q2 * az;
        float s3 = 4.0f * q1q1 * q3 - two_q1 * ax + 4.0f * q2q2 * q3 - two_q2 * ay;
        float step_norm = sqrtf(s0 * s0 + s1 * s1 + s2 * s2 + s3 * s3);
        if (step_norm > 0.0f) {
            float step_recip = 1.0f / step_norm;
            s0 *= step_recip;
            s1 *= step_recip;
            s2 *= step_recip;
            s3 *= step_recip;
            q_dot0 -= kMadgwickBeta * s0;
            q_dot1 -= kMadgwickBeta * s1;
            q_dot2 -= kMadgwickBeta * s2;
            q_dot3 -= kMadgwickBeta * s3;
        }
    }

    q0 += q_dot0 * dt;
    q1 += q_dot1 * dt;
    q2 += q_dot2 * dt;
    q3 += q_dot3 * dt;

    Quat updated;
    updated.w = q0;
    updated.x = q1;
    updated.y = q2;
    updated.z = q3;
    g_q = quat_normalize(updated);
}

Quat fusion_quaternion() {
    // conj(reference) * current is identity at the moment of the reset.
    return quat_normalize(quat_multiply(quat_conjugate(g_ref), g_q));
}

void reset_orientation() {
    g_ref = g_q;
}
