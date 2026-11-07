/*
 * GameGrip sample packet. 40 bytes, little-endian, no padding.
 * Must match host/gg_packet.py.
 *
 * gg_serialize writes every field one byte at a time. Do not memcpy
 * this struct onto the wire: alignment padding is not part of the format.
 */

#ifndef GAMEGRIP_PACKET_H
#define GAMEGRIP_PACKET_H

#include <stdint.h>

#define GG_PACKET_SIZE 40
#define GG_MAGIC 0x4747
#define GG_VERSION 1

#define GG_MODULE_UPPER 0
#define GG_MODULE_LOWER 1

#define GG_FLAG_FAKE_IMU (1u << 0)
#define GG_FLAG_STILL (1u << 1)
#define GG_FLAG_FUSION_OK (1u << 2)
#define GG_FLAG_LOW_BATT (1u << 3)

typedef struct {
    uint16_t magic;
    uint8_t ver;
    uint8_t module_id;
    uint32_t seq;
    uint32_t t_ms;
    float qw;
    float qx;
    float qy;
    float qz;
    float gyro_mag;
    float accel_mag;
    uint8_t btn0;
    uint8_t btn1;
    uint8_t flags;
    int8_t rssi;
} GGPacket;

static inline void gg_write_u16_le(uint8_t *out, uint16_t value) {
    out[0] = (uint8_t)(value & 0xFFu);
    out[1] = (uint8_t)((value >> 8) & 0xFFu);
}

static inline void gg_write_u32_le(uint8_t *out, uint32_t value) {
    out[0] = (uint8_t)(value & 0xFFu);
    out[1] = (uint8_t)((value >> 8) & 0xFFu);
    out[2] = (uint8_t)((value >> 16) & 0xFFu);
    out[3] = (uint8_t)((value >> 24) & 0xFFu);
}

static inline void gg_write_f32_le(uint8_t *out, float value) {
    union {
        float f;
        uint32_t u;
    } bits;
    bits.f = value;
    gg_write_u32_le(out, bits.u);
}

/* Write p into out[0..39] in the host/gg_packet.py field order. */
static inline void gg_serialize(const GGPacket *p, uint8_t out[40]) {
    gg_write_u16_le(out + 0, p->magic);
    out[2] = p->ver;
    out[3] = p->module_id;
    gg_write_u32_le(out + 4, p->seq);
    gg_write_u32_le(out + 8, p->t_ms);
    gg_write_f32_le(out + 12, p->qw);
    gg_write_f32_le(out + 16, p->qx);
    gg_write_f32_le(out + 20, p->qy);
    gg_write_f32_le(out + 24, p->qz);
    gg_write_f32_le(out + 28, p->gyro_mag);
    gg_write_f32_le(out + 32, p->accel_mag);
    out[36] = p->btn0;
    out[37] = p->btn1;
    out[38] = p->flags;
    out[39] = (uint8_t)p->rssi;
}

#endif
