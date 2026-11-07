# GameGrip packet

One sample is **40 bytes**, little-endian, with no padding between fields. The Python format string is `<HBBII4fffBBBb`. `firmware/upper/packet.h` and `host/gg_packet.py` must stay in lockstep.

| Offset | Name | Type | Meaning |
| ---: | --- | --- | --- |
| 0 | magic | u16 | Constant `0x4747` |
| 2 | ver | u8 | Protocol version, currently `1` |
| 3 | module_id | u8 | `0` = upper arm, `1` = lower arm |
| 4 | seq | u32 | Per-packet counter on the sender; increments and wraps |
| 8 | t_ms | u32 | Milliseconds on the sending device |
| 12 | qw | f32 | Quaternion w, unit quaternion in the device frame |
| 16 | qx | f32 | Quaternion x, unit quaternion in the device frame |
| 20 | qy | f32 | Quaternion y, unit quaternion in the device frame |
| 24 | qz | f32 | Quaternion z, unit quaternion in the device frame |
| 28 | gyro_mag | f32 | Magnitude of angular velocity, rad/s |
| 32 | accel_mag | f32 | Acceleration magnitude, m/s² |
| 36 | btn0 | u8 | Button 0, `0` or `1` |
| 37 | btn1 | u8 | Button 1, `0` or `1` |
| 38 | flags | u8 | bit0 `FAKE_IMU`, bit1 `STILL`, bit2 `FUSION_OK`, bit3 `LOW_BATT` |
| 39 | rssi | i8 | Signal strength; `0` if unused |

`LOW_BATT` is flags bit3 (`0x08`). The sender sets it when `BATTERY_ENABLED` is 1 and the cell is below 3.4 V. The bit stays 0 when the battery reading is off, or when the cell is at or above 3.4 V. Older packets leave the bit clear. The packet is still 40 bytes, and the other fields are unchanged. The host reads it with `low_batt(flags)` in `host/gg_packet.py`.
