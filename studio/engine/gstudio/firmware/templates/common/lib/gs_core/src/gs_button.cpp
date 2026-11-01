#include "gs_button.h"

namespace gs {

const char* button_event_name(ButtonEvent e) {
  switch (e) {
    case ButtonEvent::Press: return "press";
    case ButtonEvent::Tap: return "tap";
    case ButtonEvent::LongStart: return "long_start";
    case ButtonEvent::LongRelease: return "long_release";
    default: return "none";
  }
}

ButtonEvent ButtonFsm::update(uint32_t now, bool raw) {
  if (!started_) {
    // First sample: adopt the level without an edge. A button held at power-up is ignored until
    // it is released (so holding it to enter setup mode never starts a story).
    started_ = true;
    raw_ = raw;
    stable_ = raw;
    boot_held_ = raw;
    raw_since_ = now;
    return ButtonEvent::None;
  }
  if (raw != raw_) {
    raw_ = raw;
    raw_since_ = now;
  }
  const bool settled = static_cast<uint32_t>(now - raw_since_) >= cfg_.debounce_ms;
  if (boot_held_) {
    if (settled && !raw_) {
      boot_held_ = false;
      stable_ = false;
    }
    return ButtonEvent::None;
  }
  if (settled && raw_ != stable_) {
    stable_ = raw_;
    if (stable_) {
      pressed_at_ = raw_since_;  // the press began when the level first changed
      long_fired_ = false;
      return ButtonEvent::Press;
    }
    const bool was_long = long_fired_;
    long_fired_ = false;
    return was_long ? ButtonEvent::LongRelease : ButtonEvent::Tap;
  }
  if (stable_ && !long_fired_ && static_cast<uint32_t>(now - pressed_at_) >= cfg_.long_press_ms) {
    long_fired_ = true;
    return ButtonEvent::LongStart;
  }
  return ButtonEvent::None;
}

}  // namespace gs
