#include <unity.h>

#include <string.h>

#include "gs_templates.h"

using namespace gs;

static void test_trend_and_threshold() {
  Trend t(10);
  for (int i = 0; i < 15; ++i) t.push(float(i));
  TEST_ASSERT_EQUAL(10, t.size());
  TEST_ASSERT_EQUAL_FLOAT(5.0f, t.at(0));
  TEST_ASSERT_EQUAL_FLOAT(14.0f, t.at(9));
  TEST_ASSERT_EQUAL_FLOAT(5.0f, t.min());
  TEST_ASSERT_EQUAL_FLOAT(14.0f, t.max());
  TEST_ASSERT_EQUAL_FLOAT(9.5f, t.mean());
  float out[5];
  TEST_ASSERT_EQUAL(5, t.downsample(out, 5));
  TEST_ASSERT_EQUAL_FLOAT(5.5f, out[0]);
  TEST_ASSERT_EQUAL_FLOAT(13.5f, out[4]);
  Threshold th(10.0f, 30.0f, 1.0f);
  TEST_ASSERT_FALSE(th.update(25));
  TEST_ASSERT_TRUE(th.update(30.5f));
  TEST_ASSERT_TRUE(th.update(29.5f));  // inside the hysteresis band: still warning
  TEST_ASSERT_FALSE(th.update(28.5f));
  TEST_ASSERT_TRUE(th.update(9.0f));
}

void setUp(void) {}
void tearDown(void) {}

// sensor_display product tests: the hardware-free logic this template runs (lib/gs_core/gs_templates).
int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_trend_and_threshold);
  return UNITY_END();
}
