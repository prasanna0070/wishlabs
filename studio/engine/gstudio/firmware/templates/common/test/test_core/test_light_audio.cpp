#include <unity.h>

#include <string.h>

#include "gs_audio_math.h"
#include "gs_light.h"
#include "gs_pin.h"

static void test_night_light_fades_in_holds_and_fades_out() {
  gs::NightLight n;
  n.start(1000, 20u * 60u * 1000u);
  TEST_ASSERT_EQUAL(0, n.level(1000));
  TEST_ASSERT_TRUE(n.level(1750) > 100 && n.level(1750) < 160);
  TEST_ASSERT_EQUAL(255, n.level(3000));
  TEST_ASSERT_EQUAL(255, n.level(1000 + 20u * 60u * 1000u - 1));
  const uint32_t end = 1000 + 20u * 60u * 1000u + gs::NightLight::kFadeOutMs;
  TEST_ASSERT_TRUE(n.level(end - 15000) > 100 && n.level(end - 15000) < 140);
  TEST_ASSERT_EQUAL(0, n.level(end));
  TEST_ASSERT_FALSE(n.active(end));
}

static void test_night_light_restart_does_not_flash() {
  gs::NightLight n;
  n.start(0, 60000);
  const uint8_t before = n.level(800);
  n.start(800, 60000);
  TEST_ASSERT_EQUAL(before, n.level(800));
  n.stop(5000);
  TEST_ASSERT_TRUE(n.level(5000 + 1000) < 255);
}

static void test_indicator_colours() {
  gs::LightPalette pal;
  const gs::Rgb night = gs::indicator_color(gs::Indicator::Off, 0, pal, 255);
  TEST_ASSERT_TRUE(night.r > night.b);  // warm
  const gs::Rgb dark = gs::indicator_color(gs::Indicator::Off, 0, pal, 0);
  TEST_ASSERT_EQUAL(0, dark.r + dark.g + dark.b);
  const gs::Rgb listen = gs::indicator_color(gs::Indicator::Listening, 0, pal, 255);
  TEST_ASSERT_TRUE(listen.b > listen.r && listen.b > listen.g);
  gs::Rgb c;
  TEST_ASSERT_TRUE(gs::parse_hex_color("#FFB36B", &c));
  TEST_ASSERT_EQUAL(0xFF, c.r);
  TEST_ASSERT_EQUAL(0xB3, c.g);
  TEST_ASSERT_EQUAL(0x6B, c.b);
  TEST_ASSERT_FALSE(gs::parse_hex_color("#FFB36", &c));
  TEST_ASSERT_FALSE(gs::parse_hex_color("#GGB36B", &c));
}

static void test_wav_header_round_trip_and_extra_chunks() {
  uint8_t buf[200];
  gs::wav_header(buf, 16000, 16, 1, 64000);
  gs::WavInfo info;
  TEST_ASSERT_TRUE(gs::wav_parse(buf, 44, 44 + 64000, &info));
  TEST_ASSERT_EQUAL_UINT32(16000, info.sample_rate);
  TEST_ASSERT_EQUAL_UINT32(44, info.data_offset);
  TEST_ASSERT_EQUAL_UINT32(64000, info.data_bytes);
  // ffmpeg-style: a LIST chunk before data.
  uint8_t f[120];
  memcpy(f, buf, 36);
  memcpy(f + 36, "LIST", 4);
  f[40] = 26; f[41] = 0; f[42] = 0; f[43] = 0;
  memset(f + 44, 'x', 26);
  memcpy(f + 70, "data", 4);
  f[74] = 0xFF; f[75] = 0xFF; f[76] = 0xFF; f[77] = 0xFF;  // streamed: unknown size
  TEST_ASSERT_TRUE(gs::wav_parse(f, 78, 1078, &info));
  TEST_ASSERT_EQUAL_UINT32(78, info.data_offset);
  TEST_ASSERT_EQUAL_UINT32(1000, info.data_bytes);
  gs::wav_header(buf, 16000, 8, 1, 100);  // 8-bit is refused
  TEST_ASSERT_FALSE(gs::wav_parse(buf, 44, 144, &info));
  TEST_ASSERT_FALSE(gs::wav_parse(reinterpret_cast<const uint8_t*>("not a wav at all"), 16, 16, &info));
}

static void test_volume_curve_is_capped_and_monotonic() {
  uint16_t prev = 0;
  for (int v = 0; v <= 60; ++v) {
    const uint16_t g = gs::volume_gain_q12(uint8_t(v), 60);
    TEST_ASSERT_TRUE(g >= prev);
    prev = g;
  }
  TEST_ASSERT_EQUAL(gs::volume_gain_q12(60, 60), gs::volume_gain_q12(100, 60));  // never above the max
  TEST_ASSERT_EQUAL(4096, gs::volume_gain_q12(100, 100));
  TEST_ASSERT_EQUAL(0, gs::volume_gain_q12(0, 100));
  int16_t s[3] = {32767, -32768, 1000};
  gs::apply_gain(s, 3, 2048);
  TEST_ASSERT_EQUAL(16383, s[0]);
  TEST_ASSERT_EQUAL(-16384, s[1]);
  TEST_ASSERT_EQUAL(500, s[2]);
}

static void test_mic_conversion_and_levels() {
  TEST_ASSERT_EQUAL(32767, gs::mic_to_pcm16(0x7FFFFF00, 8));
  TEST_ASSERT_EQUAL(-32768, gs::mic_to_pcm16(int32_t(0x80000000u), 8));
  TEST_ASSERT_EQUAL(256, gs::mic_to_pcm16(0x00010000, 8));
  int16_t tone[1600];
  uint32_t ph = 0;
  gs::tone_fill(tone, 1600, 16000, 1000, &ph, 16384);
  const float db = gs::rms_dbfs(tone, 1600);
  TEST_ASSERT_FLOAT_WITHIN(0.5f, -9.03f, db);  // sine at half scale: -6 dB peak, -9 dB RMS
  TEST_ASSERT_TRUE(gs::peak_abs(tone, 1600) > 16000);
  int16_t silence[100] = {0};
  TEST_ASSERT_FLOAT_WITHIN(0.01f, -120.0f, gs::rms_dbfs(silence, 100));
}

static void test_pin_guard_locks_and_backs_off() {
  // MAJ-2: a new PIN needs 6 to 8 digits; a 4-digit PIN from an older firmware still unlocks.
  TEST_ASSERT_TRUE(gs::pin_format_ok("246813"));
  TEST_ASSERT_TRUE(gs::pin_format_ok("12345678"));
  TEST_ASSERT_FALSE(gs::pin_format_ok("12345"));
  TEST_ASSERT_FALSE(gs::pin_format_ok("2468"));
  TEST_ASSERT_TRUE(gs::pin_format_ok("2468", gs::kPinLegacyMinDigits));
  TEST_ASSERT_FALSE(gs::pin_format_ok("123", gs::kPinLegacyMinDigits));
  TEST_ASSERT_FALSE(gs::pin_format_ok("123456789"));
  TEST_ASSERT_FALSE(gs::pin_format_ok("12a4"));
  gs::PinGuard g;
  for (int i = 0; i < 4; ++i) g.failure(1000);
  TEST_ASSERT_FALSE(g.locked(1000));
  g.failure(1000);
  TEST_ASSERT_TRUE(g.locked(1000));
  TEST_ASSERT_EQUAL_UINT32(60000, g.retry_after_ms(1000));
  TEST_ASSERT_FALSE(g.locked(61000));
  for (int i = 0; i < 5; ++i) g.failure(70000);
  TEST_ASSERT_EQUAL_UINT32(120000, g.retry_after_ms(70000));  // second lock doubles
  g.success();
  TEST_ASSERT_FALSE(g.locked(70001));
}

void register_light_audio_tests() {
  RUN_TEST(test_night_light_fades_in_holds_and_fades_out);
  RUN_TEST(test_night_light_restart_does_not_flash);
  RUN_TEST(test_indicator_colours);
  RUN_TEST(test_wav_header_round_trip_and_extra_chunks);
  RUN_TEST(test_volume_curve_is_capped_and_monotonic);
  RUN_TEST(test_mic_conversion_and_levels);
  RUN_TEST(test_pin_guard_locks_and_backs_off);
}
