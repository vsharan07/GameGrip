#include "battery.h"

#include "config.h"

#include <Arduino.h>

#if BATTERY_ENABLED
// GPIO 34 is ADC1_CH6. ADC2 pins are shared with Wi-Fi, so the divider stays here.
static const int kBatteryPin = 34;
static const uint8_t kAdcBits = 12;
static const float kAdcFullScaleCounts = 4095.0f;
// Assumed pin voltage at full scale with ADC_11db attenuation.
static const float kAdcFullScaleVolts = 3.3f;
// Vcell = Vpin * (R_top + R_bottom) / R_bottom. Two equal resistors give 2.
static const float kBatteryDividerRatio = 2.0f;
static const float kLowBatteryVolts = 3.4f;
static const uint32_t kBatteryPeriodMs = 1000;

static float g_volts = 0.0f;
static bool g_low = false;
static uint32_t g_next_ms = 0;

static void sample_battery(uint32_t now_ms) {
    int counts = analogRead(kBatteryPin);
    float pin_volts = ((float)counts / kAdcFullScaleCounts) * kAdcFullScaleVolts;
    g_volts = pin_volts * kBatteryDividerRatio;
    g_low = g_volts < kLowBatteryVolts;
    g_next_ms = now_ms + kBatteryPeriodMs;
}
#endif

void battery_begin() {
#if BATTERY_ENABLED
    pinMode(kBatteryPin, INPUT);
    analogReadResolution(kAdcBits);
    analogSetPinAttenuation(kBatteryPin, ADC_11db);
    sample_battery(millis());
#endif
}

void battery_poll(uint32_t now_ms) {
#if BATTERY_ENABLED
    if ((int32_t)(now_ms - g_next_ms) >= 0) {
        sample_battery(now_ms);
    }
#else
    (void)now_ms;
#endif
}

bool battery_enabled() {
    return BATTERY_ENABLED != 0;
}

bool battery_is_low() {
#if BATTERY_ENABLED
    return g_low;
#else
    return false;
#endif
}

float battery_volts() {
#if BATTERY_ENABLED
    return g_volts;
#else
    return 0.0f;
#endif
}
