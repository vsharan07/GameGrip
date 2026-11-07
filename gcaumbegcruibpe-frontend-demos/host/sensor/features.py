"""Features from one CSV sample.

Axes stay in the firmware units: acceleration in g, angular velocity in
deg/s. Nothing here is a quaternion, and nothing is integrated into position.
"""

import math

FEATURE_NAMES = ("gx_dps", "gy_dps", "gz_dps", "gyro_mag_dps", "accel_mag_g")


def gyro_mag(sample):
    return math.sqrt(sample[3] * sample[3] + sample[4] * sample[4] + sample[5] * sample[5])


def accel_mag(sample):
    return math.sqrt(sample[0] * sample[0] + sample[1] * sample[1] + sample[2] * sample[2])


def feature_vector(sample):
    gx, gy, gz = sample[3], sample[4], sample[5]
    return (gx, gy, gz, gyro_mag(sample), accel_mag(sample))


def resample(series, length):
    """Linear resample so recordings of different lengths share one timeline."""
    if length < 2:
        raise ValueError("resample length must be at least 2")
    if not series:
        return []
    width = len(series[0])
    if len(series) == 1:
        return [tuple(series[0]) for _ in range(length)]
    last = len(series) - 1
    output = []
    for index in range(length):
        position = last * (float(index) / float(length - 1))
        low = int(position)
        high = min(low + 1, last)
        fraction = position - low
        output.append(tuple(
            series[low][dim] * (1.0 - fraction) + series[high][dim] * fraction
            for dim in range(width)
        ))
    return output
