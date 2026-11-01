// Fakes for the device modules a native suite does not compile for real (LED strip, display,
// OTA flash, I2C sensors), recording what the firmware asked them to do, plus helpers that drive the
// real setup() / loop() from src/main.cpp. Include in exactly ONE file of a test suite.
#pragma once
#include <string>
#include <vector>

#include "Arduino.h"
#include "gs_display.h"
#include "gs_led.h"
#include "gs_ota.h"
#include "gs_sensors.h"

namespace gs_host {

struct UiLog {
  gs::Rgb led;                         // the colour last shown on every pixel
  std::vector<std::string> events;    // "led:r,g,b" on each show(), "face:<expression>" on each draw
  std::vector<std::string> texts;     // every text drawn on the display
  int listening_shows = 0;            // show() calls while the pixels were listening-blue
};
inline UiLog ui;
inline bool display_present = true;

inline bool is_listening_blue(const gs::Rgb& c) { return c.b > 0 && c.b > c.r && c.b > c.g; }

}  // namespace gs_host

void setup();
void loop();

namespace gsled {
static gs::Rgb s_px;
bool begin() { return true; }
bool present() { return true; }
uint16_t count() { return 1; }
void set_cap(uint8_t) {}
void set(uint16_t, gs::Rgb c) { s_px = c; }
void set_all(gs::Rgb c) { s_px = c; }
void show() {
  if (s_px.r != gs_host::ui.led.r || s_px.g != gs_host::ui.led.g || s_px.b != gs_host::ui.led.b || gs_host::ui.events.empty())
    gs_host::ui.events.push_back("led:" + std::to_string(s_px.r) + "," + std::to_string(s_px.g) + "," + std::to_string(s_px.b));
  gs_host::ui.led = s_px;
  if (gs_host::is_listening_blue(s_px)) ++gs_host::ui.listening_shows;
}
}  // namespace gsled

namespace gsdisp {
bool begin() { return gs_host::display_present; }
bool present() { return gs_host::display_present; }
int width() { return 128; }
int height() { return 64; }
bool is_color() { return false; }
uint16_t rgb(uint8_t r, uint8_t g, uint8_t b) { return uint16_t((r | g | b) ? 0xFFFF : 0); }
void clear() {}
void fill_rect(int, int, int, int, uint16_t) {}
void fill_round_rect(int, int, int, int, int, uint16_t) {}
void fill_circle(int, int, int, uint16_t) {}
void fill_triangle(int, int, int, int, int, int, uint16_t) {}
void line(int, int, int, int, uint16_t) {}
void text(int, int, const char* s, uint8_t, uint16_t) { gs_host::ui.texts.push_back(s ? s : ""); }
int text_width(const char* s, uint8_t size) { return int(strlen(s)) * 6 * size; }
void flush() {}
void set_brightness(uint8_t) {}
void power(bool) {}
void draw_face(gs::Expression e, uint32_t, uint16_t) { gs_host::ui.events.push_back(std::string("face:") + gs::expression_name(e)); }
}  // namespace gsdisp

namespace gsota {
void begin() {}
void loop(uint32_t, bool) {}
bool pending_verify() { return false; }
const char* state_name() { return "valid"; }
const char* running_partition() { return "app0"; }
bool signing_enabled() { return false; }
bool upload_begin(const char*, String* error) {
  if (error) *error = "This build has no update signing key.";
  return false;
}
bool upload_write(const uint8_t*, size_t, String*) { return false; }
bool upload_end(String*) { return false; }
void upload_abort() {}
uint32_t upload_bytes() { return 0; }
}  // namespace gsota

namespace gssensors {
bool begin() { return true; }
int sensor_count() { return 0; }
int read_all(Reading*, int) { return 0; }
bool temperature_c(float*) { return false; }
bool humidity_pct(float*) { return false; }
bool pressure_hpa(float*) { return false; }
bool light_lux(float*) { return false; }
bool distance_mm(float*) { return false; }
bool has_rtc() { return false; }
bool rtc_now(time_t*) { return false; }
bool rtc_set(time_t) { return false; }
}  // namespace gssensors

namespace gs_host {

// Runs the firmware's loop() for ms milliseconds (each pass advances the clock by its delay(2)).
inline void run_for(uint32_t ms) {
  const uint32_t end = now_ms + ms;
  while (int32_t(now_ms - end) < 0) ::loop();
}

// Drains the audio task (plays whatever is queued) and lets loop() see the result.
inline void play_queued() {
  pump_task("gs_audio");
  run_for(10);
}

// Holds the first button for ms, then releases it, running the firmware all the while.
inline void press_button(int pin, uint32_t ms) {
  gpio_in[pin] = LOW;
  run_for(ms);
  gpio_in[pin] = HIGH;
  run_for(80);
}

// A fresh power-up: RAM state goes; card, NVS and Preferences stay. hold_ms > 0 holds the first
// button for that long while plugging in.
inline void power_up(int button_pin, uint32_t hold_ms = 0) {
  reboot_volatile();
  ui = UiLog();
  if (hold_ms) {
    gpio_fn = [button_pin, hold_ms](int pin, uint32_t now) { return (pin == button_pin && now < hold_ms) ? LOW : HIGH; };
  }
  ::setup();
  gpio_fn = nullptr;
  gpio_in[button_pin] = HIGH;
  play_queued();  // the start-up sound
}

}  // namespace gs_host
