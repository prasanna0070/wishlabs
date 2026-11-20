#include "gs_motion.h"

#include "gs_board.h"
#include "gs_motion_plan.h"

namespace gsmotion {

#if GS_HAS_SERVO
static gs::ServoPlanner s_plan;
static bool s_pulsing = false;
#endif

bool present() {
#if GS_HAS_SERVO
  return true;
#else
  return false;
#endif
}

void begin(uint8_t rest_deg) {
#if GS_HAS_SERVO
  s_plan.begin(rest_deg, millis());
#else
  (void)rest_deg;
#endif
}

void move_to(uint8_t deg, uint16_t speed_deg_s) {
#if GS_HAS_SERVO
  s_plan.move_to(deg, speed_deg_s, millis());
#else
  (void)deg;
  (void)speed_deg_s;
#endif
}

void wave(uint8_t n, uint8_t amplitude_deg, uint8_t rest_deg, uint16_t speed_deg_s) {
#if GS_HAS_SERVO
  s_plan.wave(n, amplitude_deg, rest_deg, speed_deg_s, millis());
#else
  (void)n;
  (void)amplitude_deg;
  (void)rest_deg;
  (void)speed_deg_s;
#endif
}

void pulse(uint8_t open_deg, uint8_t rest_deg, uint32_t hold_ms, uint16_t speed_deg_s) {
#if GS_HAS_SERVO
  s_plan.pulse(open_deg, rest_deg, hold_ms, speed_deg_s, millis());
#else
  (void)open_deg;
  (void)rest_deg;
  (void)hold_ms;
  (void)speed_deg_s;
#endif
}

void stop() {
#if GS_HAS_SERVO
  s_plan.stop(millis());
#endif
}

void tick(uint32_t now) {
#if GS_HAS_SERVO
  if (s_plan.tick(now)) {
    gsboard::servo_write(s_plan.angle());  // attaches the 50 Hz output on first use
    s_pulsing = true;
  } else if (s_pulsing && s_plan.settled(now)) {
    gsboard::servo_detach();  // settled: stop the pulses (no holding current, no buzz)
    s_pulsing = false;
  }
#else
  (void)now;
#endif
}

bool busy() {
#if GS_HAS_SERVO
  return s_plan.busy();
#else
  return false;
#endif
}

uint8_t angle() {
#if GS_HAS_SERVO
  return s_plan.angle();
#else
  return 0;
#endif
}

}  // namespace gsmotion
