#include "fake_imu.h"

#include "config.h"

#include <math.h>

// Same periods as scripts/fake_udp.py. Gyro and the z rotation are both
// sine pulses; gyro is not the derivative of the quaternion.
static const double kSwingPeriodS = 2.5;
static const double kSwingDurationS = 1.0;
static const double kLowerPhaseOffsetS = 0.5;
static const double kPi = 3.14159265358979323846;
static const double kSwingMaxRad = 30.0 * kPi / 180.0;
static const double kSwingPeakGyro = 1.0;
static const float kIdleAccel = 9.81f;

// Python's % on a float stays non-negative. fmod() does not.
static double wrap_period(double value, double period) {
    double wrapped = fmod(value, period);
    if (wrapped < 0.0) {
        wrapped += period;
    }
    return wrapped;
}

void fake_imu(uint32_t t_ms, ImuSample *out) {
    const double t_s = (double)t_ms / 1000.0;
    const double offset = (MODULE_ID == 1) ? kLowerPhaseOffsetS : 0.0;
    const double local = wrap_period(t_s - offset, kSwingPeriodS);

    if (local >= kSwingDurationS) {
        out->qw = 1.0f;
        out->qx = 0.0f;
        out->qy = 0.0f;
        out->qz = 0.0f;
        out->gyro_mag = 0.0f;
        out->accel_mag = kIdleAccel;
        out->still = true;
        return;
    }

    const double pulse = sin(kPi * (local / kSwingDurationS));
    const double half = (kSwingMaxRad * pulse) * 0.5;
    out->qw = (float)cos(half);
    out->qx = 0.0f;
    out->qy = 0.0f;
    out->qz = (float)sin(half);
    out->gyro_mag = (float)(kSwingPeakGyro * pulse);
    out->accel_mag = kIdleAccel;
    out->still = false;
}
