#include "imu_driver.h"

#include <Arduino.h>
#include <Wire.h>

static const int kImuSdaPin = 21;
static const int kImuSclPin = 22;
static const uint8_t kMpu6050Address = 0x68;
static const uint32_t kI2cClockHz = 400000;
static const uint16_t kI2cTimeoutMs = 50;
static const uint8_t kI2cScanFirst = 0x01;
static const uint8_t kI2cScanLast = 0x7E;
static const uint8_t kWireOk = 0;
static const uint8_t kHexPadBelow = 0x10;

static const uint8_t kRegSmplrtDiv = 0x19;
static const uint8_t kRegConfig = 0x1A;
static const uint8_t kRegGyroConfig = 0x1B;
static const uint8_t kRegAccelConfig = 0x1C;
static const uint8_t kRegAccelXoutH = 0x3B;
static const uint8_t kRegPwrMgmt1 = 0x6B;
static const uint8_t kRegWhoAmI = 0x75;

static const uint8_t kWhoAmIMpu6050 = 0x68;
static const uint8_t kPwrMgmtWake = 0x00;
static const uint32_t kMpuWakeDelayMs = 100;

// DLPF_CFG 3: accel bandwidth 44 Hz, gyro bandwidth 42 Hz, gyro clock 1 kHz.
static const uint8_t kDlpfCfg42Hz = 0x03;
static const uint16_t kGyroOutputRateHz = 1000;
static const uint16_t kImuSampleRateHz = 200;
static const uint8_t kSmplrtDiv =
    static_cast<uint8_t>((kGyroOutputRateHz / kImuSampleRateHz) - 1);

// FS_SEL lives in bits 4:3. 1 is ±500 deg/s. 1 is ±4 g.
static const uint8_t kFsSelShift = 3;
static const uint8_t kGyroFsSel500Dps = 1;
static const uint8_t kAccelFsSel4G = 1;
static const uint8_t kGyroConfig500Dps = kGyroFsSel500Dps << kFsSelShift;
static const uint8_t kAccelConfig4G = kAccelFsSel4G << kFsSelShift;

static const float kGyroLsbPerDegPerS = 65.5f;
static const float kAccelLsbPerG = 8192.0f;
static const float kPi = 3.14159265358979323846f;
static const float kDegToRad = kPi / 180.0f;
static const float kGravityMps2 = 9.80665f;

static const uint8_t kImuBurstBytes = 14;
static const uint8_t kBurstGyroOffset = 8;
static const uint8_t kWhoAmIBytes = 1;

static bool g_bus_ready = false;

static bool i2c_bus_begin() {
    if (g_bus_ready) {
        return true;
    }
    if (!Wire.begin(kImuSdaPin, kImuSclPin, kI2cClockHz)) {
        return false;
    }
    // A stuck bus returns after this instead of waiting forever.
    Wire.setTimeOut(kI2cTimeoutMs);
    g_bus_ready = true;
    return true;
}

static void print_address(uint8_t address) {
    Serial.print("0x");
    if (address < kHexPadBelow) {
        Serial.print('0');
    }
    Serial.print(address, HEX);
}

static bool write_register(uint8_t reg, uint8_t value) {
    Wire.beginTransmission(kMpu6050Address);
    Wire.write(reg);
    Wire.write(value);
    return Wire.endTransmission() == kWireOk;
}

static bool read_bytes(uint8_t reg, uint8_t *dest, size_t length) {
    Wire.beginTransmission(kMpu6050Address);
    Wire.write(reg);
    if (Wire.endTransmission(false) != kWireOk) {
        return false;
    }
    size_t got = Wire.requestFrom(kMpu6050Address, length);
    if (got != length) {
        return false;
    }
    for (size_t i = 0; i < length; i++) {
        if (Wire.available() <= 0) {
            return false;
        }
        dest[i] = (uint8_t)Wire.read();
    }
    return true;
}

static int16_t read_be_i16(const uint8_t *bytes) {
    return (int16_t)(((uint16_t)bytes[0] << 8) | bytes[1]);
}

bool i2c_scan() {
    if (!i2c_bus_begin()) {
        Serial.print("I2C bus failed to start (SDA ");
        Serial.print(kImuSdaPin);
        Serial.print(", SCL ");
        Serial.print(kImuSclPin);
        Serial.println(").");
        return false;
    }

    Serial.print("I2C scan SDA=");
    Serial.print(kImuSdaPin);
    Serial.print(" SCL=");
    Serial.println(kImuSclPin);

    int found = 0;
    for (uint8_t address = kI2cScanFirst; address <= kI2cScanLast; address++) {
        Wire.beginTransmission(address);
        if (Wire.endTransmission() == kWireOk) {
            Serial.print("I2C device at ");
            print_address(address);
            Serial.println();
            found++;
        }
    }
    if (found == 0) {
        Serial.println("no I2C devices found");
    } else {
        Serial.print("I2C scan done, ");
        Serial.print(found);
        Serial.println(" device(s)");
    }
    return true;
}

bool imu_driver_begin() {
    if (!i2c_bus_begin()) {
        return false;
    }

    Wire.beginTransmission(kMpu6050Address);
    if (Wire.endTransmission() != kWireOk) {
        return false;
    }

    uint8_t who = 0;
    if (!read_bytes(kRegWhoAmI, &who, kWhoAmIBytes) || who != kWhoAmIMpu6050) {
        return false;
    }

    if (!write_register(kRegPwrMgmt1, kPwrMgmtWake)) {
        return false;
    }
    delay(kMpuWakeDelayMs);
    if (!write_register(kRegConfig, kDlpfCfg42Hz)) {
        return false;
    }
    if (!write_register(kRegSmplrtDiv, kSmplrtDiv)) {
        return false;
    }
    if (!write_register(kRegGyroConfig, kGyroConfig500Dps)) {
        return false;
    }
    if (!write_register(kRegAccelConfig, kAccelConfig4G)) {
        return false;
    }
    return true;
}

bool imu_driver_read(Vec3 *accel, Vec3 *gyro) {
    if (accel == NULL || gyro == NULL) {
        return false;
    }
    uint8_t raw[kImuBurstBytes];
    if (!read_bytes(kRegAccelXoutH, raw, kImuBurstBytes)) {
        return false;
    }

    int16_t raw_ax = read_be_i16(raw + 0);
    int16_t raw_ay = read_be_i16(raw + 2);
    int16_t raw_az = read_be_i16(raw + 4);
    int16_t raw_gx = read_be_i16(raw + kBurstGyroOffset);
    int16_t raw_gy = read_be_i16(raw + kBurstGyroOffset + 2);
    int16_t raw_gz = read_be_i16(raw + kBurstGyroOffset + 4);

    accel->x = (raw_ax / kAccelLsbPerG) * kGravityMps2;
    accel->y = (raw_ay / kAccelLsbPerG) * kGravityMps2;
    accel->z = (raw_az / kAccelLsbPerG) * kGravityMps2;
    gyro->x = (raw_gx / kGyroLsbPerDegPerS) * kDegToRad;
    gyro->y = (raw_gy / kGyroLsbPerDegPerS) * kDegToRad;
    gyro->z = (raw_gz / kGyroLsbPerDegPerS) * kDegToRad;
    return true;
}
