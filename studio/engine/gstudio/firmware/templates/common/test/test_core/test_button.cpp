#include <unity.h>

#include <vector>

#include "gs_button.h"

using gs::ButtonEvent;
using gs::ButtonFsm;

// Feeds a level from t0 to t1 (exclusive) in 1 ms steps; collects non-None events.
static void feed(ButtonFsm& b, uint32_t t0, uint32_t t1, bool level, std::vector<ButtonEvent>& ev) {
  for (uint32_t t = t0; t != t1; ++t) {
    ButtonEvent e = b.update(t, level);
    if (e != ButtonEvent::None) ev.push_back(e);
  }
}

static void test_tap_is_press_then_tap() {
  ButtonFsm b;
  std::vector<ButtonEvent> ev;
  feed(b, 0, 100, false, ev);
  feed(b, 100, 250, true, ev);
  feed(b, 250, 400, false, ev);
  TEST_ASSERT_EQUAL(2, ev.size());
  TEST_ASSERT_EQUAL(ButtonEvent::Press, ev[0]);
  TEST_ASSERT_EQUAL(ButtonEvent::Tap, ev[1]);
}

static void test_long_press_fires_once_at_threshold() {
  ButtonFsm b(gs::ButtonConfig{30, 600});
  std::vector<ButtonEvent> ev;
  feed(b, 0, 50, false, ev);
  uint32_t long_at = 0;
  for (uint32_t t = 50; t < 1500; ++t) {
    ButtonEvent e = b.update(t, true);
    if (e == ButtonEvent::LongStart) long_at = t;
    if (e != ButtonEvent::None) ev.push_back(e);
  }
  feed(b, 1500, 1600, false, ev);
  TEST_ASSERT_EQUAL(3, ev.size());
  TEST_ASSERT_EQUAL(ButtonEvent::Press, ev[0]);
  TEST_ASSERT_EQUAL(ButtonEvent::LongStart, ev[1]);
  TEST_ASSERT_EQUAL(ButtonEvent::LongRelease, ev[2]);
  // The press began at t=50, so the long press fires at 50 + 600.
  TEST_ASSERT_EQUAL_UINT32(650, long_at);
}

static void test_contact_bounce_gives_one_press() {
  ButtonFsm b;
  std::vector<ButtonEvent> ev;
  feed(b, 0, 50, false, ev);
  for (uint32_t t = 50; t < 75; ++t) {  // 25 ms of chatter, toggling every 3 ms
    ButtonEvent e = b.update(t, ((t / 3) % 2) == 0);
    if (e != ButtonEvent::None) ev.push_back(e);
  }
  feed(b, 75, 200, true, ev);
  feed(b, 200, 300, false, ev);
  TEST_ASSERT_EQUAL(2, ev.size());
  TEST_ASSERT_EQUAL(ButtonEvent::Press, ev[0]);
  TEST_ASSERT_EQUAL(ButtonEvent::Tap, ev[1]);
}

static void test_short_glitch_is_ignored() {
  ButtonFsm b;
  std::vector<ButtonEvent> ev;
  feed(b, 0, 50, false, ev);
  feed(b, 50, 60, true, ev);  // 10 ms spike, shorter than the 30 ms debounce
  feed(b, 60, 200, false, ev);
  TEST_ASSERT_EQUAL(0, ev.size());
}

static void test_held_at_boot_is_not_a_press() {
  ButtonFsm b;
  std::vector<ButtonEvent> ev;
  feed(b, 0, 2000, true, ev);  // held while powering up
  TEST_ASSERT_EQUAL(0, ev.size());
  feed(b, 2000, 2100, false, ev);
  TEST_ASSERT_EQUAL(0, ev.size());
  feed(b, 2100, 2200, true, ev);
  feed(b, 2200, 2300, false, ev);
  TEST_ASSERT_EQUAL(2, ev.size());
  TEST_ASSERT_EQUAL(ButtonEvent::Tap, ev[1]);
}

static void test_timer_wraparound() {
  ButtonFsm b(gs::ButtonConfig{30, 600});
  std::vector<ButtonEvent> ev;
  const uint32_t t0 = 0xFFFFFF00u;  // 256 ms before the 32-bit millisecond counter wraps
  feed(b, t0, t0 + 50, false, ev);
  feed(b, t0 + 50, t0 + 800, true, ev);  // crosses zero while held
  feed(b, t0 + 800, t0 + 900, false, ev);
  TEST_ASSERT_EQUAL(3, ev.size());
  TEST_ASSERT_EQUAL(ButtonEvent::LongStart, ev[1]);
  TEST_ASSERT_EQUAL(ButtonEvent::LongRelease, ev[2]);
}

static void test_configurable_long_press() {
  ButtonFsm b(gs::ButtonConfig{20, 1000});
  std::vector<ButtonEvent> ev;
  feed(b, 0, 10, false, ev);
  feed(b, 10, 900, true, ev);  // 890 ms: still a tap with a 1 s threshold
  feed(b, 900, 1000, false, ev);
  TEST_ASSERT_EQUAL(ButtonEvent::Tap, ev.back());
}

void register_button_tests() {
  RUN_TEST(test_tap_is_press_then_tap);
  RUN_TEST(test_long_press_fires_once_at_threshold);
  RUN_TEST(test_contact_bounce_gives_one_press);
  RUN_TEST(test_short_glitch_is_ignored);
  RUN_TEST(test_held_at_boot_is_not_a_press);
  RUN_TEST(test_timer_wraparound);
  RUN_TEST(test_configurable_long_press);
}
