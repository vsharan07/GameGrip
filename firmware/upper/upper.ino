#include <WiFi.h>
#include <WiFiUdp.h>

#include "battery.h"
#include "config.h"
#include "imu_interface.h"
#include "packet.h"

static const uint32_t kWifiTimeoutMs = 15000;
static const uint32_t kSendPeriodMs = 20;
static const uint32_t kReportPeriodMs = 1000;
static const uint32_t kLedBlinkMs = 200;
static const uint32_t kBootSerialWaitMs = 200;

// ESP32 Dev Module does not define LED_BUILTIN. DevKit boards wire it to GPIO 2.
#if defined(LED_BUILTIN)
static const int kStatusLed = LED_BUILTIN;
#else
static const int kStatusLed = 2;
#endif

static WiFiUDP g_udp;
static uint32_t g_seq = 0;
static uint32_t g_last_sent_seq = 0;
static uint32_t g_next_send_ms = 0;
static uint32_t g_next_report_ms = 0;

static void set_led(bool on) {
    digitalWrite(kStatusLed, on ? HIGH : LOW);
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
    packet.btn0 = 0;
    packet.btn1 = 0;
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
    Serial.begin(115200);
    delay(kBootSerialWaitMs);
    imu_begin();
    battery_begin();
    connect_wifi();
    uint32_t now_ms = millis();
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
