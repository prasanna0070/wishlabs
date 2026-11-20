// gs_motion_plan: hardware-free motion planning for the motion pack's single hobby servo (on J_LED).
// The firmware glue (src/gs_motion.cpp) writes angle() to the servo whenever tick() reports a change and
// stops the pulses once the arm has settled, so a servo never holds against a stop. Angles are whole
// degrees 0..180, speeds degrees per second (0 = jump), times milliseconds (wrapping millis()).
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace gs {

class ServoPlanner {
 public:
  static constexpr uint8_t kMaxSteps = 12;
  static constexpr uint32_t kSettleMs = 600;  // idle this long before the pulses may stop

  // Arm at rest_deg. The angle at power-up is unknown, so the first output jumps there.
  void begin(uint8_t rest_deg, uint32_t now);
  // Each call replaces whatever was still queued and starts from the current angle.
  void move_to(uint8_t deg, uint16_t speed_deg_s, uint32_t now);
  // n waves (1..5): rest+amplitude, rest-amplitude, ..., then back to rest.
  void wave(uint8_t n, uint8_t amplitude_deg, uint8_t rest_deg, uint16_t speed_deg_s, uint32_t now);
  // Open, hold for hold_ms, close back to rest (a flap or a flag).
  void pulse(uint8_t open_deg, uint8_t rest_deg, uint32_t hold_ms, uint16_t speed_deg_s, uint32_t now);
  void stop(uint32_t now);  // keep the current angle, drop the queue

  // Advances the plan to `now`; true when the commanded angle changed (write it to the servo).
  bool tick(uint32_t now);
  uint8_t angle() const { return uint8_t(pos_ + 0.5f); }
  bool busy() const { return i_ < n_; }
  // True once the plan has been idle for kSettleMs (the glue then stops the pulses).
  bool settled(uint32_t now) const { return !busy() && uint32_t(now - idle_since_) >= kSettleMs; }
  uint8_t queued() const { return uint8_t(n_ - i_); }

 private:
  struct Step {
    uint8_t target;
    uint16_t speed;
    uint32_t hold_ms;
  };
  void clear(uint32_t now);
  void add(uint8_t target, uint16_t speed, uint32_t hold_ms);

  Step steps_[kMaxSteps] = {};
  uint8_t n_ = 0;
  uint8_t i_ = 0;
  float pos_ = 90.0f;
  uint8_t last_out_ = 255;  // 255 = nothing written yet
  bool holding_ = false;
  uint32_t hold_until_ = 0;
  uint32_t last_ms_ = 0;
  uint32_t idle_since_ = 0;
};

uint8_t clamp_deg(int deg);

}  // namespace gs
