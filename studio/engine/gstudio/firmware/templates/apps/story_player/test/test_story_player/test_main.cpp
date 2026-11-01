// story_player product suite, default mode (parent_web): the story sequencing and the interaction
// controller from lib/gs_core, plus the real firmware (src/main.cpp setup()/loop(), the app, the web
// API, the card, the audio player) running against the host shims (MIN-18, MAJ-1, MAJ-33, MIN-38).
#include <unity.h>

#include "suite_config.h"
#include "gs_app.h"
#include "gs_device.h"
#include "gs_host_card.h"
#include "gs_host_device.h"
#include <SD.h>
#include <WebServer.h>

void register_story_tests();
void register_story_ctl_tests();

static int btn() {
  static const int pins[] = GS_BUTTON_PINS;
  return pins[0];
}
static WebServer& server() { return *WebServer::instance(); }
static const std::map<std::string, std::string> kPin = {{"X-GS-PIN", "246813"}};

void setUp(void) {
  gs_host::reset_all();
  if (WebServer::instance()) server().reset_routes();
}
void tearDown(void) {}

// Plug in holding the button 6 s (opens the setup hotspot), tap the gadget, save Wi-Fi + PIN.
static void set_up_parent_page() {
  gs_host::power_up(btn(), 6000);
  TEST_ASSERT_TRUE(gs_host::wifi.ap_on);
  gs_host::press_button(btn(), 120);  // the tap that confirms the first PIN
  gs_host::play_queued();
  const HostReply& r =
      server().request(HTTP_POST, "/api/setup", "{\"ssid\":\"HomeNet\",\"password\":\"pw123456\",\"pin\":\"246813\"}");
  TEST_ASSERT_EQUAL_MESSAGE(200, r.status, r.body.c_str());
}

static const HostReply& act(const std::string& json) { return server().request(HTTP_POST, "/api/action", json, kPin); }

static int reply_int(const HostReply& r, const char* key) {
  const std::string k = std::string("\"") + key + "\":";
  const size_t p = r.body.find(k);
  return p == std::string::npos ? -1 : atoi(r.body.c_str() + p + k.size());
}

static void test_unconfirmed_take_is_gone_after_begin() {
  gs_host::story_card(false);
  gs_host::put_file("/fav/tmp.wav", gs_host::wav(16000, 800, 9000));
  gs_host::power_up(btn());
  TEST_ASSERT_FALSE(SD.exists("/fav/tmp.wav"));
  TEST_ASSERT_TRUE(gs_host::sd.removed.count("/fav/tmp.wav"));
  TEST_ASSERT_TRUE(gs_host::all_zero(gs_host::sd.removed["/fav/tmp.wav"]));  // zeroed before it was deleted
}

static void test_parent_web_never_opens_the_mic_or_writes_a_clip() {
  gs_host::story_card(true);
  gs_host::mic_tone(3.0f, 8000);
  gs_host::power_up(btn());
  gs_host::press_button(btn(), 2500);  // a long hold: in parent_web mode it records nothing
  gs_host::pump_task("gs_mic");
  gs_host::play_queued();
  gs_host::press_button(btn(), 120);  // a tap: a story
  gs_host::play_queued();
  TEST_ASSERT_EQUAL(0, gs_host::i2s.rx_enables);
  for (const auto& f : gs_host::sd.files) TEST_ASSERT_FALSE_MESSAGE(f.first.rfind("/fav/", 0) == 0, f.first.c_str());
  TEST_ASSERT_TRUE(gs_host::was_read("/stories/mango_tree/s00.wav"));
}

static void test_api_state_and_clip_need_the_pin() {
  gs_host::story_card(false);
  set_up_parent_page();
  TEST_ASSERT_EQUAL(401, server().request(HTTP_GET, "/api/state").status);
  TEST_ASSERT_EQUAL(401, server().request(HTTP_GET, "/api/clip?id=1").status);
  const HostReply& r = server().request(HTTP_GET, "/api/state", "", kPin);
  TEST_ASSERT_EQUAL(200, r.status);
  TEST_ASSERT_TRUE(r.body.find("\"capture_mode\":\"parent_web\"") != std::string::npos);
  TEST_ASSERT_TRUE(r.body.find("records no voice") != std::string::npos);
}

static void test_typed_names_are_spoken_as_hero_and_friend() {
  gs_host::story_card(false);
  set_up_parent_page();
  const HostReply& a = act("{\"action\":\"fav_add\",\"name\":\"Anya\",\"confirm\":true}");
  TEST_ASSERT_EQUAL(200, a.status);
  TEST_ASSERT_TRUE(a.body.find("\"name_audio\":false") != std::string::npos);  // "not spoken yet"
  const int anya = reply_int(a, "id");
  const int kabir = reply_int(act("{\"action\":\"fav_add\",\"name\":\"Kabir\",\"confirm\":true}"), "id");
  TEST_ASSERT_TRUE(anya > 0 && kabir > 0 && anya != kabir);
  const std::vector<uint8_t> clip = gs_host::wav(24000, 6000);
  const std::string body(clip.begin(), clip.end());
  TEST_ASSERT_EQUAL(200, server().upload_request("/api/name_clip?id=" + std::to_string(anya), body, kPin).status);
  TEST_ASSERT_EQUAL(200, server().upload_request("/api/name_clip?id=" + std::to_string(kabir), body, kPin).status);
  const HostReply& st = server().request(HTTP_GET, "/api/state", "", kPin);
  TEST_ASSERT_TRUE(st.body.find("\"name_audio\":false") == std::string::npos);
  gs_host::sd.reads.clear();
  TEST_ASSERT_EQUAL(200, act("{\"action\":\"play_story\",\"id\":\"mango_tree\"}").status);
  gs_host::play_queued();
  TEST_ASSERT_TRUE(gs_host::was_read("/names/anya.wav"));   // the hero ...
  TEST_ASSERT_TRUE(gs_host::was_read("/names/kabir.wav"));  // ... and the friend
  TEST_ASSERT_TRUE(gs_host::was_read("/stories/mango_tree/s03.wav"));
}

static void test_hello_prompt_when_present_else_a_chime() {
  gs_host::story_card(true);
  gs_host::power_up(btn());
  TEST_ASSERT_TRUE(gs_host::was_read("/prompts/hello.wav"));
  gs_host::reset_all();
  gs_host::story_card(false);
  gs_host::power_up(btn());
  TEST_ASSERT_FALSE(gs_host::was_read("/prompts/hello.wav"));
  TEST_ASSERT_TRUE(gs_host::i2s.spk.size() > 1000);  // the ready chime
}

int main(int, char**) {
  UNITY_BEGIN();
  register_story_tests();
  register_story_ctl_tests();
  RUN_TEST(test_unconfirmed_take_is_gone_after_begin);
  RUN_TEST(test_parent_web_never_opens_the_mic_or_writes_a_clip);
  RUN_TEST(test_api_state_and_clip_need_the_pin);
  RUN_TEST(test_typed_names_are_spoken_as_hero_and_friend);
  RUN_TEST(test_hello_prompt_when_present_else_a_chime);
  return UNITY_END();
}
