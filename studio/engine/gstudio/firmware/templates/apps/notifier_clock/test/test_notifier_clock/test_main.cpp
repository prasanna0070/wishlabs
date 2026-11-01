#include <unity.h>

#include <string.h>

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

void setUp(void) {}
void tearDown(void) {}

// notifier_clock product tests: the hardware-free logic this template runs (lib/gs_core/gs_templates).
int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_notice_queue_priority_expiry_and_ack);
  RUN_TEST(test_quiet_hours_and_clock_format);
  return UNITY_END();
}
