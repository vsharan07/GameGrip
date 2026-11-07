#include <WiFi.h>
#include <WiFiUdp.h>

#include "battery.h"
#include "config.h"
#include "fusion.h"
#include "imu_interface.h"
#include "packet.h"

static const uint32_t kWifiTimeoutMs = 15000;
static const uint32_t kSendPeriodMs = 20;
static const uint32_t kReportPeriodMs = 1000;
static const uint32_t kLedBlinkMs = 200;
static const uint32_t kBootSerialWaitMs = 200;
static const uint32_t kDebounceMs = 25;
static const uint32_t kLongPressMs = 1500;
static const int kBtn0Pin = 25;
static const int kBtn1Pin = 26;

// ESP32 Dev Module does not define LED_BUILTIN. DevKit boards wire it to GPIO 2.
#if defined(LED_BUILTIN)
static const int kStatusLed = LED_BUILTIN;
#else
static const int kStatusLed = 2;
#endif

struct DebouncedButton {
    int pin;
    uint8_t stable;
    uint8_t candidate;
    uint32_t since_ms;
};

static WiFiUDP g_udp;
static uint32_t g_seq = 0;
static uint32_t g_last_sent_seq = 0;
static uint32_t g_next_send_ms = 0;
static uint32_t g_next_report_ms = 0;
static DebouncedButton g_btn0 = {kBtn0Pin, 0, 0, 0};
static DebouncedButton g_btn1 = {kBtn1Pin, 0, 0, 0};
static uint8_t g_btn1_was_down = 0;
static uint8_t g_reset_sent = 0;
static uint32_t g_btn1_held_since_ms = 0;

static void set_led(bool on) {
    digitalWrite(kStatusLed, on ? HIGH : LOW);
}

// INPUT_PULLUP holds an open pin HIGH. Invert that so 1 means pressed and an
// unconnected button stays 0.
static uint8_t raw_pressed(int pin) {
    return digitalRead(pin) == LOW ? 1 : 0;
}

static uint8_t debounce_button(DebouncedButton *button, uint32_t now_ms) {
    uint8_t raw = raw_pressed(button->pin);
    if (raw != button->candidate) {
        button->candidate = raw;
        button->since_ms = now_ms;
    } else if ((uint32_t)(now_ms - button->since_ms) >= kDebounceMs) {
        button->stable = raw;
    }
    return button->stable;
}

static void poll_buttons(uint32_t now_ms) {
    debounce_button(&g_btn0, now_ms);
    debounce_button(&g_btn1, now_ms);
    if (g_btn1.stable) {
        if (!g_btn1_was_down) {
            g_btn1_was_down = 1;
            g_btn1_held_since_ms = now_ms;
            g_reset_sent = 0;
        } else if (!g_reset_sent && (uint32_t)(now_ms - g_btn1_held_since_ms) >= kLongPressMs) {
            reset_orientation();
            g_reset_sent = 1;
            Serial.println("orientation reset");
        }
    } else {
        g_btn1_was_down = 0;
        g_reset_sent = 0;
    }
}

static void connect_wifi() {
    // Retries until the association succeeds. g_seq is left alone so a
    // drop does not look like a reboot to the host.
    while (WiFi.status() != WL_CONNECTED) {
        WiFi.mode(WIFI_STA);
        WiFi.persistent(false);
        WiFi.disconnect(false, false);
        WiFi.begin(WIFI_SSID, WIFI_PASS);
        uint32_t start = millis();
        while ((millis() - start) < kWifiTimeoutMs) {
            if (WiFi.status() == WL_CONNECTED) {
                break;
            }
            set_led(!digitalRead(kStatusLed));
            delay(kLedBlinkMs);
        }
    }
    set_led(true);
    g_udp.stop();
    g_udp.begin(UDP_PORT);
}

static void send_sample(uint32_t now_ms) {
    ImuSample sample;
    if (!read_imu(&sample)) {
        return;
    }

    GGPacket packet;
    packet.magic = GG_MAGIC;
    packet.ver = GG_VERSION;
    packet.module_id = MODULE_ID;
    packet.seq = g_seq;
    packet.t_ms = now_ms;
    packet.qw = sample.qw;
    packet.qx = sample.qx;
    packet.qy = sample.qy;
    packet.qz = sample.qz;
    packet.gyro_mag = sample.gyro_mag;
    packet.accel_mag = sample.accel_mag;
    packet.btn0 = g_btn0.stable;
    packet.btn1 = g_btn1.stable;
    packet.flags = 0;
    if (!imu_using_real()) {
        packet.flags = (uint8_t)(packet.flags | GG_FLAG_FAKE_IMU);
    }
    if (sample.still) {
        packet.flags = (uint8_t)(packet.flags | GG_FLAG_STILL);
    }
    if (imu_fusion_ok()) {
        packet.flags = (uint8_t)(packet.flags | GG_FLAG_FUSION_OK);
    }
    if (battery_is_low()) {
        packet.flags = (uint8_t)(packet.flags | GG_FLAG_LOW_BATT);
    }
    packet.rssi = 0;

    uint8_t raw[GG_PACKET_SIZE];
    gg_serialize(&packet, raw);
    if (!g_udp.beginPacket(HOST_IP, UDP_PORT)) {
        return;
    }
    g_udp.write(raw, GG_PACKET_SIZE);
    if (!g_udp.endPacket()) {
        return;
    }
    g_last_sent_seq = g_seq;
    g_seq++;
}

static void print_report() {
    Serial.print("sending seq=");
    Serial.print(g_last_sent_seq);
    Serial.print(" imu=");
    Serial.print(imu_using_real() ? "real" : "fake");
    Serial.print(" fusion=");
    Serial.print(imu_fusion_ok() ? "ok" : "fail");
    Serial.print(" batt=");
    if (battery_enabled()) {
        Serial.println(battery_volts(), 2);
    } else {
        Serial.println("off");
    }
}

void setup() {
    pinMode(kStatusLed, OUTPUT);
    set_led(false);
    pinMode(kBtn0Pin, INPUT_PULLUP);
    pinMode(kBtn1Pin, INPUT_PULLUP);
    Serial.begin(115200);
    delay(kBootSerialWaitMs);
    imu_begin();
    battery_begin();
    connect_wifi();
    uint32_t now_ms = millis();
    g_btn0.since_ms = now_ms;
    g_btn1.since_ms = now_ms;
    g_next_send_ms = now_ms;
    g_next_report_ms = now_ms;
}

void loop() {
    if (WiFi.status() != WL_CONNECTED) {
        set_led(false);
        connect_wifi();
    }
    set_led(true);

    uint32_t now_ms = millis();
    poll_buttons(now_ms);
    battery_poll(now_ms);
    if ((int32_t)(now_ms - g_next_send_ms) >= 0) {
        send_sample(now_ms);
        g_next_send_ms += kSendPeriodMs;
        if ((int32_t)(now_ms - g_next_send_ms) >= 0) {
            g_next_send_ms = now_ms + kSendPeriodMs;
        }
    }
    if ((int32_t)(now_ms - g_next_report_ms) >= 0) {
        print_report();
        g_next_report_ms += kReportPeriodMs;
        if ((int32_t)(now_ms - g_next_report_ms) >= 0) {
            g_next_report_ms = now_ms + kReportPeriodMs;
        }
    }
}
