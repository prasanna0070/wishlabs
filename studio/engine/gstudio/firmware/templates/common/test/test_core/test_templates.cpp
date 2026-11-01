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

void register_template_tests() {
  RUN_TEST(test_face_blinks_reacts_and_sleeps);
  RUN_TEST(test_clap_detector_single_and_double);
  RUN_TEST(test_clap_detector_ignores_sustained_sound);
  RUN_TEST(test_notice_queue_priority_expiry_and_ack);
  RUN_TEST(test_quiet_hours_and_clock_format);
  RUN_TEST(test_scenes_and_auto_dim);
  RUN_TEST(test_trend_and_threshold);
}
