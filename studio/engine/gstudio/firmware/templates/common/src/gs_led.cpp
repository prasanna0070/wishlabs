#include "gs_led.h"

#include "config.h"
#include "hardware.h"

#if GS_HAS_NEOPIXEL
#include <Adafruit_NeoPixel.h>
#endif

namespace gsled {

#if GS_HAS_NEOPIXEL
static Adafruit_NeoPixel s_strip(GS_LED_COUNT, GS_PIN_LED_DIN, NEO_GRB + NEO_KHZ800);
static gs::Rgb s_pixels[GS_LED_COUNT];
static bool s_dirty = true;
static bool s_started = false;
#endif
static uint8_t s_cap = GS_CFG_LED_CAP;

bool begin() {
#if GS_HAS_NEOPIXEL
  s_strip.begin();
  s_strip.clear();
  s_strip.show();
  s_started = true;
  return true;
#else
  return false;
#endif
}

bool present() {
#if GS_HAS_NEOPIXEL
  return s_started;
#else
  return false;
#endif
}

uint16_t count() {
#if GS_HAS_NEOPIXEL
  return GS_LED_COUNT;
#else
  return 0;
#endif
}

void set_cap(uint8_t cap) {
  if (cap > GS_CFG_LED_CAP) cap = GS_CFG_LED_CAP;  // never above the build's current budget
  s_cap = cap;
#if GS_HAS_NEOPIXEL
  s_dirty = true;
#endif
}

void set(uint16_t index, gs::Rgb c) {
#if GS_HAS_NEOPIXEL
  if (index >= GS_LED_COUNT) return;
  gs::Rgb& p = s_pixels[index];
  if (p.r != c.r || p.g != c.g || p.b != c.b) {
    p = c;
    s_dirty = true;
  }
#else
  (void)index;
  (void)c;
#endif
}

void set_all(gs::Rgb c) {
  for (uint16_t i = 0; i < count(); ++i) set(i, c);
}

void show() {
#if GS_HAS_NEOPIXEL
  if (!s_started || !s_dirty) return;
  for (uint16_t i = 0; i < GS_LED_COUNT; ++i) {
    const gs::Rgb c = gs::scale(s_pixels[i], s_cap);
    s_strip.setPixelColor(i, s_strip.Color(c.r, c.g, c.b));
  }
  s_strip.show();
  s_dirty = false;
#endif
}

}  // namespace gsled
