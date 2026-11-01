// Debounced button with tap / long-press detection. Pure logic: feed it the raw level and the time.
#pragma once
#include <stdint.h>

namespace gs {

enum class ButtonEvent : uint8_t {
  None = 0,
  Press,        // debounced press edge (fires first, before Tap or LongStart)
  Tap,          // released before long_press_ms
  LongStart,    // still held at long_press_ms (fires once per hold)
  LongRelease,  // released after LongStart
};

const char* button_event_name(ButtonEvent e);

struct ButtonConfig {
  uint16_t debounce_ms = 30;     // level must be stable this long to count
  uint16_t long_press_ms = 600;  // hold time that turns a press into a long press
};

class ButtonFsm {
 public:
  explicit ButtonFsm(ButtonConfig cfg = ButtonConfig()) : cfg_(cfg) {}

  // Call every few milliseconds with the raw (already polarity-corrected) level.
  // Returns at most one event per call. Press always precedes Tap/LongStart/LongRelease.
  ButtonEvent update(uint32_t now_ms, bool raw_pressed);

  bool pressed() const { return stable_ && !boot_held_; }
  // True while a press that began before the first update() is still held.
  bool held_since_boot() const { return boot_held_; }
  // How long the current press has lasted (0 when released).
  uint32_t held_ms(uint32_t now_ms) const { return stable_ ? now_ms - pressed_at_ : 0; }
  void set_config(ButtonConfig cfg) { cfg_ = cfg; }
  const ButtonConfig& config() const { return cfg_; }

 private:
  ButtonConfig cfg_;
  bool stable_ = false;
  bool raw_ = false;
  bool started_ = false;
  bool boot_held_ = false;
  uint32_t raw_since_ = 0;
  uint32_t pressed_at_ = 0;
  bool long_fired_ = false;
};

}  // namespace gs
