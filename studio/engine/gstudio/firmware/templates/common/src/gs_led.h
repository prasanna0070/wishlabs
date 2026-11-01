// WS2812B light (single LED, ring or strip) through the backplane's 3.3 V to 5 V level shifter.
#pragma once
#include <Arduino.h>

#include "gs_light.h"

namespace gsled {

bool begin();
bool present();
uint16_t count();
// Global brightness cap 0..255, applied to every colour (keeps the 5 V current in budget).
void set_cap(uint8_t cap);
void set(uint16_t index, gs::Rgb c);
void set_all(gs::Rgb c);
void show();  // only sends when something changed

}  // namespace gsled
