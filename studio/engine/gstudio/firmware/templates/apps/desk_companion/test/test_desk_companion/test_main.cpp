#include <unity.h>

#include <string.h>

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

void setUp(void) {}
void tearDown(void) {}

// desk_companion product tests: the hardware-free logic this template runs (lib/gs_core/gs_templates).
int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_face_blinks_reacts_and_sleeps);
  RUN_TEST(test_clap_detector_single_and_double);
  RUN_TEST(test_clap_detector_ignores_sustained_sound);
  return UNITY_END();
}
