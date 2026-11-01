#include "gs_light.h"

#include "gs_util.h"

namespace gs {

static int hexval(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

bool parse_hex_color(const char* s, Rgb* out) {
  if (!s) return false;
  if (*s == '#') ++s;
  int v[6];
  for (int i = 0; i < 6; ++i) {
    if (!s[i]) return false;
    v[i] = hexval(s[i]);
    if (v[i] < 0) return false;
  }
  if (s[6]) return false;
  out->r = uint8_t(v[0] * 16 + v[1]);
  out->g = uint8_t(v[2] * 16 + v[3]);
  out->b = uint8_t(v[4] * 16 + v[5]);
  return true;
}

Rgb scale(Rgb c, uint8_t level) {
  Rgb o;
  o.r = uint8_t((uint16_t(c.r) * level + 127) / 255);
  o.g = uint8_t((uint16_t(c.g) * level + 127) / 255);
  o.b = uint8_t((uint16_t(c.b) * level + 127) / 255);
  return o;
}

void NightLight::start(uint32_t now, uint32_t duration_ms) {
  const uint8_t current = level(now);
  active_ = true;
  start_ = now;
  start_level_ = current;  // continue from the present brightness: no flash on restart
  fade_out_at_ = now + duration_ms;
}

void NightLight::stop(uint32_t now) {
  if (!active(now)) return;
  if (!time_reached(now, fade_out_at_)) fade_out_at_ = now;
}

bool NightLight::active(uint32_t now) const {
  return active_ && !time_reached(now, fade_out_at_ + kFadeOutMs);
}

uint32_t NightLight::remaining_ms(uint32_t now) const {
  if (!active(now)) return 0;
  return (fade_out_at_ + kFadeOutMs) - now;
}

uint8_t NightLight::level(uint32_t now) const {
  if (!active(now)) return 0;
  uint32_t full = 255;
  const uint32_t since = now - start_;
  if (since < kFadeInMs) full = start_level_ + (255u - start_level_) * since / kFadeInMs;
  if (time_reached(now, fade_out_at_)) {
    const uint32_t into = now - fade_out_at_;
    const uint32_t left = into >= kFadeOutMs ? 0 : kFadeOutMs - into;
    full = full * left / kFadeOutMs;
  }
  return uint8_t(full > 255 ? 255 : full);
}

uint8_t breathe(uint32_t now, uint32_t period_ms) {
  if (period_ms < 2) return 255;
  const uint32_t t = now % period_ms;
  const uint32_t half = period_ms / 2;
  const uint32_t v = t < half ? t * 255 / half : (period_ms - t) * 255 / (period_ms - half);
  return uint8_t(v > 255 ? 255 : v);
}

Rgb indicator_color(Indicator ind, uint32_t now, const LightPalette& pal, uint8_t night_factor) {
  const uint8_t night = uint8_t((uint16_t(pal.night_level) * night_factor) / 255);
  switch (ind) {
    case Indicator::Listening:
      return scale(pal.listening, pal.indicator_level);
    case Indicator::KeepPrompt: {
      const uint8_t b = uint8_t(40 + (uint16_t(breathe(now, 1600)) * 215) / 255);
      return scale(pal.keep, uint8_t((uint16_t(b) * pal.indicator_level) / 255));
    }
    case Indicator::Saved:
      return ((now / 250) % 2) ? scale(pal.saved, pal.indicator_level) : Rgb();
    case Indicator::Discarded:
      return scale(pal.discarded, uint8_t(pal.indicator_level / 2));
    case Indicator::Error:
      return ((now / 400) % 2) ? scale(pal.error, pal.indicator_level) : Rgb();
    case Indicator::Story:
    case Indicator::Off:
    default:
      return scale(pal.night, night);
  }
}

}  // namespace gs
