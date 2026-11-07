#include <WiFi.h>
#include <WiFiUdp.h>
#include <Wire.h>

// --- Configuration Constants ---
#define WIFI_SSID "Your_SSID"      // Replace with your WiFi network name
#define WIFI_PASS "Your_Password"  // Replace with your WiFi password
#define HOST_IP   "192.168.1.100"  // Replace with your laptop/receiver IP address
#define UDP_PORT  8888             // UDP port to stream data to

// MPU6050 I2C Address (0x68 is standard, 0x69 if AD0 is pulled high)
const uint8_t MPU6050_ADDR = 0x68;

// Protocol Magic & Constants
#define GG_MAGIC 0x4747
#define GG_VERSION 1
#define MODULE_ID 1
#define GG_PACKET_SIZE 44

#define GG_FLAG_FAKE_IMU   (1 << 0)
#define GG_FLAG_STILL      (1 << 1)
#define GG_FLAG_FUSION_OK  (1 << 2)

// Packet Structure
struct GGPacket {
    uint16_t magic;
    uint8_t ver;
    uint8_t module_id;
    uint32_t seq;
    uint32_t t_ms;
    float qw, qx, qy, qz;
    float gyro_mag;
    float accel_mag;
    uint8_t btn0;
    uint8_t btn1;
    uint8_t flags;
    int8_t rssi;
};

// Internal sample structure
struct ImuSample {
    float qw, qx, qy, qz;
    float gyro_mag;
    float accel_mag;
    bool still;
    float gx_dps, gy_dps, gz_dps;  // gyro per axis, degrees/second
};

// Streams "ax,ay,az,gx,gy,gz" (g and deg/s) every sample for the Python
// swing detector. The 500 ms human-readable report has no commas, so the
// Python script ignores it.
static const bool kStreamCsv = true;

static const uint32_t kWifiTimeoutMs = 5000; // Shorter timeout so it doesn't freeze long on boot
static const uint32_t kSendPeriodMs = 20;
static const uint32_t kReportPeriodMs = 500;   // Set to 500ms as requested
static const uint32_t kLedBlinkMs = 200;
static const uint32_t kBootSerialWaitMs = 200;

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
static bool g_imu_initialized = false;
static bool g_wifi_connected = false;

static void set_led(bool on) {
    digitalWrite(kStatusLed, on ? HIGH : LOW);
}

// Sensor scale factors - must match the ranges set in imu_begin()
static const float kAccelLsbPerG = 4096.0f;   // +-8 g
static const float kGyroLsbPerDps = 16.4f;    // +-2000 deg/s

static void mpu_write(uint8_t reg, uint8_t value) {
    Wire.beginTransmission(MPU6050_ADDR);
    Wire.write(reg);
    Wire.write(value);
    Wire.endTransmission();
}

// Initialize MPU6050 over I2C
void imu_begin() {
    Wire.begin(21, 22); // Standard ESP32 SDA=21, SCL=22
    Wire.beginTransmission(MPU6050_ADDR);
    Wire.write(0x6B); // PWR_MGMT_1 register
    Wire.write(0x00); // Wake up MPU6050
    if (Wire.endTransmission() == 0) {
        g_imu_initialized = true;
        // Default ranges (+-250 deg/s, +-2 g) max out during fast swings.
        mpu_write(0x1B, 0x18);  // GYRO_CONFIG:  +-2000 deg/s -> 16.4 LSB per deg/s
        mpu_write(0x1C, 0x10);  // ACCEL_CONFIG: +-8 g        -> 4096 LSB per g
        Serial.println("[INFO] MPU6050 initialized successfully (gyro +-2000 dps, accel +-8 g).");
    } else {
        Serial.println("[WARNING] MPU6050 not found! Running with fake data fallback.");
        g_imu_initialized = false;
    }
}

// Read raw sensor data or fall back gracefully
bool read_imu(ImuSample *sample) {
    if (!g_imu_initialized) {
        sample->qw = 1.0f; sample->qx = 0.0f; sample->qy = 0.0f; sample->qz = 0.0f;
        sample->gyro_mag = 0.0f;
        sample->accel_mag = 9.81f;
        sample->still = true;
        return true;
    }

    // Request accelerometer and gyro data from MPU6050
    Wire.beginTransmission(MPU6050_ADDR);
    Wire.write(0x3B); // Starting register for ACCEL_XOUT_H
    Wire.endTransmission(false);
    Wire.requestFrom(MPU6050_ADDR, (uint8_t)14, (uint8_t)true);

    if (Wire.available() < 14) return false;

    // Read bytes into an array first: in "Wire.read() << 8 | Wire.read()"
    // C++ doesn't guarantee which read() runs first, so high/low can swap.
    uint8_t b[14];
    for (int i = 0; i < 14; i++) b[i] = Wire.read();
    int16_t ax = (int16_t)((b[0] << 8) | b[1]);
    int16_t ay = (int16_t)((b[2] << 8) | b[3]);
    int16_t az = (int16_t)((b[4] << 8) | b[5]);
    // b[6..7] = temperature (skipped)
    int16_t gx = (int16_t)((b[8] << 8) | b[9]);
    int16_t gy = (int16_t)((b[10] << 8) | b[11]);
    int16_t gz = (int16_t)((b[12] << 8) | b[13]);

    sample->gx_dps = gx / kGyroLsbPerDps;
    sample->gy_dps = gy / kGyroLsbPerDps;
    sample->gz_dps = gz / kGyroLsbPerDps;

    // Convert raw values to basic metrics
    sample->qw = 1.0f; 
    sample->qx = (float)ax / kAccelLsbPerG;
    sample->qy = (float)ay / kAccelLsbPerG;
    sample->qz = (float)az / kAccelLsbPerG;
    sample->accel_mag = sqrt(sample->qx*sample->qx + sample->qy*sample->qy + sample->qz*sample->qz);
    sample->gyro_mag = sqrtf(sample->gx_dps*sample->gx_dps + sample->gy_dps*sample->gy_dps + sample->gz_dps*sample->gz_dps);
    sample->still = (sample->gyro_mag < 0.1f);

    return true;
}

bool imu_using_real() { return g_imu_initialized; }
bool imu_fusion_ok() { return g_imu_initialized; }

// Serialize packet structure into raw bytes for UDP transmission
void gg_serialize(const GGPacket *pkt, uint8_t *dst) {
    memcpy(dst + 0, &pkt->magic, 2);
    memcpy(dst + 2, &pkt->ver, 1);
    memcpy(dst + 3, &pkt->module_id, 1);
    memcpy(dst + 4, &pkt->seq, 4);
    memcpy(dst + 8, &pkt->t_ms, 4);
    memcpy(dst + 12, &pkt->qw, 4);
    memcpy(dst + 16, &pkt->qx, 4);
    memcpy(dst + 20, &pkt->qy, 4);
    memcpy(dst + 24, &pkt->qz, 4);
    memcpy(dst + 28, &pkt->gyro_mag, 4);
    memcpy(dst + 32, &pkt->accel_mag, 4);
    memcpy(dst + 36, &pkt->btn0, 1);
    memcpy(dst + 37, &pkt->btn1, 1);
    memcpy(dst + 38, &pkt->flags, 1);
    memcpy(dst + 39, &pkt->rssi, 1);
}

static void try_connect_wifi() {
    WiFi.mode(WIFI_STA);
    WiFi.persistent(false);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    
    uint32_t start = millis();
    while ((millis() - start) < kWifiTimeoutMs) {
        if (WiFi.status() == WL_CONNECTED) {
            g_wifi_connected = true;
            set_led(true);
            g_udp.stop();
            g_udp.begin(UDP_PORT);
            Serial.println("[INFO] WiFi connected successfully.");
            return;
        }
        set_led(!digitalRead(kStatusLed));
        delay(kLedBlinkMs);
    }
    
    g_wifi_connected = false;
    set_led(false);
    Serial.println("[WARNING] WiFi connection failed/absent. Running in Serial Debug Mode.");
}

static void send_sample(uint32_t now_ms) {
    ImuSample sample;
    if (!read_imu(&sample)) return;

    if (kStreamCsv && imu_using_real()) {
        Serial.printf("%.4f,%.4f,%.4f,%.2f,%.2f,%.2f\n",
                      sample.qx, sample.qy, sample.qz,
                      sample.gx_dps, sample.gy_dps, sample.gz_dps);
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
        packet.flags |= GG_FLAG_FAKE_IMU;
    }
    if (sample.still) {
        packet.flags |= GG_FLAG_STILL;
    }
    if (imu_fusion_ok()) {
        packet.flags |= GG_FLAG_FUSION_OK;
    }
    packet.rssi = g_wifi_connected ? (int8_t)WiFi.RSSI() : 0;

    // Only send over UDP if WiFi is active
    if (g_wifi_connected) {
        uint8_t raw[GG_PACKET_SIZE];
        gg_serialize(&packet, raw);
        
        if (g_udp.beginPacket(HOST_IP, UDP_PORT)) {
            g_udp.write(raw, GG_PACKET_SIZE);
            g_udp.endPacket();
        }
    }

    g_last_sent_seq = g_seq;
    g_seq++;
}

static void print_report(uint32_t now_ms) {
    ImuSample sample;
    read_imu(&sample);

    if (!g_wifi_connected) {
        // Print raw IMU data to console every 500ms when WiFi is down
        Serial.printf("[NO WIFI] t=%lums | Accel X: %.2f Y: %.2f Z: %.2f (Mag: %.2f) | Gyro Mag: %.2f | Still: %s\n",
                      now_ms, sample.qx, sample.qy, sample.qz, sample.accel_mag, sample.gyro_mag, sample.still ? "YES" : "NO");
    } else {
        // Standard status report when WiFi is active
        Serial.print("sending seq=");
        Serial.print(g_last_sent_seq);
        Serial.print(" imu=");
        Serial.print(imu_using_real() ? "real" : "fake");
        Serial.print(" fusion=");
        Serial.println(imu_fusion_ok() ? "ok" : "fail");
    }
}

void setup() {
    pinMode(kStatusLed, OUTPUT);
    set_led(false);
    Serial.begin(115200);
    delay(kBootSerialWaitMs);
    
    imu_begin();
    try_connect_wifi();
    
    uint32_t now_ms = millis();
    g_next_send_ms = now_ms;
    g_next_report_ms = now_ms;
}

void loop() {
    // Periodically check WiFi status if it dropped
    if (WiFi.status() != WL_CONNECTED && g_wifi_connected) {
        g_wifi_connected = false;
        set_led(false);
        Serial.println("[WARNING] WiFi lost!");
    }

    uint32_t now_ms = millis();
    
    if ((int32_t)(now_ms - g_next_send_ms) >= 0) {
        send_sample(now_ms);
        g_next_send_ms += kSendPeriodMs;
    }
    
    if ((int32_t)(now_ms - g_next_report_ms) >= 0) {
        print_report(now_ms);
        g_next_report_ms += kReportPeriodMs;
    }
}