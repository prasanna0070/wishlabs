#include "gs_motion_plan.h"

namespace gs {

uint8_t clamp_deg(int deg) { return uint8_t(deg < 0 ? 0 : (deg > 180 ? 180 : deg)); }

void ServoPlanner::clear(uint32_t now) {
  n_ = 0;
  i_ = 0;
  holding_ = false;
  last_ms_ = now;
  idle_since_ = now;
}

void ServoPlanner::add(uint8_t target, uint16_t speed, uint32_t hold_ms) {
  if (n_ >= kMaxSteps) return;
  steps_[n_++] = Step{clamp_deg(target), speed, hold_ms};
}

void ServoPlanner::begin(uint8_t rest_deg, uint32_t now) {
  clear(now);
  pos_ = float(clamp_deg(rest_deg));
  last_out_ = 255;  // the first tick writes the rest angle
}

void ServoPlanner::move_to(uint8_t deg, uint16_t speed_deg_s, uint32_t now) {
  clear(now);
  add(deg, speed_deg_s, 0);
}

void ServoPlanner::wave(uint8_t n, uint8_t amplitude_deg, uint8_t rest_deg, uint16_t speed_deg_s, uint32_t now) {
  clear(now);
  if (n < 1) n = 1;
  if (n > 5) n = 5;
  const int r = clamp_deg(rest_deg);
  for (uint8_t k = 0; k < n; ++k) {
    add(clamp_deg(r + amplitude_deg), speed_deg_s, 0);
    add(clamp_deg(r - amplitude_deg), speed_deg_s, 0);
  }
  add(uint8_t(r), speed_deg_s, 0);
}

void ServoPlanner::pulse(uint8_t open_deg, uint8_t rest_deg, uint32_t hold_ms, uint16_t speed_deg_s, uint32_t now) {
  clear(now);
  add(open_deg, speed_deg_s, hold_ms);
  add(rest_deg, speed_deg_s, 0);
}

void ServoPlanner::stop(uint32_t now) { clear(now); }

bool ServoPlanner::tick(uint32_t now) {
  const uint32_t dt = uint32_t(now - last_ms_);
  last_ms_ = now;
  if (i_ < n_) {
    Step& s = steps_[i_];
    if (holding_) {
      if (int32_t(now - hold_until_) >= 0) {
        holding_ = false;
        ++i_;
      }
    } else {
      const float target = float(s.target);
      if (s.speed == 0) {
        pos_ = target;
      } else {
        const float step = float(s.speed) * float(dt) / 1000.0f;
        if (pos_ < target) {
          pos_ = (pos_ + step >= target) ? target : pos_ + step;
        } else if (pos_ > target) {
          pos_ = (pos_ - step <= target) ? target : pos_ - step;
        }
      }
      if (pos_ == target) {
        if (s.hold_ms > 0) {
          holding_ = true;
          hold_until_ = now + s.hold_ms;
        } else {
          ++i_;
        }
      }
    }
    if (i_ >= n_) idle_since_ = now;
  }
  const uint8_t out = angle();
  if (out != last_out_) {
    last_out_ = out;
    return true;
  }
  return false;
}

}  // namespace gs
