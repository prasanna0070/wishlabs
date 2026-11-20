#include <unity.h>

#include <string.h>

#include "gs_motion_plan.h"
#include "gs_schedule.h"
#include "gs_templates.h"

using namespace gs;

static void test_notice_queue_priority_expiry_and_ack() {
  NoticeQueue q;
  Rgb red{255, 0, 0}, blue{0, 0, 255};
  const uint16_t a = q.push("Laundry done", blue, 1, 0, 0);
  const uint16_t b = q.push("Door open", red, 5, 10000, 100);
  TEST_ASSERT_EQUAL(b, q.top(200)->id);
  q.expire(10200);
  TEST_ASSERT_EQUAL(1, q.count());
  TEST_ASSERT_EQUAL(a, q.top(10200)->id);
  TEST_ASSERT_TRUE(q.ack(a));
  TEST_ASSERT_FALSE(q.ack(a));
  TEST_ASSERT_NULL(q.top(10300));
  for (int i = 0; i < 12; ++i) q.push("x", red, uint8_t(i % 3), 0, uint32_t(i));
  TEST_ASSERT_EQUAL(kMaxNotices, q.count());
  TEST_ASSERT_EQUAL(2, q.top(100)->priority);
}

static void test_quiet_hours_and_clock_format() {
  TEST_ASSERT_TRUE(in_quiet_hours(23 * 60, 22 * 60, 7 * 60));
  TEST_ASSERT_TRUE(in_quiet_hours(6 * 60 + 59, 22 * 60, 7 * 60));
  TEST_ASSERT_FALSE(in_quiet_hours(7 * 60, 22 * 60, 7 * 60));
  TEST_ASSERT_FALSE(in_quiet_hours(12 * 60, 0, 0));
  TEST_ASSERT_TRUE(in_quiet_hours(13 * 60, 13 * 60, 14 * 60));
  char buf[16];
  format_clock(21, 5, true, buf, sizeof(buf));
  TEST_ASSERT_EQUAL_STRING("21:05", buf);
  format_clock(21, 5, false, buf, sizeof(buf));
  TEST_ASSERT_EQUAL_STRING("9:05 pm", buf);
  format_clock(0, 30, false, buf, sizeof(buf));
  TEST_ASSERT_EQUAL_STRING("12:30 am", buf);
}

static void test_daily_schedule_fires_once_a_day() {
  DailySchedule s;
  const uint16_t mins[] = {8 * 60, 19 * 60};
  s.set(mins, 2);
  // clock first valid at 07:00 on day 5: nothing due yet
  TEST_ASSERT_EQUAL(-1, s.due(5, 7 * 60));
  TEST_ASSERT_EQUAL(-1, s.due(5, 7 * 60 + 59));
  TEST_ASSERT_EQUAL(0, s.due(5, 8 * 60));      // 08:00 fires
  TEST_ASSERT_EQUAL(-1, s.due(5, 8 * 60));     // once
  TEST_ASSERT_EQUAL(-1, s.due(5, 8 * 60 + 5));
  TEST_ASSERT_EQUAL(1, s.due(5, 19 * 60 + 3));  // a few minutes late still fires
  TEST_ASSERT_EQUAL(-1, s.due(5, 19 * 60 + 4));
  TEST_ASSERT_EQUAL(-1, s.due(5, 19 * 60 + 2));  // clock stepped back: no repeat
  TEST_ASSERT_EQUAL(0, s.due(6, 8 * 60 + 1));   // next day again
  TEST_ASSERT_EQUAL(-1, s.due(6, 8 * 60 + 30));
}

static void test_daily_schedule_no_replay_after_boot_and_grace() {
  DailySchedule s;
  const uint16_t mins[] = {9 * 60, 14 * 60, 21 * 60};
  s.set(mins, 3);
  // reboot at 14:05: 09:00 is long past (not replayed), 14:00 is within the 10 minute grace
  TEST_ASSERT_EQUAL(1, s.due(9, 14 * 60 + 5));
  TEST_ASSERT_EQUAL(-1, s.due(9, 14 * 60 + 6));
  TEST_ASSERT_EQUAL(-1, s.due(9, 20 * 60 + 59));
  // 21:00 missed while busy until 21:10: past the grace, skipped
  TEST_ASSERT_EQUAL(-1, s.due(9, 21 * 60 + 10));
  TEST_ASSERT_EQUAL(0, s.due(10, 9 * 60));
}

static void test_daily_schedule_next_and_format() {
  DailySchedule s;
  uint16_t wait = 0;
  TEST_ASSERT_EQUAL(-1, s.next(600, &wait));
  const uint16_t mins[] = {8 * 60, 19 * 60, 1500, 20 * 60};  // 1500 is out of range: ignored
  s.set(mins, 4);
  TEST_ASSERT_EQUAL(3, s.count());
  TEST_ASSERT_EQUAL(0, s.next(7 * 60, &wait));
  TEST_ASSERT_EQUAL(60, wait);
  TEST_ASSERT_EQUAL(1, s.next(8 * 60, &wait));
  TEST_ASSERT_EQUAL(11 * 60, wait);
  TEST_ASSERT_EQUAL(0, s.next(23 * 60, &wait));  // tomorrow's first
  TEST_ASSERT_EQUAL(9 * 60, wait);
  char buf[8];
  format_hhmm(485, buf, sizeof(buf));
  TEST_ASSERT_EQUAL_STRING("08:05", buf);
  format_hhmm(0, buf, sizeof(buf));
  TEST_ASSERT_EQUAL_STRING("00:00", buf);
}

// Motion pack: a due reminder opens the flap for servo_hold_s, then closes it.
static void test_servo_pulse_opens_holds_and_closes() {
  ServoPlanner p;
  p.begin(0, 0);
  p.tick(0);
  p.pulse(90, 0, 3000, 120, 1000);  // 90 deg at 120 deg/s = 750 ms, hold 3 s, 750 ms back
  uint32_t t = 1000;
  for (; t <= 1000 + 760; t += 10) p.tick(t);
  TEST_ASSERT_EQUAL(90, p.angle());
  for (; t <= 1000 + 750 + 2900; t += 10) p.tick(t);
  TEST_ASSERT_EQUAL(90, p.angle());  // still holding
  TEST_ASSERT_TRUE(p.busy());
  for (; t <= 1000 + 750 + 3000 + 800 && p.busy(); t += 10) p.tick(t);
  TEST_ASSERT_EQUAL(0, p.angle());
  TEST_ASSERT_FALSE(p.busy());
  TEST_ASSERT_TRUE(t > 1000 + 4400 && t < 1000 + 4700);
}

void setUp(void) {}
void tearDown(void) {}

// notifier_clock product tests: the hardware-free logic this template runs (lib/gs_core/gs_templates).
int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_notice_queue_priority_expiry_and_ack);
  RUN_TEST(test_quiet_hours_and_clock_format);
  RUN_TEST(test_daily_schedule_fires_once_a_day);
  RUN_TEST(test_daily_schedule_no_replay_after_boot_and_grace);
  RUN_TEST(test_daily_schedule_next_and_format);
  RUN_TEST(test_servo_pulse_opens_holds_and_closes);
  return UNITY_END();
}
