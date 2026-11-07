#ifndef GAMEGRIP_FUSION_H
#define GAMEGRIP_FUSION_H

// 6-DOF orientation. Gyro is rad/s. Accel is m/s^2. The filter normalizes accel.

struct Vec3 {
    float x;
    float y;
    float z;
};

struct Quat {
    float w;
    float x;
    float y;
    float z;
};

// Integrate one IMU sample. dt is seconds since the previous call.
void fusion_update(const Vec3 &gyro, const Vec3 &accel, float dt);

// Orientation relative to the last reset_orientation() reference.
// Before the first reset this is the absolute filter quaternion.
Quat fusion_quaternion();

// Store the current absolute quaternion and make later outputs relative to it.
void reset_orientation();

#endif
