// story_player product suite, opt-in child_voice_clip mode (network off): the real firmware records,
// encrypts, plays back, keeps, removes, erases, repairs and expires voice clips on a host SD card, and
// the microphone only opens with the listening light and face on, including in the factory self-test
// (MAJ-1, MAJ-17, MAJ-18 mic path, MIN-16, MIN-17, MIN-18, MIN-38).
#include <unity.h>

#include "suite_config.h"
#include "gs_app.h"
#include "gs_clips.h"
#include "gs_device.h"
#include "gs_host_card.h"
#include "gs_host_device.h"
#include "gs_listen.h"
#include <SD.h>

static bool led_is_blue(const std::string& e) {
  int r = 0, g = 0, b = 0;
  if (sscanf(e.c_str(), "led:%d,%d,%d", &r, &g, &b) != 3) return false;
  return b > 0 && b > r && b > g;
}

static int btn() {
  static const int pins[] = GS_BUTTON_PINS;
  return pins[0];
}

void setUp(void) { gs_host::reset_all(); }
void tearDown(void) {}

// App actions without the network: the same handler the parent page calls.
static int act(const char* json, std::string* reply = nullptr) {
  JsonDocument in, out;
  deserializeJson(in, json);
  const int st = gs_make_app()->action(in["action"] | "", in.as<JsonObjectConst>(), out.to<JsonObject>(), millis());
  if (reply) serializeJson(out, *reply);
  return st;
}

// Hold, hear the cue, speak (the mic source), let go, hear the clip back, then tap to keep (or not).
static void record_a_name(bool keep) {
  gs_host::mic_tone(1.2f, 9000);
  gs_host::gpio_in[btn()] = LOW;
  gs_host::run_for(700);        // long press: listening light, then the spoken cue is queued
  gs_host::play_queued();       // the cue plays; when it ends the mic opens
  gs_host::pump_task("gs_mic"); // the child speaks for 1.2 s
  gs_host::run_for(20);
  gs_host::gpio_in[btn()] = HIGH;
  gs_host::run_for(80);
  gs_host::play_queued();       // the clip plays back, then "tap to keep"
  gs_host::run_for(50);
  if (keep) {
    gs_host::press_button(btn(), 120);
    gs_host::play_queued();     // "saved"
  } else {
    gs_host::run_for(GS_CFG_KEEP_WINDOW_MS + 200);
    gs_host::play_queued();     // "let's try again"
  }
}

static void test_kept_clip_is_encrypted_and_plays_back() {
  gs_host::story_card(true);
  gs_host::power_up(btn());
  gs_host::i2s.spk.clear();
  record_a_name(true);
  TEST_ASSERT_EQUAL(1, gs_store().mem().count);
  TEST_ASSERT_TRUE(SD.exists("/fav/f001.wav"));
  const auto& bytes = gs_host::sd.files["/fav/f001.wav"];
  TEST_ASSERT_TRUE(bytes.size() > 30000);  // about 1.2 s of 16 kHz 16-bit audio
  TEST_ASSERT_FALSE(gs_host::contains(bytes, "RIFF"));
  TEST_ASSERT_FALSE(gs_host::contains(bytes, "WAVE"));
  TEST_ASSERT_FALSE(SD.exists("/fav/tmp.wav"));
  // The playback decrypted it: the speaker got a tone near the mic's level, not noise or silence.
  int16_t peak = 0;
  for (int16_t v : gs_host::i2s.spk) peak = int16_t(std::max<int>(peak, abs(v)));
  TEST_ASSERT_TRUE(peak > 500);
  TEST_ASSERT_TRUE(gsclips::has_key());
}

static void test_remove_zeroes_and_deletes_the_clip() {
  gs_host::story_card(false);
  gs_host::power_up(btn());
  record_a_name(true);
  TEST_ASSERT_TRUE(SD.exists("/fav/f001.wav"));
  std::string r;
  TEST_ASSERT_EQUAL(200, act("{\"action\":\"fav_remove\",\"id\":1,\"confirm\":true}", &r));
  TEST_ASSERT_FALSE(SD.exists("/fav/f001.wav"));
  TEST_ASSERT_TRUE(gs_host::all_zero(gs_host::sd.removed["/fav/f001.wav"]));
}

static void test_failed_remove_reports_500_and_the_next_mount_finishes() {
  gs_host::story_card(false);
  gs_host::power_up(btn());
  record_a_name(true);
  gs_host::sd.fail_remove.insert("/fav/f001.wav");
  std::string r;
  TEST_ASSERT_EQUAL(500, act("{\"action\":\"fav_remove\",\"id\":1,\"confirm\":true}", &r));
  TEST_ASSERT_TRUE(r.find("could not be deleted") != std::string::npos);
  TEST_ASSERT_EQUAL(1, gs_store().mem().app[gs::kAppPendingWipe]);
  gs_host::sd.fail_remove.clear();
  gs_host::power_up(btn());  // next mount
  TEST_ASSERT_FALSE(SD.exists("/fav/f001.wav"));
  TEST_ASSERT_EQUAL(0, gs_store().mem().app[gs::kAppPendingWipe]);
}

static void test_erase_without_a_card_reports_failure() {
  gs_host::story_card(false);
  gs_host::power_up(btn());
  record_a_name(true);
  gs_host::sd.present = false;
  std::string r;
  TEST_ASSERT_EQUAL(500, act("{\"action\":\"fav_erase_all\",\"confirm\":\"ERASE\"}", &r));
  TEST_ASSERT_TRUE(r.find("insert the card and retry") != std::string::npos);
  TEST_ASSERT_EQUAL(1, gs_store().mem().app[gs::kAppPendingWipe]);
  TEST_ASSERT_EQUAL(0, gs_store().mem().count);
  TEST_ASSERT_FALSE(gsclips::has_key());  // the clip left on the card can no longer be decrypted
  gs_host::sd.present = true;
  gs_host::power_up(btn());
  TEST_ASSERT_FALSE(SD.exists("/fav/f001.wav"));
  TEST_ASSERT_EQUAL(0, gs_store().mem().app[gs::kAppPendingWipe]);
}

static void test_erase_removes_100_clips_zeroed() {
  gs_host::story_card(false);
  gs_host::power_up(btn());
  for (int i = 1; i <= 100; ++i) {
    char p[24];
    snprintf(p, sizeof(p), "/fav/f%03d.wav", i);
    gs_host::put_file(p, std::vector<uint8_t>(1000, uint8_t(i)));
  }
  std::string r;
  TEST_ASSERT_EQUAL(200, act("{\"action\":\"fav_erase_all\",\"confirm\":\"ERASE\"}", &r));
  TEST_ASSERT_TRUE(r.find("\"clips_deleted\":100") != std::string::npos);
  int left = 0;
  for (const auto& f : gs_host::sd.files) left += f.first.rfind("/fav/", 0) == 0;
  TEST_ASSERT_EQUAL(0, left);
  for (const auto& kv : gs_host::sd.removed) TEST_ASSERT_TRUE(gs_host::all_zero(kv.second));
}

static void test_cut_between_save_and_rename_is_repaired() {
  gs_host::story_card(false);
  gs_host::power_up(btn());
  uint8_t id = 0;
  TEST_ASSERT_EQUAL(gs::StoreResult::Ok, gs_store().add_clip(&id));  // committed, then the power went
  TEST_ASSERT_EQUAL(1, gs_store().mem().count);
  gs_host::power_up(btn());
  TEST_ASSERT_EQUAL(0, gs_store().mem().count);  // the dead favourite is gone and its slot is free
  TEST_ASSERT_TRUE(gs_host::serial_out.find("GS-MEMORY {\"repaired\":1}") != std::string::npos);
}

static void test_clips_expire_after_the_retention_time() {
  gs_host::story_card(false);
  gs_host::power_up(btn());
  record_a_name(true);
  TEST_ASSERT_TRUE(SD.exists("/fav/f001.wav"));
  const uint32_t h = GS_CFG_CLIP_RETENTION_HOURS + 1;
  gs_host::nvs["gsnvs/gsmem"]["uphours"] = {uint8_t(h), uint8_t(h >> 8), uint8_t(h >> 16), uint8_t(h >> 24)};
  gs_host::power_up(btn());
  TEST_ASSERT_FALSE(SD.exists("/fav/f001.wav"));
  TEST_ASSERT_EQUAL(0, gs_store().mem().count);
}

static void test_mic_opens_only_under_the_listening_light_including_selftest() {
  gs_host::story_card(false);
  gs_host::power_up(btn());
  std::vector<std::string>& ev = gs_host::ui.events;
  bool flag_ok = true;
  gs_host::i2s.on_rx = [&](bool on) {
    if (on && !gslisten::active()) flag_ok = false;
    ev.push_back(on ? "rx:1" : "rx:0");
  };
  // The factory self-test's microphone check (serial "selftest"): runs on the UI task.
  gs_host::mic_tone(4.0f, 6000);
  gs_host::serial_in = "selftest\n";
  ev.clear();
  gs_host::run_for(10);
  TEST_ASSERT_TRUE(gs_host::serial_out.find("\"id\":\"microphone\"") != std::string::npos);
  int opens = 0;
  for (size_t i = 0; i < ev.size(); ++i) {
    if (ev[i] == "rx:1") {
      ++opens;
      bool led = false, face = false;
      for (size_t k = i; k-- > 0 && ev[k] != "rx:0";) {
        if (led_is_blue(ev[k])) led = true;
        if (ev[k] == "face:listening") face = true;
      }
      TEST_ASSERT_TRUE_MESSAGE(led && face, "mic opened without the listening light and face");
    }
    if (ev[i] == "rx:0") {
      bool cleared = false;
      for (size_t k = i + 1; k < ev.size() && !cleared; ++k)
        if (ev[k].rfind("led:", 0) == 0) cleared = ev[k] == "led:0,0,0";
      TEST_ASSERT_TRUE_MESSAGE(cleared, "light still says listening after the mic closed");
    }
  }
  TEST_ASSERT_EQUAL(2, opens);  // quiet level, then the clap
  TEST_ASSERT_FALSE(gs_host::i2s.rx_enabled);
  // The recording path (from the mic task): the listening flag is up before the channel starts.
  record_a_name(false);
  TEST_ASSERT_TRUE(flag_ok);
  TEST_ASSERT_FALSE(gs_host::i2s.rx_enabled);
  TEST_ASSERT_FALSE(gslisten::active());
}

static void test_each_step_speaks_its_prompt_when_the_card_has_it() {
  gs_host::story_card(true);
  gs_host::power_up(btn());
  gs_host::sd.reads.clear();
  record_a_name(true);
  TEST_ASSERT_TRUE(gs_host::was_read("/prompts/hold_to_tell.wav"));
  TEST_ASSERT_TRUE(gs_host::was_read("/prompts/tap_to_keep.wav"));
  TEST_ASSERT_TRUE(gs_host::was_read("/prompts/saved.wav"));
  gs_host::sd.reads.clear();
  record_a_name(false);
  TEST_ASSERT_TRUE(gs_host::was_read("/prompts/try_again.wav"));
}

static void test_chimes_replace_prompts_the_card_lacks() {
  gs_host::story_card(false);
  gs_host::power_up(btn());
  gs_host::sd.reads.clear();
  gs_host::i2s.spk.clear();
  gs_host::gpio_in[btn()] = LOW;
  gs_host::run_for(700);
  gs_host::play_queued();
  const size_t cue = gs_host::i2s.spk.size();
  TEST_ASSERT_TRUE(cue > 1000);  // a short tone instead of "tell me a name"
  gs_host::gpio_in[btn()] = HIGH;
  gs_host::run_for(100);
  gs_host::play_queued();
  for (const auto& r : gs_host::sd.reads) TEST_ASSERT_TRUE_MESSAGE(r.rfind("/prompts/", 0) != 0, r.c_str());
  TEST_ASSERT_EQUAL(3000, GS_CFG_CLIP_MS);  // MIN-38: 3 s to say a name
}

static void test_card_names_become_favourites_when_the_network_is_off() {
  gs_host::story_card(false, {"Anya", "Kabir"});
  gs_host::power_up(btn());
  TEST_ASSERT_NOT_NULL(gs_store().find_name("Anya"));
  TEST_ASSERT_NOT_NULL(gs_store().find_name("Kabir"));
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_kept_clip_is_encrypted_and_plays_back);
  RUN_TEST(test_remove_zeroes_and_deletes_the_clip);
  RUN_TEST(test_failed_remove_reports_500_and_the_next_mount_finishes);
  RUN_TEST(test_erase_without_a_card_reports_failure);
  RUN_TEST(test_erase_removes_100_clips_zeroed);
  RUN_TEST(test_cut_between_save_and_rename_is_repaired);
  RUN_TEST(test_clips_expire_after_the_retention_time);
  RUN_TEST(test_mic_opens_only_under_the_listening_light_including_selftest);
  RUN_TEST(test_each_step_speaks_its_prompt_when_the_card_has_it);
  RUN_TEST(test_chimes_replace_prompts_the_card_lacks);
  RUN_TEST(test_card_names_become_favourites_when_the_network_is_off);
  return UNITY_END();
}
