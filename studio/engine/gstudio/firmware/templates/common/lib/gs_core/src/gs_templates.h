// Hardware-free logic for the desk_companion, notifier_clock, ambient_light and sensor_display
// templates. Each piece is small, deterministic and unit-tested on the host.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "gs_light.h"
#include "gs_util.h"

namespace gs {

// ---------------------------------------------------------------- desk_companion
enum class Expression : uint8_t { Neutral, Blink, Happy, Sleepy, Surprised, Listening };
const char* expression_name(Expression e);

class FaceFsm {
 public:
  explicit FaceFsm(uint32_t seed = 1, uint32_t sleep_after_ms = 5u * 60u * 1000u) : rng_(seed), sleep_after_(sleep_after_ms) {}
  void poke(uint32_t now);  // tap: happy for 2.5 s, wakes up
  void clap(uint32_t now);  // surprised for 1.5 s, wakes up
  void set_listening(bool on, uint32_t now);
  void tick(uint32_t now);  // schedules blinks, falls asleep when idle
  Expression expression(uint32_t now) const;
  void set_sleep_after(uint32_t ms) { sleep_after_ = ms; }

 private:
  Rng rng_;
  uint32_t sleep_after_;
  uint32_t last_activity_ = 0;
  uint32_t next_blink_ = 2000;
  uint32_t blink_until_ = 0;
  Expression timed_ = Expression::Neutral;
  uint32_t timed_until_ = 0;
  bool listening_ = false;
  bool started_ = false;
};

// Detects claps from per-block peak levels (feed one value per ~10 ms block).
// A clap is a jump from a quiet floor to above `threshold` that falls back within 150 ms.
// Two claps 200..800 ms apart make a double clap.
enum class ClapEvent : uint8_t { None, Single, Double };

class ClapDetector {
 public:
  explicit ClapDetector(int16_t threshold = 6000) : threshold_(threshold) {}
  ClapEvent feed(uint32_t now, int16_t block_peak);
  void set_threshold(int16_t t) { threshold_ = t; }

 private:
  int16_t threshold_;
  int32_t floor_ = 200;
  bool high_ = false;
  uint32_t rise_at_ = 0;
  bool have_clap_ = false;
  uint32_t last_clap_ = 0;
};

// ---------------------------------------------------------------- notifier_clock
constexpr size_t kNoticeTextMax = 47;
constexpr size_t kMaxNotices = 8;

struct Notice {
  uint16_t id = 0;
  uint8_t priority = 0;  // higher shows first
  Rgb color;
  char text[kNoticeTextMax + 1] = {0};
  uint32_t created = 0;
  uint32_t expires = 0;  // 0 = until acknowledged
};

class NoticeQueue {
 public:
  // Returns the new id (never 0). When full, the lowest-priority, oldest notice is replaced.
  uint16_t push(const char* text, Rgb color, uint8_t priority, uint32_t ttl_ms, uint32_t now);
  bool ack(uint16_t id);
  void ack_all() { count_ = 0; }
  void expire(uint32_t now);
  const Notice* top(uint32_t now) const;
  size_t count() const { return count_; }
  const Notice* at(size_t i) const { return i < count_ ? &items_[i] : nullptr; }

 private:
  Notice items_[kMaxNotices];
  size_t count_ = 0;
  uint16_t next_id_ = 1;
};

// minute_of_day 0..1439. start == end means "no quiet hours". Wraps past midnight.
bool in_quiet_hours(uint16_t minute_of_day, uint16_t start, uint16_t end);
void format_clock(int hour, int minute, bool h24, char* out, size_t cap);  // "21:05" or "9:05 pm"

// ---------------------------------------------------------------- ambient_light
enum class Effect : uint8_t { Solid, Breathe, Candle, Rainbow };
const char* effect_name(Effect e);
bool parse_effect(const char* s, Effect* out);

struct Scene {
  char name[24] = {0};
  Rgb color;
  uint8_t brightness = 128;  // 0..255 before the global cap
  Effect effect = Effect::Solid;
};

Rgb hsv(uint8_t h, uint8_t s, uint8_t v);
// Colour of one pixel of a scene at time now (deterministic for a given seed).
Rgb scene_pixel(const Scene& sc, uint16_t pixel, uint16_t count, uint32_t now, uint32_t seed);

// Maps ambient light (lux) to a brightness factor 0..255 with smoothing: darker room, dimmer light.
class AutoDim {
 public:
  AutoDim(float dark_lux = 5.0f, float bright_lux = 300.0f, uint8_t min_factor = 25)
      : dark_(dark_lux), bright_(bright_lux), min_(min_factor) {}
  uint8_t update(float lux);
  uint8_t factor() const { return uint8_t(level_ + 0.5f); }

 private:
  float dark_, bright_;
  uint8_t min_;
  float level_ = 255.0f;
  bool primed_ = false;
};

// ---------------------------------------------------------------- sensor_display
constexpr size_t kTrendMax = 144;

class Trend {
 public:
  explicit Trend(size_t capacity = kTrendMax) : cap_(capacity > kTrendMax ? kTrendMax : capacity) {}
  void push(float v);
  size_t size() const { return n_; }
  float at(size_t i) const;  // 0 = oldest
  float min() const;
  float max() const;
  float mean() const;
  // Averages the series into n buckets (for a graph n pixels wide). Returns buckets written.
  size_t downsample(float* out, size_t n) const;

 private:
  float v_[kTrendMax] = {0};
  size_t cap_;
  size_t head_ = 0;
  size_t n_ = 0;
};

// Warns when the value goes above `high` (or below `low`), clears with hysteresis.
class Threshold {
 public:
  Threshold(float low, float high, float hysteresis) : low_(low), high_(high), hyst_(hysteresis) {}
  bool update(float v);
  bool active() const { return active_; }

 private:
  float low_, high_, hyst_;
  bool active_ = false;
};

}  // namespace gs
