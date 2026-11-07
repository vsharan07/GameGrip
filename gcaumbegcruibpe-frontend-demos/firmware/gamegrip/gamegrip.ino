#include <Wire.h>

#include "config.h"

// Serial contract with host/sensor/csv_stream.py:
//   one line per sample, ax,ay,az,gx,gy,gz
//   accelerometer in g, gyroscope in deg/s
//   optional 7th column 0 or 1 only when GAMEGRIP_BUTTON_PIN is 0 or higher
// Diagnostic lines have no six-value CSV, so the host ignores them.
// This sketch does not estimate orientation. qw/qx in the old UDP packet
// were not a quaternion, and that packet is not sent from here.
// GAMEGRIP_BUTTON_PIN defaults to -1. No GPIO is claimed until one is wired.

static const uint8_t kMpuAddress = GAMEGRIP_MPU_ADDR;
static const int kImuSdaPin = GAMEGRIP_IMU_SDA;
static const int kImuSclPin = GAMEGRIP_IMU_SCL;
static const uint32_t kSerialBaud = GAMEGRIP_SERIAL_BAUD;

static const uint8_t kRegPwrMgmt1 = 0x6B;
static const uint8_t kRegGyroConfig = 0x1B;
static const uint8_t kRegAccelConfig = 0x1C;
static const uint8_t kRegAccelXoutH = 0x3B;
static const uint8_t kRegWhoAmI = 0x75;
static const uint8_t kWhoAmIMpu6050 = 0x68;
static const uint8_t kPwrMgmtWake = 0x00;
static const uint32_t kMpuWakeDelayMs = 100;

static const uint8_t kFsSelShift = 3;
static const uint8_t kGyroFsSel2000Dps = 3;
static const uint8_t kAccelFsSel8G = 2;
static const uint8_t kGyroConfig2000Dps = kGyroFsSel2000Dps << kFsSelShift;
static const uint8_t kAccelConfig8G = kAccelFsSel8G << kFsSelShift;
static const float kGyroLsbPerDps = 16.4f;
static const float kAccelLsbPerG = 4096.0f;

static const uint8_t kImuBurstBytes = 14;
static const uint8_t kBurstGyroOffset = 8;
static const uint8_t kI2cScanFirst = 0x01;
static const uint8_t kI2cScanLast = 0x7E;
static const uint8_t kWireOk = 0;
static const uint8_t kHexPadBelow = 0x10;
static const uint32_t kI2cClockHz = 400000;
static const uint16_t kI2cTimeoutMs = 50;

static const uint32_t kSamplePeriodMs = 20;
static const uint32_t kReportPeriodMs = 1000;
static const uint32_t kBootSerialWaitMs = 200;
static const int kButtonPin = GAMEGRIP_BUTTON_PIN;
static const uint32_t kButtonDebounceMs = 25;

#if defined(LED_BUILTIN)
static const int kStatusLed = LED_BUILTIN;
#else
static const int kStatusLed = 2;
#endif

static bool g_imu_ready = false;
static uint32_t g_next_sample_ms = 0;
static uint32_t g_next_report_ms = 0;
static uint32_t g_csv_samples = 0;
static uint32_t g_read_failures = 0;
static uint8_t g_button_stable = 0;
static uint8_t g_button_candidate = 0;
static uint32_t g_button_since_ms = 0;

static int16_t read_be_i16(const uint8_t *bytes) {
    return (int16_t)(((uint16_t)bytes[0] << 8) | bytes[1]);
}

static void print_address(uint8_t address) {
    Serial.print("0x");
    if (address < kHexPadBelow) {
        Serial.print('0');
    }
    Serial.print(address, HEX);
}

static bool i2c_start() {
    if (!Wire.begin(kImuSdaPin, kImuSclPin, kI2cClockHz)) {
        Serial.print("I2C bus failed to start (SDA ");
        Serial.print(kImuSdaPin);
        Serial.print(", SCL ");
        Serial.print(kImuSclPin);
        Serial.println(").");
        return false;
    }
    Wire.setTimeOut(kI2cTimeoutMs);
    return true;
}

static void i2c_scan() {
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
}

static bool write_register(uint8_t reg, uint8_t value) {
    Wire.beginTransmission(kMpuAddress);
    Wire.write(reg);
    Wire.write(value);
    return Wire.endTransmission() == kWireOk;
}

static bool read_bytes(uint8_t reg, uint8_t *dest, size_t length) {
    Wire.beginTransmission(kMpuAddress);
    Wire.write(reg);
    if (Wire.endTransmission(false) != kWireOk) {
        return false;
    }
    if (Wire.requestFrom(kMpuAddress, length) != length) {
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

static bool imu_begin() {
    if (!i2c_start()) {
        g_imu_ready = false;
        return false;
    }
    i2c_scan();

    Wire.beginTransmission(kMpuAddress);
    if (Wire.endTransmission() != kWireOk) {
        Serial.println("MPU6050 did not answer at 0x68. Not streaming sensor data.");
        g_imu_ready = false;
        return false;
    }
    uint8_t who = 0;
    if (!read_bytes(kRegWhoAmI, &who, 1) || who != kWhoAmIMpu6050) {
        Serial.println("MPU6050 WHO_AM_I check failed at 0x68. Not streaming sensor data.");
        g_imu_ready = false;
        return false;
    }
    if (!write_register(kRegPwrMgmt1, kPwrMgmtWake)) {
        Serial.println("MPU6050 wake failed. Not streaming sensor data.");
        g_imu_ready = false;
        return false;
    }
    delay(kMpuWakeDelayMs);
    if (!write_register(kRegGyroConfig, kGyroConfig2000Dps) ||
        !write_register(kRegAccelConfig, kAccelConfig8G)) {
        Serial.println("MPU6050 range setup failed. Not streaming sensor data.");
        g_imu_ready = false;
        return false;
    }
    g_imu_ready = true;
    Serial.println("MPU6050 ready at 0x68. gyro +-2000 dps, accel +-8 g.");
    return true;
}

static bool read_sample(float *ax_g, float *ay_g, float *az_g, float *gx_dps, float *gy_dps, float *gz_dps) {
    uint8_t raw[kImuBurstBytes];
    if (!read_bytes(kRegAccelXoutH, raw, kImuBurstBytes)) {
        return false;
    }
    int16_t ax = read_be_i16(raw + 0);
    int16_t ay = read_be_i16(raw + 2);
    int16_t az = read_be_i16(raw + 4);
    int16_t gx = read_be_i16(raw + kBurstGyroOffset);
    int16_t gy = read_be_i16(raw + kBurstGyroOffset + 2);
    int16_t gz = read_be_i16(raw + kBurstGyroOffset + 4);
    *ax_g = ax / kAccelLsbPerG;
    *ay_g = ay / kAccelLsbPerG;
    *az_g = az / kAccelLsbPerG;
    *gx_dps = gx / kGyroLsbPerDps;
    *gy_dps = gy / kGyroLsbPerDps;
    *gz_dps = gz / kGyroLsbPerDps;
    return true;
}

static void poll_button(uint32_t now_ms) {
    if (kButtonPin < 0) {
        return;
    }
    uint8_t pressed = digitalRead(kButtonPin) == LOW ? 1 : 0;
    if (pressed != g_button_candidate) {
        g_button_candidate = pressed;
        g_button_since_ms = now_ms;
    } else if ((uint32_t)(now_ms - g_button_since_ms) >= kButtonDebounceMs) {
        g_button_stable = pressed;
    }
}

static void print_csv() {
    float ax_g = 0.0f;
    float ay_g = 0.0f;
    float az_g = 0.0f;
    float gx_dps = 0.0f;
    float gy_dps = 0.0f;
    float gz_dps = 0.0f;
    if (!read_sample(&ax_g, &ay_g, &az_g, &gx_dps, &gy_dps, &gz_dps)) {
        g_read_failures++;
        return;
    }
    if (kButtonPin >= 0) {
        Serial.printf(
            "%.4f,%.4f,%.4f,%.2f,%.2f,%.2f,%u\n",
            ax_g, ay_g, az_g, gx_dps, gy_dps, gz_dps, g_button_stable);
    } else {
        Serial.printf("%.4f,%.4f,%.4f,%.2f,%.2f,%.2f\n", ax_g, ay_g, az_g, gx_dps, gy_dps, gz_dps);
    }
    g_csv_samples++;
}

static void print_status() {
    if (!g_imu_ready) {
        Serial.println("status imu=missing");
        return;
    }
    Serial.print("status imu=real samples=");
    Serial.print(g_csv_samples);
    Serial.print(" read_failures=");
    Serial.println(g_read_failures);
}

void setup() {
    pinMode(kStatusLed, OUTPUT);
    digitalWrite(kStatusLed, LOW);
    Serial.begin(kSerialBaud);
    delay(kBootSerialWaitMs);
    imu_begin();
    if (kButtonPin >= 0) {
        pinMode(kButtonPin, INPUT_PULLUP);
        Serial.print("Button enabled on GPIO ");
        Serial.print(kButtonPin);
        Serial.println(". Active low, 25 ms debounce. CSV has a 7th column.");
    } else {
        Serial.println("No button pin configured.");
    }
    digitalWrite(kStatusLed, g_imu_ready ? HIGH : LOW);
    uint32_t now_ms = millis();
    g_next_sample_ms = now_ms;
    g_next_report_ms = now_ms;
}

void loop() {
    uint32_t now_ms = millis();
    poll_button(now_ms);
    if (g_imu_ready && (int32_t)(now_ms - g_next_sample_ms) >= 0) {
        print_csv();
        g_next_sample_ms += kSamplePeriodMs;
        if ((int32_t)(now_ms - g_next_sample_ms) >= 0) {
            g_next_sample_ms = now_ms + kSamplePeriodMs;
        }
    }
    if ((int32_t)(now_ms - g_next_report_ms) >= 0) {
        print_status();
        g_next_report_ms += kReportPeriodMs;
        if ((int32_t)(now_ms - g_next_report_ms) >= 0) {
            g_next_report_ms = now_ms + kReportPeriodMs;
        }
    }
}
