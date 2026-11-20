#include <unity.h>

#include <string.h>

#include "gs_motion_plan.h"
#include "gs_templates.h"

using namespace gs;

static void test_face_blinks_reacts_and_sleeps() {
  FaceFsm f(7, 60000);
  int blinks = 0;
  for (uint32_t t = 0; t < 30000; t += 10) {
    f.tick(t);
    if (f.expression(t) == Expression::Blink) ++blinks;
  }
  TEST_ASSERT_TRUE(blinks >= 4 * 15);  // at least four 150 ms blinks in 30 s
  f.poke(30000);
  TEST_ASSERT_EQUAL(Expression::Happy, f.expression(31000));
  TEST_ASSERT_NOT_EQUAL(Expression::Happy, f.expression(33000));
  f.tick(95000);
  TEST_ASSERT_EQUAL(Expression::Sleepy, f.expression(95000));
  f.clap(96000);
  TEST_ASSERT_EQUAL(Expression::Surprised, f.expression(96500));
  f.set_listening(true, 97000);
  TEST_ASSERT_EQUAL(Expression::Listening, f.expression(97000));
}

static void test_clap_detector_single_and_double() {
  ClapDetector d(6000);
  uint32_t t = 0;
  for (; t < 1000; t += 10) TEST_ASSERT_EQUAL(ClapEvent::None, d.feed(t, 150));
  TEST_ASSERT_EQUAL(ClapEvent::None, d.feed(t, 20000));
  t += 10;
  TEST_ASSERT_EQUAL(ClapEvent::None, d.feed(t, 12000));
  t += 10;
  TEST_ASSERT_EQUAL(ClapEvent::Single, d.feed(t, 300));
  for (t += 10; t < 1400; t += 10) d.feed(t, 150);
  d.feed(t, 22000);
  t += 20;
  TEST_ASSERT_EQUAL(ClapEvent::Double, d.feed(t, 200));
}

static void test_clap_detector_ignores_sustained_sound() {
  ClapDetector d(6000);
  uint32_t t = 0;
  for (; t < 500; t += 10) d.feed(t, 150);
  int events = 0;
  for (; t < 1500; t += 10)
    if (d.feed(t, 15000) != ClapEvent::None) ++events;  // a second of loud music
  for (; t < 1600; t += 10)
    if (d.feed(t, 150) != ClapEvent::None) ++events;
  TEST_ASSERT_EQUAL(0, events);
}

// Motion pack: the servo planner behind a wave on tap (gs_core gs_motion_plan, driven by src/gs_motion.cpp).
static void test_servo_wave_timing_and_settle() {
  ServoPlanner p;
  p.begin(90, 0);
  TEST_ASSERT_TRUE(p.tick(0));  // the first output goes to rest (the angle at power-up is unknown)
  TEST_ASSERT_EQUAL(90, p.angle());
  TEST_ASSERT_FALSE(p.tick(10));
  p.wave(2, 35, 90, 120, 100);
  TEST_ASSERT_TRUE(p.busy());
  uint8_t mx = 0, mn = 180;
  uint32_t t = 100;
  for (; t < 6000 && p.busy(); t += 10) {
    p.tick(t);
    if (p.angle() > mx) mx = p.angle();
    if (p.angle() < mn) mn = p.angle();
  }
  // 35 + 70 + 70 + 70 + 35 = 280 degrees at 120 deg/s = 2.33 s (+ one 10 ms tick per step)
  TEST_ASSERT_EQUAL(125, mx);
  TEST_ASSERT_EQUAL(55, mn);
  TEST_ASSERT_TRUE(t > 100 + 2300 && t < 100 + 2500);
  TEST_ASSERT_EQUAL(90, p.angle());
  TEST_ASSERT_FALSE(p.settled(t));
  TEST_ASSERT_TRUE(p.settled(t + ServoPlanner::kSettleMs));
}

static void test_servo_wave_clamps_and_jumps() {
  ServoPlanner p;
  p.begin(150, 0);
  p.tick(0);
  p.wave(1, 60, 150, 0, 10);  // speed 0 = jump; 150 + 60 clamps to 180
  TEST_ASSERT_TRUE(p.tick(20));
  TEST_ASSERT_EQUAL(180, p.angle());
  p.tick(30);
  TEST_ASSERT_EQUAL(90, p.angle());
  p.tick(40);
  TEST_ASSERT_EQUAL(150, p.angle());
  TEST_ASSERT_FALSE(p.busy());
  p.wave(9, 10, 90, 0, 50);  // n is capped at 5 waves (11 steps)
  TEST_ASSERT_EQUAL(11, p.queued());
}

void setUp(void) {}
void tearDown(void) {}

// desk_companion product tests: the hardware-free logic this template runs (lib/gs_core/gs_templates).
int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_face_blinks_reacts_and_sleeps);
  RUN_TEST(test_clap_detector_single_and_double);
  RUN_TEST(test_clap_detector_ignores_sustained_sound);
  RUN_TEST(test_servo_wave_timing_and_settle);
  RUN_TEST(test_servo_wave_clamps_and_jumps);
  return UNITY_END();
}
