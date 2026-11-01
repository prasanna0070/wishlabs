#include "gs_listen.h"

#include <Arduino.h>

#include "gs_display.h"
#include "gs_led.h"
#include "gs_light.h"

static volatile bool s_on = false;
static volatile bool s_pending = false;
static void* s_ui_task = nullptr;
static bool (*s_override)(bool) = nullptr;

static void show(bool on) {
  if (s_override && s_override(on)) return;
  const gs::LightPalette pal;
  gsled::set_all(on ? gs::scale(pal.listening, pal.indicator_level) : gs::Rgb());
  gsled::show();
  if (gsdisp::present()) gsdisp::draw_face(on ? gs::Expression::Listening : gs::Expression::Neutral, millis(), gsdisp::kWhite);
}

void gs_listening_indicator(bool on) {
  s_on = on;
  if (static_cast<void*>(xTaskGetCurrentTaskHandle()) == s_ui_task) {
    s_pending = false;
    show(on);
  } else {
    s_pending = true;
  }
}

namespace gslisten {

void set_ui_task(void* task) { s_ui_task = task; }
void set_override(bool (*fn)(bool on)) { s_override = fn; }
bool active() { return s_on; }

void apply(uint32_t now) {
  (void)now;
  if (!s_pending) return;
  s_pending = false;
  show(s_on);
}

}  // namespace gslisten
