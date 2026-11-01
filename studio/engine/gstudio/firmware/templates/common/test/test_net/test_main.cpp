// Shared suite: the real src/gs_net.cpp (setup hotspot, parent PIN, PIN guard, API auth, voice clip
// streaming and the spoken-name upload), with gs_board.cpp, gs_clips.cpp and gs_kv_nvs.cpp, against the
// host shims in test/shims. Runs for every template (MAJ-2, MAJ-33 device side, MIN-18).
#include <unity.h>

#include <string>

#include "config.h"
#include "gs_audio_math.h"
#include "gs_board.h"
#include "gs_clip.h"
#include "gs_clips.h"
#include "gs_fsfile.h"
#include "gs_kv_nvs.h"
#include "gs_net.h"
#include "hardware.h"
#include <SD.h>
#include <WebServer.h>
#include <mbedtls/sha256.h>

// gsota is not under test here: a build without a signing key refuses every update.
namespace gsota {
void begin() {}
void loop(uint32_t, bool) {}
bool pending_verify() { return false; }
const char* state_name() { return "valid"; }
const char* running_partition() { return "app0"; }
bool signing_enabled() { return false; }
bool upload_begin(const char*, String* e) {
  if (e) *e = "no key";
  return false;
}
bool upload_write(const uint8_t*, size_t, String*) { return false; }
bool upload_end(String*) { return false; }
void upload_abort() {}
uint32_t upload_bytes() { return 0; }
}  // namespace gsota

static NvsKv s_kv;
static std::string s_shown_ssid, s_shown_pass;
static std::string s_name_target = "/names/anya.wav";

static void hook_state(JsonObject out) { out["hello"] = 1; }
static int hook_action(const char*, JsonObjectConst, JsonObject out) {
  out["ok"] = false;
  return 404;
}
static String hook_clip(uint8_t id) { return id == 1 ? String("/fav/f001.wav") : String(""); }
static String hook_name_clip(uint8_t id) { return id == 3 ? String(s_name_target.c_str()) : String(""); }
static void hook_show(const char* ssid, const char* pass) {
  s_shown_ssid = ssid;
  s_shown_pass = pass;
}

static WebServer& server() { return *WebServer::instance(); }

static void boot(bool force_setup) {
  server().reset_routes();
  gsnet::Hooks h;
  h.state = hook_state;
  h.action = hook_action;
  h.clip_path = hook_clip;
  h.name_clip_path = hook_name_clip;
  h.show_setup = hook_show;
  gsnet::begin(h, force_setup);
}

void setUp(void) {
  gs_host::reset_all();
  s_shown_ssid.clear();
  s_shown_pass.clear();
  gsboard::begin();
  s_kv.begin();
  gsclips::begin(&s_kv);
}
void tearDown(void) {}

static const std::map<std::string, std::string> pin(const char* p) { return {{"X-GS-PIN", p}}; }

// Opens the hotspot with a button hold, taps the device and saves Wi-Fi + PIN 246813.
static void set_up_device() {
  boot(true);
  gsnet::note_device_tap(millis());
  const HostReply& r = server().request(HTTP_POST, "/api/setup", "{\"ssid\":\"HomeNet\",\"password\":\"pw123456\",\"pin\":\"246813\"}");
  TEST_ASSERT_EQUAL(200, r.status);
  TEST_ASSERT_TRUE(gsnet::pin_set());
}

static void test_sha256_shim_matches_fips_vector() {
  mbedtls_sha256_context c;
  uint8_t out[32];
  mbedtls_sha256_init(&c);
  mbedtls_sha256_starts(&c, 0);
  mbedtls_sha256_update(&c, reinterpret_cast<const uint8_t*>("abc"), 3);
  mbedtls_sha256_finish(&c, out);
  const uint8_t expect[32] = {0xba, 0x78, 0x16, 0xbf, 0x8f, 0x01, 0xcf, 0xea, 0x41, 0x41, 0x40, 0xde, 0x5d, 0xae, 0x22, 0x23,
                              0xb0, 0x03, 0x61, 0xa3, 0x96, 0x17, 0x7a, 0x9c, 0xb4, 0x10, 0xff, 0x61, 0xf2, 0x00, 0x15, 0xad};
  TEST_ASSERT_EQUAL_HEX8_ARRAY(expect, out, 32);
}

static void test_boot_with_no_wifi_does_not_open_a_hotspot() {
  boot(false);
  TEST_ASSERT_EQUAL(0, gs_host::wifi.softap_calls);
  TEST_ASSERT_FALSE(gsnet::setup_open());
  TEST_ASSERT_EQUAL_STRING("offline", gsnet::mode());
  // Nor later: the old 60 s fallback hotspot is gone.
  for (int i = 0; i < 200; ++i) {
    gs_host::now_ms += 1000;
    gsnet::loop(millis());
  }
  TEST_ASSERT_EQUAL(0, gs_host::wifi.softap_calls);
}

static void test_button_hold_opens_wpa2_hotspot_with_device_passphrase() {
  boot(true);
  TEST_ASSERT_EQUAL(1, gs_host::wifi.softap_calls);
  TEST_ASSERT_TRUE(gs_host::wifi.ap_pass.size() >= 8);
  TEST_ASSERT_EQUAL_STRING(gs_host::wifi.ap_pass.c_str(), s_shown_pass.c_str());  // shown on the gadget's screen
  TEST_ASSERT_EQUAL_STRING(gs_host::wifi.ap_ssid.c_str(), s_shown_ssid.c_str());
  const std::string first = gs_host::wifi.ap_pass;
  gs_host::reboot_volatile();
  boot(true);
  TEST_ASSERT_EQUAL_STRING(first.c_str(), gs_host::wifi.ap_pass.c_str());  // per device: the same after a restart
}

static void test_first_pin_needs_a_tap_on_the_device() {
  boot(true);
  const std::string body = "{\"ssid\":\"HomeNet\",\"password\":\"pw123456\",\"pin\":\"246813\"}";
  const HostReply& r = server().request(HTTP_POST, "/api/setup", body);
  TEST_ASSERT_EQUAL(403, r.status);
  TEST_ASSERT_TRUE(r.body.find("need_tap") != std::string::npos);
  TEST_ASSERT_FALSE(gsnet::pin_set());
  gsnet::note_device_tap(millis());
  TEST_ASSERT_EQUAL(200, server().request(HTTP_POST, "/api/setup", body).status);
  TEST_ASSERT_TRUE(gsnet::pin_set());
}

static void test_tap_confirmation_expires() {
  boot(true);
  gsnet::note_device_tap(millis());
  gs_host::now_ms += gsnet::kTapConfirmMs + 1;
  const HostReply& r =
      server().request(HTTP_POST, "/api/setup", "{\"ssid\":\"HomeNet\",\"password\":\"pw\",\"pin\":\"246813\"}");
  TEST_ASSERT_EQUAL(403, r.status);
}

static void test_five_digit_pin_is_rejected() {
  boot(true);
  gsnet::note_device_tap(millis());
  const HostReply& r =
      server().request(HTTP_POST, "/api/setup", "{\"ssid\":\"HomeNet\",\"password\":\"pw\",\"pin\":\"12345\"}");
  TEST_ASSERT_EQUAL(400, r.status);
  TEST_ASSERT_TRUE(r.body.find("6 to 8 digits") != std::string::npos);
  TEST_ASSERT_FALSE(gsnet::pin_set());
}

static void test_state_and_clip_need_the_pin() {
  set_up_device();
  TEST_ASSERT_EQUAL(401, server().request(HTTP_GET, "/api/state").status);
  TEST_ASSERT_EQUAL(401, server().request(HTTP_GET, "/api/clip?id=1").status);
  TEST_ASSERT_EQUAL(401, server().request(HTTP_GET, "/api/state", "", pin("111111")).status);
  const HostReply& ok = server().request(HTTP_GET, "/api/state", "", pin("246813"));
  TEST_ASSERT_EQUAL(200, ok.status);
  TEST_ASSERT_TRUE(ok.body.find("\"hello\":1") != std::string::npos);
}

static void test_lockout_survives_a_restart() {
  set_up_device();
  for (int i = 0; i < 5; ++i) server().request(HTTP_GET, "/api/state", "", pin("000000"));
  TEST_ASSERT_EQUAL(429, server().request(HTTP_GET, "/api/state", "", pin("246813")).status);
  gs_host::reboot_volatile();  // power cycle: millis() restarts at 0
  boot(false);
  TEST_ASSERT_EQUAL(429, server().request(HTTP_GET, "/api/state", "", pin("246813")).status);
  gs_host::now_ms += 61000;
  TEST_ASSERT_EQUAL(200, server().request(HTTP_GET, "/api/state", "", pin("246813")).status);
}

static void test_lockout_is_per_address() {
  set_up_device();
  gs_host::wifi.client_ip = 0x0A000063;  // someone else on the network
  for (int i = 0; i < 5; ++i) server().request(HTTP_GET, "/api/state", "", pin("000000"));
  TEST_ASSERT_EQUAL(429, server().request(HTTP_GET, "/api/state", "", pin("246813")).status);
  gs_host::wifi.client_ip = 0x0A000005;  // the parent's phone is not locked out
  TEST_ASSERT_EQUAL(200, server().request(HTTP_GET, "/api/state", "", pin("246813")).status);
}

#if GS_HAS_SD
static std::string wav_bytes(uint32_t rate, uint16_t channels, size_t samples) {
  std::string w(44 + samples * 2 * channels, '\0');
  gs::wav_header(reinterpret_cast<uint8_t*>(&w[0]), rate, 16, channels, uint32_t(samples * 2 * channels));
  for (size_t i = 0; i < samples * channels; ++i) {
    const int16_t v = int16_t((i * 37) % 2000 - 1000);
    memcpy(&w[44 + 2 * i], &v, 2);
  }
  return w;
}

static void test_name_clip_upload_is_validated_and_atomic() {
  set_up_device();
  const std::string good = wav_bytes(24000, 1, 12000);  // 0.5 s, 24 kB
  TEST_ASSERT_EQUAL(401, server().upload_request("/api/name_clip?id=3", good).status);
  TEST_ASSERT_FALSE(SD.exists("/names/anya.wav"));
  const HostReply& r = server().upload_request("/api/name_clip?id=3", good, pin("246813"));
  TEST_ASSERT_EQUAL(200, r.status);
  TEST_ASSERT_TRUE(SD.exists("/names/anya.wav"));
  TEST_ASSERT_TRUE(gs_host::sd.files["/names/anya.wav"] == std::vector<uint8_t>(good.begin(), good.end()));
  // Oversize, stereo and not-a-WAV are refused, and the stored clip is left exactly as it was.
  const std::string big = wav_bytes(24000, 1, 40000);  // 80 kB
  TEST_ASSERT_EQUAL(413, server().upload_request("/api/name_clip?id=3", big, pin("246813")).status);
  TEST_ASSERT_EQUAL(400, server().upload_request("/api/name_clip?id=3", wav_bytes(24000, 2, 4000), pin("246813")).status);
  TEST_ASSERT_EQUAL(400, server().upload_request("/api/name_clip?id=3", std::string(3000, 'x'), pin("246813")).status);
  TEST_ASSERT_TRUE(gs_host::sd.files["/names/anya.wav"] == std::vector<uint8_t>(good.begin(), good.end()));
  TEST_ASSERT_FALSE(SD.exists("/names/.upload.tmp"));
  TEST_ASSERT_EQUAL(404, server().upload_request("/api/name_clip?id=9", good, pin("246813")).status);
}

static void test_clip_is_served_decrypted_only_with_the_pin() {
  set_up_device();
  uint8_t key[32], nonce[12] = {1};
  TEST_ASSERT_TRUE(gsclips::key(key, true));
  gsboard::sd_mkdirs("/fav");
  {
    File f = SD.open("/fav/f001.wav", FILE_WRITE);
    GsFsFile ff(f);
    gs::ClipWriter w;
    TEST_ASSERT_TRUE(w.begin(&ff, key, nonce, 0, 16000));
    int16_t pcm[160];
    for (int i = 0; i < 160; ++i) pcm[i] = int16_t(i * 50);
    TEST_ASSERT_TRUE(w.write_pcm(pcm, 160));
    TEST_ASSERT_TRUE(w.finish());
    f.close();
  }
  const auto& onc = gs_host::sd.files["/fav/f001.wav"];
  TEST_ASSERT_NOT_EQUAL(0, memcmp(onc.data(), "RIFF", 4));  // not a WAV on the card
  TEST_ASSERT_EQUAL(401, server().request(HTTP_GET, "/api/clip?id=1").status);
  const HostReply& r = server().request(HTTP_GET, "/api/clip?id=1", "", pin("246813"));
  TEST_ASSERT_EQUAL(200, r.status);
  TEST_ASSERT_EQUAL(44 + 320, r.body.size());
  TEST_ASSERT_EQUAL(0, memcmp(r.body.data(), "RIFF", 4));
}

#endif  // GS_HAS_SD

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_sha256_shim_matches_fips_vector);
  RUN_TEST(test_boot_with_no_wifi_does_not_open_a_hotspot);
  RUN_TEST(test_button_hold_opens_wpa2_hotspot_with_device_passphrase);
  RUN_TEST(test_first_pin_needs_a_tap_on_the_device);
  RUN_TEST(test_tap_confirmation_expires);
  RUN_TEST(test_five_digit_pin_is_rejected);
  RUN_TEST(test_state_and_clip_need_the_pin);
  RUN_TEST(test_lockout_survives_a_restart);
  RUN_TEST(test_lockout_is_per_address);
#if GS_HAS_SD  // designs without a card have no clips: those tests are not part of their count
  RUN_TEST(test_name_clip_upload_is_validated_and_atomic);
  RUN_TEST(test_clip_is_served_decrypted_only_with_the_pin);
#endif
  return UNITY_END();
}
