// Light logic: the night-light timer with gentle fades, and the indicator colours.
#pragma once
#include <stdint.h>

#include "gs_story_ctl.h"

namespace gs {

struct Rgb {
  uint8_t r = 0, g = 0, b = 0;
};

bool parse_hex_color(const char* s, Rgb* out);  // "#FFB36B" or "FFB36B"
Rgb scale(Rgb c, uint8_t level);                 // level 0..255

// Fade in, hold for the timer, fade out. Restarting while lit does not flash.
class NightLight {
 public:
  static constexpr uint32_t kFadeInMs = 1500;
  static constexpr uint32_t kFadeOutMs = 30000;

  void start(uint32_t now, uint32_t duration_ms);
  void stop(uint32_t now);  // begins the fade-out now
  void off() { active_ = false; }
  bool active(uint32_t now) const;
  uint32_t remaining_ms(uint32_t now) const;
  // 0..255 brightness factor at time now.
  uint8_t level(uint32_t now) const;

 private:
  bool active_ = false;
  uint32_t start_ = 0;
  uint32_t fade_out_at_ = 0;
  uint8_t start_level_ = 0;
};

struct LightPalette {
  Rgb night{255, 179, 107};  // warm amber glow
  Rgb listening{0, 70, 255}; // blue
  Rgb keep{40, 255, 90};     // green
  Rgb saved{40, 255, 90};
  Rgb discarded{255, 140, 30};
  Rgb error{255, 20, 10};
  uint8_t night_level = 31;      // 0..255: 12 % of full scale by default
  uint8_t indicator_level = 110; // 0..255: indicators stay clearly visible
};

// Colour of the light at time now for a story_player indicator, blended with the night glow.
// Listening is always solid blue at indicator brightness, whatever the night-light setting.
Rgb indicator_color(Indicator ind, uint32_t now, const LightPalette& pal, uint8_t night_factor);

// Triangle "breathing" wave, 0..255, with the given period.
uint8_t breathe(uint32_t now, uint32_t period_ms);

}  // namespace gs
