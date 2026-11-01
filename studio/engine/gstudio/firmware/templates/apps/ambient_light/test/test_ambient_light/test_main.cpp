#include <unity.h>

#include <string.h>

#include "gs_templates.h"

using namespace gs;

static void test_scenes_and_auto_dim() {
  Scene s;
  s.color = Rgb{255, 120, 40};
  s.brightness = 200;
  s.effect = Effect::Solid;
  Rgb p = scene_pixel(s, 0, 12, 0, 1);
  TEST_ASSERT_EQUAL(200, p.r);
  s.effect = Effect::Candle;
  bool varied = false;
  Rgb first = scene_pixel(s, 3, 12, 0, 42);
  for (uint32_t t = 0; t < 2000; t += 90) {
    Rgb q = scene_pixel(s, 3, 12, t, 42);
    TEST_ASSERT_TRUE(q.r <= 200 && q.r >= 100);
    varied = varied || q.r != first.r;
  }
  TEST_ASSERT_TRUE(varied);
  TEST_ASSERT_EQUAL(scene_pixel(s, 3, 12, 500, 42).r, scene_pixel(s, 3, 12, 500, 42).r);  // deterministic
  Effect e;
  TEST_ASSERT_TRUE(parse_effect("rainbow", &e));
  TEST_ASSERT_EQUAL(Effect::Rainbow, e);
  TEST_ASSERT_FALSE(parse_effect("strobe", &e));
  AutoDim d(5, 300, 25);
  TEST_ASSERT_EQUAL(25, d.update(1));
  for (int i = 0; i < 100; ++i) d.update(1000);
  TEST_ASSERT_TRUE(d.factor() > 250);
  const uint8_t after_one = d.update(1);
  TEST_ASSERT_TRUE(after_one > 200);  // smoothed: no sudden drop
}

void setUp(void) {}
void tearDown(void) {}

// ambient_light product tests: the hardware-free logic this template runs (lib/gs_core/gs_templates).
int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_scenes_and_auto_dim);
  return UNITY_END();
}
