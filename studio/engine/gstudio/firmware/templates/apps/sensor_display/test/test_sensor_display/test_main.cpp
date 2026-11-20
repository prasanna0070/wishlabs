#include <unity.h>

#include <string.h>

#include "gs_soil.h"
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

static void test_soil_percent_maps_the_calibration() {
  // dry 3000 = 0 %, wet 1300 = 100 %; a capacitive probe reads lower when wetter
  TEST_ASSERT_EQUAL(0, soil_percent(3000, 3000, 1300));
  TEST_ASSERT_EQUAL(100, soil_percent(1300, 3000, 1300));
  TEST_ASSERT_EQUAL(50, soil_percent(2150, 3000, 1300));
  TEST_ASSERT_EQUAL(30, soil_percent(2490, 3000, 1300));  // 510 / 1700 = 30 %
  TEST_ASSERT_EQUAL(0, soil_percent(3500, 3000, 1300));   // drier than the dry calibration: clamped
  TEST_ASSERT_EQUAL(0, soil_percent(4095, 3000, 1300));   // open lead reads full scale: clamped
  TEST_ASSERT_EQUAL(100, soil_percent(900, 3000, 1300));  // wetter than the wet calibration: clamped
  TEST_ASSERT_EQUAL(100, soil_percent(0, 3000, 1300));
  TEST_ASSERT_EQUAL(-1, soil_percent(2000, 1300, 3000));  // bad calibration (wet above dry)
  TEST_ASSERT_EQUAL(-1, soil_percent(2000, 2000, 2000));
}

static void test_mean_of() {
  const int v[4] = {1000, 1002, 1004, 1006};
  TEST_ASSERT_EQUAL(1003, mean_of(v, 4));
  TEST_ASSERT_EQUAL(1000, mean_of(v, 1));
  TEST_ASSERT_EQUAL(0, mean_of(v, 0));
}

static void test_stable_needs_repeats() {
  Stable s(2, true);
  TEST_ASSERT_TRUE(s.update(false));   // one low sample is not enough (sloshing)
  TEST_ASSERT_TRUE(s.update(true));    // back up: the count restarts
  TEST_ASSERT_TRUE(s.update(false));
  TEST_ASSERT_FALSE(s.update(false));  // two in a row: now low
  TEST_ASSERT_FALSE(s.update(true));
  TEST_ASSERT_TRUE(s.update(true));
  Stable one(0, false);                // need 0 behaves as 1
  TEST_ASSERT_TRUE(one.update(true));
}

static void test_soil_dry_warning_has_hysteresis() {
  Threshold dry(30.0f, 1000.0f, 3.0f);  // as the app sets it for soil_dry_percent 30
  TEST_ASSERT_FALSE(dry.update(45));
  TEST_ASSERT_TRUE(dry.update(29));
  TEST_ASSERT_TRUE(dry.update(32));     // inside the 3-point band: still dry
  TEST_ASSERT_FALSE(dry.update(34));    // watered
  TEST_ASSERT_FALSE(dry.update(100));   // the high side never triggers
}

void setUp(void) {}
void tearDown(void) {}

// sensor_display product tests: the hardware-free logic this template runs (lib/gs_core/gs_templates).
int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_trend_and_threshold);
  RUN_TEST(test_soil_percent_maps_the_calibration);
  RUN_TEST(test_mean_of);
  RUN_TEST(test_stable_needs_repeats);
  RUN_TEST(test_soil_dry_warning_has_hysteresis);
  return UNITY_END();
}
