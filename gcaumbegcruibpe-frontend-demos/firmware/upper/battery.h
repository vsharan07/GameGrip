#ifndef GAMEGRIP_BATTERY_H
#define GAMEGRIP_BATTERY_H

#include <stdint.h>

void battery_begin();
void battery_poll(uint32_t now_ms);
bool battery_enabled();
bool battery_is_low();
float battery_volts();

#endif
