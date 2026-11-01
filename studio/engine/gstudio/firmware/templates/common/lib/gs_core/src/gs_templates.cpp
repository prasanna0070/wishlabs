#include "gs_templates.h"

#include <stdio.h>
#include <string.h>

namespace gs {

// ---------------------------------------------------------------- desk_companion
const char* expression_name(Expression e) {
  switch (e) {
    case Expression::Neutral: return "neutral";
    case Expression::Blink: return "blink";
    case Expression::Happy: return "happy";
    case Expression::Sleepy: return "sleepy";
    case Expression::Surprised: return "surprised";
    case Expression::Listening: return "listening";
  }
  return "neutral";
}

void FaceFsm::poke(uint32_t now) {
  last_activity_ = now;
  timed_ = Expression::Happy;
  timed_until_ = now + 2500;
}

void FaceFsm::clap(uint32_t now) {
  last_activity_ = now;
  timed_ = Expression::Surprised;
  timed_until_ = now + 1500;
}

void FaceFsm::set_listening(bool on, uint32_t now) {
  listening_ = on;
  if (on) last_activity_ = now;
}

void FaceFsm::tick(uint32_t now) {
  if (!started_) {
    started_ = true;
    last_activity_ = now;
    next_blink_ = now + 2000 + rng_.below(4000);
  }
  if (time_reached(now, next_blink_)) {
    blink_until_ = now + 150;
    next_blink_ = now + 2000 + rng_.below(4000);
  }
}

Expression FaceFsm::expression(uint32_t now) const {
  if (listening_) return Expression::Listening;
  if (timed_until_ && !time_reached(now, timed_until_)) return timed_;
  if (started_ && time_reached(now, last_activity_ + sleep_after_)) return Expression::Sleepy;
  if (blink_until_ && !time_reached(now, blink_until_)) return Expression::Blink;
  return Expression::Neutral;
}

ClapEvent ClapDetector::feed(uint32_t now, int16_t peak) {
  ClapEvent ev = ClapEvent::None;
  if (!high_) {
    if (peak > threshold_ && peak > floor_ * 4) {
      high_ = true;
      rise_at_ = now;
    } else {
      floor_ = (floor_ * 15 + peak) / 16;  // slow noise-floor tracker
    }
  } else if (peak < threshold_ / 2) {
    high_ = false;
    if (now - rise_at_ <= 150) {  // short and sharp: a clap, not speech or music
      if (have_clap_ && now - last_clap_ >= 200 && now - last_clap_ <= 800) {
        ev = ClapEvent::Double;
        have_clap_ = false;
      } else {
        ev = ClapEvent::Single;
        have_clap_ = true;
        last_clap_ = now;
      }
    }
  } else if (now - rise_at_ > 400) {
    high_ = false;  // sustained sound: ignore and re-learn the floor
    floor_ = peak;
  }
  return ev;
}

// ---------------------------------------------------------------- notifier_clock
uint16_t NoticeQueue::push(const char* text, Rgb color, uint8_t priority, uint32_t ttl_ms, uint32_t now) {
  size_t slot = count_;
  if (count_ >= kMaxNotices) {
    slot = 0;
    for (size_t i = 1; i < count_; ++i) {
      const Notice& a = items_[i];
      const Notice& b = items_[slot];
      if (a.priority < b.priority || (a.priority == b.priority && static_cast<int32_t>(a.created - b.created) < 0))
        slot = i;
    }
  } else {
    ++count_;
  }
  Notice& n = items_[slot];
  n = Notice();
  n.id = next_id_;
  next_id_ = static_cast<uint16_t>(next_id_ == 0xFFFF ? 1 : next_id_ + 1);
  n.priority = priority;
  n.color = color;
  copy_str(n.text, sizeof(n.text), text ? text : "");
  n.created = now;
  n.expires = ttl_ms ? now + ttl_ms : 0;
  return n.id;
}

bool NoticeQueue::ack(uint16_t id) {
  for (size_t i = 0; i < count_; ++i) {
    if (items_[i].id == id) {
      for (size_t j = i + 1; j < count_; ++j) items_[j - 1] = items_[j];
      --count_;
      return true;
    }
  }
  return false;
}

void NoticeQueue::expire(uint32_t now) {
  size_t w = 0;
  for (size_t i = 0; i < count_; ++i) {
    if (items_[i].expires && time_reached(now, items_[i].expires)) continue;
    items_[w++] = items_[i];
  }
  count_ = w;
}

const Notice* NoticeQueue::top(uint32_t now) const {
  const Notice* best = nullptr;
  for (size_t i = 0; i < count_; ++i) {
    const Notice& n = items_[i];
    if (n.expires && time_reached(now, n.expires)) continue;
    if (!best || n.priority > best->priority ||
        (n.priority == best->priority && static_cast<int32_t>(n.created - best->created) > 0))
      best = &n;
  }
  return best;
}

bool in_quiet_hours(uint16_t m, uint16_t start, uint16_t end) {
  if (start == end) return false;
  if (start < end) return m >= start && m < end;
  return m >= start || m < end;
}

void format_clock(int hour, int minute, bool h24, char* out, size_t cap) {
  if (h24) {
    snprintf(out, cap, "%02d:%02d", hour % 24, minute % 60);
    return;
  }
  const int h = hour % 12 == 0 ? 12 : hour % 12;
  snprintf(out, cap, "%d:%02d %s", h, minute % 60, hour % 24 < 12 ? "am" : "pm");
}

// ---------------------------------------------------------------- ambient_light
const char* effect_name(Effect e) {
  switch (e) {
    case Effect::Solid: return "solid";
    case Effect::Breathe: return "breathe";
    case Effect::Candle: return "candle";
    case Effect::Rainbow: return "rainbow";
  }
  return "solid";
}

bool parse_effect(const char* s, Effect* out) {
  if (!s) return false;
  const Effect all[4] = {Effect::Solid, Effect::Breathe, Effect::Candle, Effect::Rainbow};
  for (Effect e : all) {
    if (strcmp(s, effect_name(e)) == 0) {
      *out = e;
      return true;
    }
  }
  return false;
}

Rgb hsv(uint8_t h, uint8_t s, uint8_t v) {
  Rgb o;
  if (s == 0) {
    o.r = o.g = o.b = v;
    return o;
  }
  const uint8_t region = h / 43;
  const uint8_t rem = uint8_t((h - region * 43) * 6);
  const uint8_t p = uint8_t((v * (255 - s)) >> 8);
  const uint8_t q = uint8_t((v * (255 - ((s * rem) >> 8))) >> 8);
  const uint8_t t = uint8_t((v * (255 - ((s * (255 - rem)) >> 8))) >> 8);
  switch (region) {
    case 0: o.r = v; o.g = t; o.b = p; break;
    case 1: o.r = q; o.g = v; o.b = p; break;
    case 2: o.r = p; o.g = v; o.b = t; break;
    case 3: o.r = p; o.g = q; o.b = v; break;
    case 4: o.r = t; o.g = p; o.b = v; break;
    default: o.r = v; o.g = p; o.b = q; break;
  }
  return o;
}

static uint32_t mix32(uint32_t x) {
  x ^= x >> 16;
  x *= 0x7FEB352Du;
  x ^= x >> 15;
  x *= 0x846CA68Bu;
  x ^= x >> 16;
  return x;
}

Rgb scene_pixel(const Scene& sc, uint16_t pixel, uint16_t count, uint32_t now, uint32_t seed) {
  switch (sc.effect) {
    case Effect::Breathe: {
      const uint8_t b = uint8_t(60 + (uint16_t(breathe(now, 6000)) * 195) / 255);
      return scale(sc.color, uint8_t((uint16_t(b) * sc.brightness) / 255));
    }
    case Effect::Candle: {
      // Slow random flicker per pixel, stepped every 90 ms, between 55 % and 100 %.
      const uint32_t step = now / 90;
      const uint32_t r = mix32(seed ^ (step * 2654435761u) ^ (uint32_t(pixel) * 40503u));
      const uint8_t f = uint8_t(140 + (r % 116));
      return scale(sc.color, uint8_t((uint16_t(f) * sc.brightness) / 255));
    }
    case Effect::Rainbow: {
      const uint16_t n = count ? count : 1;
      const uint8_t h = uint8_t((uint32_t(pixel) * 256u / n + now / 40u) & 0xFF);
      return hsv(h, 230, sc.brightness);
    }
    case Effect::Solid:
    default:
      return scale(sc.color, sc.brightness);
  }
}

uint8_t AutoDim::update(float lux) {
  float target;
  if (lux <= dark_) {
    target = min_;
  } else if (lux >= bright_) {
    target = 255.0f;
  } else {
    target = min_ + (255.0f - min_) * (lux - dark_) / (bright_ - dark_);
  }
  if (!primed_) {
    level_ = target;
    primed_ = true;
  } else {
    level_ += (target - level_) * 0.1f;  // about 2 s to settle at 5 updates a second
  }
  return factor();
}

// ---------------------------------------------------------------- sensor_display
void Trend::push(float v) {
  if (cap_ == 0) return;
  v_[head_] = v;
  head_ = (head_ + 1) % cap_;
  if (n_ < cap_) ++n_;
}

float Trend::at(size_t i) const {
  if (i >= n_) return 0;
  const size_t start = (head_ + cap_ - n_) % cap_;
  return v_[(start + i) % cap_];
}

float Trend::min() const {
  float m = n_ ? at(0) : 0;
  for (size_t i = 1; i < n_; ++i)
    if (at(i) < m) m = at(i);
  return m;
}

float Trend::max() const {
  float m = n_ ? at(0) : 0;
  for (size_t i = 1; i < n_; ++i)
    if (at(i) > m) m = at(i);
  return m;
}

float Trend::mean() const {
  if (!n_) return 0;
  double s = 0;
  for (size_t i = 0; i < n_; ++i) s += at(i);
  return float(s / double(n_));
}

size_t Trend::downsample(float* out, size_t n) const {
  if (n == 0 || n_ == 0) return 0;
  if (n_ <= n) {
    for (size_t i = 0; i < n_; ++i) out[i] = at(i);
    return n_;
  }
  for (size_t b = 0; b < n; ++b) {
    const size_t lo = b * n_ / n;
    const size_t hi = (b + 1) * n_ / n;
    double s = 0;
    for (size_t i = lo; i < hi; ++i) s += at(i);
    out[b] = float(s / double(hi - lo));
  }
  return n;
}

bool Threshold::update(float v) {
  if (!active_) {
    if (v > high_ || v < low_) active_ = true;
  } else if (v < high_ - hyst_ && v > low_ + hyst_) {
    active_ = false;
  }
  return active_;
}

}  // namespace gs
