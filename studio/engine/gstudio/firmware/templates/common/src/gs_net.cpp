#include "gs_net.h"

#include <DNSServer.h>
#include <ESPmDNS.h>
#include <FS.h>
#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>
#include <esp_random.h>
#include <mbedtls/sha256.h>

#include "config.h"
#include "gs_audio_math.h"
#include "gs_board.h"
#include "gs_clips.h"
#include "gs_fsfile.h"
#include "gs_ota.h"
#include "gs_pin.h"
#include "hardware.h"
#include "parent_page.h"

#if GS_HAS_SD
#include <SD.h>
#endif

namespace gsnet {

enum class Mode : uint8_t { Offline, SetupAp, Connecting, Station };

static WebServer s_server(80);
static DNSServer s_dns;
static Preferences s_prefs;
static Hooks s_hooks;
static gs::PinGuardTable s_guard;

// Everything that a restart clears. begin() starts from a fresh copy.
struct State {
  Mode mode = Mode::Offline;
  bool ap_on = false;
  bool dns_on = false;
  bool mdns_on = false;
  uint32_t ap_started = 0;
  uint32_t restart_at = 0;
  bool restart = false;
  String ssid;
  uint8_t pin_salt[16] = {0};
  uint8_t pin_hash[32] = {0};
  bool have_pin = false;
  bool ota_ok = false;
  String ota_error;
  int ota_auth = 0;
  String ap_name;
  String ap_pass;
  bool tap_seen = false;
  uint32_t tap_at = 0;
  // name-clip upload
  int up_auth = 0;
  String up_error;
  int up_status = 0;
  String up_target;
  uint32_t up_bytes = 0;
  File up_file;
};
static State s;

static const char* kHeaders[] = {"X-GS-PIN", "X-GS-Signature"};
static const char* kPassAlphabet = "23456789abcdefghjkmnpqrstuvwxyz";  // no 0/o, 1/l/i: easy to read off a screen
#if GS_HAS_SD
static const char* kNameTmp = "/names/.upload.tmp";
#endif

// ------------------------------------------------------------------ credentials, PIN, guard
static void hash_pin(const char* pin, const uint8_t salt[16], uint8_t out[32]) {
  mbedtls_sha256_context c;
  mbedtls_sha256_init(&c);
  mbedtls_sha256_starts(&c, 0);
  mbedtls_sha256_update(&c, salt, 16);
  mbedtls_sha256_update(&c, reinterpret_cast<const uint8_t*>(pin), strlen(pin));
  mbedtls_sha256_finish(&c, out);
  mbedtls_sha256_free(&c);
}

static void load_creds() {
  s_prefs.begin("gswifi", true);
  s.ssid = s_prefs.getString("ssid", "");
  s.have_pin = s_prefs.getBytes("salt", s.pin_salt, 16) == 16 && s_prefs.getBytes("pinh", s.pin_hash, 32) == 32;
  s.ap_pass = s_prefs.getString("appass", "");
  s_prefs.end();
}

static void ensure_ap_pass() {
  bool ok = s.ap_pass.length() == 8;
  for (unsigned i = 0; ok && i < s.ap_pass.length(); ++i) ok = strchr(kPassAlphabet, s.ap_pass[i]) != nullptr;
  if (ok) return;
  char p[9];
  const size_t n = strlen(kPassAlphabet);
  for (int i = 0; i < 8; ++i) p[i] = kPassAlphabet[esp_random() % n];
  p[8] = '\0';
  s.ap_pass = p;
  s_prefs.begin("gswifi", false);
  s_prefs.putString("appass", s.ap_pass);
  s_prefs.end();
}

static bool save_creds(const String& ssid, const String& pass, const char* new_pin) {
  s_prefs.begin("gswifi", false);
  bool ok = s_prefs.putString("ssid", ssid) == ssid.length() && s_prefs.putString("pass", pass) == pass.length();
  if (ok && new_pin) {
    uint8_t salt[16];
    esp_fill_random(salt, sizeof(salt));
    uint8_t h[32];
    hash_pin(new_pin, salt, h);
    ok = s_prefs.putBytes("salt", salt, 16) == 16 && s_prefs.putBytes("pinh", h, 32) == 32;
    if (ok) {
      memcpy(s.pin_salt, salt, 16);
      memcpy(s.pin_hash, h, 32);
      s.have_pin = true;
    }
  }
  s_prefs.end();
  if (ok) s.ssid = ssid;
  return ok;
}

static String saved_pass() {
  s_prefs.begin("gswifi", true);
  String p = s_prefs.getString("pass", "");
  s_prefs.end();
  return p;
}

static bool pin_matches(const String& pin) {
  // A PIN saved by an older firmware (4 or 5 digits) still unlocks; new PINs need 6 (see handle_setup).
  if (!s.have_pin || !gs::pin_format_ok(pin.c_str(), gs::kPinLegacyMinDigits)) return false;
  uint8_t h[32];
  hash_pin(pin.c_str(), s.pin_salt, h);
  return gs::const_time_equal(h, s.pin_hash, 32);
}

static void save_guard() {
  uint8_t blob[gs::PinGuardTable::kBlobBytes];
  const size_t n = s_guard.save(blob, sizeof(blob), millis());
  if (!n) return;
  s_prefs.begin("gsguard", false);
  s_prefs.putBytes("t", blob, n);
  s_prefs.end();
  s_guard.clear_dirty();
}

static void load_guard() {
  s_guard = gs::PinGuardTable();
  uint8_t blob[gs::PinGuardTable::kBlobBytes];
  s_prefs.begin("gsguard", true);
  const size_t n = s_prefs.getBytes("t", blob, sizeof(blob));
  s_prefs.end();
  if (n) s_guard.load(blob, n, millis());
}

static uint32_t client_ip() { return uint32_t(s_server.client().remoteIP()); }

// 0 = the PIN is right; else the HTTP status to send (403 no PIN yet, 429 locked, 401 wrong).
static int pin_check() {
  const uint32_t now = millis();
  const uint32_t ip = client_ip();
  if (!s.have_pin) return 403;
  if (s_guard.locked(ip, now)) return 429;
  if (!pin_matches(s_server.header("X-GS-PIN"))) {
    s_guard.failure(ip, now);
    save_guard();  // a restart must not reset the count or lift a lock
    return 401;
  }
  s_guard.success(ip);
  if (s_guard.dirty()) save_guard();
  return 0;
}

// ------------------------------------------------------------------ helpers
static void send_json(int status, JsonDocument& doc) {
  String body;
  serializeJson(doc, body);
  s_server.sendHeader("Cache-Control", "no-store");
  s_server.send(status, "application/json", body);
}

static void send_error(int status, const char* message) {
  JsonDocument doc;
  doc["ok"] = false;
  doc["message"] = message;
  send_json(status, doc);
}

static void send_auth_error(int code) {
  if (code == 403) {
    send_error(403, "Finish the Wi-Fi setup and choose a parent PIN first.");
  } else if (code == 429) {
    JsonDocument doc;
    doc["ok"] = false;
    doc["message"] = "Too many wrong PINs. Wait, then try again.";
    doc["retry_after_s"] = (s_guard.retry_after_ms(client_ip(), millis()) + 999) / 1000;
    send_json(429, doc);
  } else {
    send_error(401, "Wrong parent PIN.");
  }
}

// Checks the parent PIN. Sends the error reply itself and returns false when it fails.
static bool authorised() {
  const int code = pin_check();
  if (code) send_auth_error(code);
  return code == 0;
}

static bool tap_confirmed(uint32_t now) { return s.tap_seen && now - s.tap_at < kTapConfirmMs; }

const char* mode() {
  switch (s.mode) {
    case Mode::SetupAp: return "setup_hotspot";
    case Mode::Connecting: return "connecting";
    case Mode::Station: return "home_wifi";
    default: return "offline";
  }
}

String ip() {
  if (WiFi.status() == WL_CONNECTED) return WiFi.localIP().toString();
  if (s.ap_on) return WiFi.softAPIP().toString();
  return "";
}

String host() { return String(GS_DEVICE_SLUG) + ".local"; }
bool pin_set() { return s.have_pin; }
bool wifi_saved() { return s.ssid.length() > 0; }
bool setup_open() { return s.ap_on; }
bool restart_requested() { return s.restart; }

void note_device_tap(uint32_t now) {
  if (!s.ap_on) return;  // a tap only confirms setup while the setup hotspot is open
  s.tap_seen = true;
  s.tap_at = now;
}

void fill_state(JsonObject out) {
  out["mode"] = mode();
  out["ssid"] = s.ssid;
  out["ip"] = ip();
  out["host"] = host();
  out["rssi"] = WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0;
  out["setup_hotspot"] = s.ap_on ? s.ap_name : String("");
}

// ------------------------------------------------------------------ Wi-Fi modes
static void start_ap() {
  if (s.ap_on) return;
  uint8_t mac[6];
  WiFi.macAddress(mac);
  char suffix[8];
  snprintf(suffix, sizeof(suffix), "%02X%02X", mac[4], mac[5]);
  s.ap_name = String(GS_AP_PREFIX) + "-setup-" + suffix;
  ensure_ap_pass();
  WiFi.mode(s.ssid.length() ? WIFI_AP_STA : WIFI_AP);
  // WPA2-PSK with the per-device passphrase; at most 2 phones at a time.
  WiFi.softAP(s.ap_name.c_str(), s.ap_pass.c_str(), 6, 0, 2);
  s_dns.setErrorReplyCode(DNSReplyCode::NoError);
  s.dns_on = s_dns.start(53, "*", WiFi.softAPIP());
  s.ap_on = true;
  s.ap_started = millis();
  s.tap_seen = false;
  if (s.mode != Mode::Station) s.mode = Mode::SetupAp;
  if (s_hooks.show_setup) s_hooks.show_setup(s.ap_name.c_str(), s.ap_pass.c_str());
  Serial.printf("GS-NET {\"event\":\"setup_hotspot\",\"ssid\":\"%s\",\"passphrase\":\"%s\",\"ip\":\"%s\"}\n",
                s.ap_name.c_str(), s.ap_pass.c_str(), WiFi.softAPIP().toString().c_str());
}

static void stop_ap() {
  if (!s.ap_on) return;
  if (s.dns_on) s_dns.stop();
  s.dns_on = false;
  WiFi.softAPdisconnect(true);
  s.ap_on = false;
  s.tap_seen = false;
  WiFi.mode(s.ssid.length() ? WIFI_STA : WIFI_OFF);
  if (s_hooks.show_setup) s_hooks.show_setup("", "");
}

static void start_sta() {
  if (!s.ssid.length()) return;
  WiFi.mode(s.ap_on ? WIFI_AP_STA : WIFI_STA);
  WiFi.setHostname(GS_DEVICE_SLUG);
  WiFi.setAutoReconnect(true);
  WiFi.begin(s.ssid.c_str(), saved_pass().c_str());
  if (s.mode != Mode::SetupAp) s.mode = Mode::Connecting;
}

bool forget_wifi() {
  s_prefs.begin("gswifi", false);
  const bool ok = s_prefs.remove("ssid") && s_prefs.remove("pass");
  s_prefs.end();
  s.ssid = "";
  WiFi.disconnect(true, true);
  return ok;
}

// ------------------------------------------------------------------ handlers
static void handle_page() {
  if (s.ap_on && s_server.hostHeader() != WiFi.softAPIP().toString() && s.mode == Mode::SetupAp &&
      !s_server.hostHeader().endsWith(".local")) {
    s_server.sendHeader("Location", String("http://") + WiFi.softAPIP().toString() + "/", true);
    s_server.send(302, "text/plain", "");
    return;
  }
  s_server.sendHeader("Cache-Control", "no-store");
  s_server.send_P(200, "text/html; charset=utf-8", GS_PARENT_PAGE);
}

static void handle_captive() {
  if (!s.ap_on) {
    send_error(404, "Not found.");
    return;
  }
  s_server.sendHeader("Location", String("http://") + WiFi.softAPIP().toString() + "/", true);
  s_server.send(302, "text/plain", "");
}

static void handle_info() {
  JsonDocument doc;
  doc["name"] = GS_DEVICE_NAME;
  doc["slug"] = GS_DEVICE_SLUG;
  doc["template"] = GS_TEMPLATE;
  doc["firmware"] = GS_FW_VERSION;
  doc["setup"] = s.ap_on;
  doc["wifi_saved"] = wifi_saved();
  doc["pin_set"] = s.have_pin;
  doc["pin_digits_min"] = gs::kPinMinDigits;
  doc["pin_digits_max"] = gs::kPinMaxDigits;
  doc["first_pin_needs_device_tap"] = !s.have_pin;
  doc["mode"] = mode();
  doc["ip"] = ip();
  doc["host"] = host();
  doc["child_directed"] = GS_CHILD_DIRECTED ? true : false;
  send_json(200, doc);
}

static void handle_scan() {
  if (!s.ap_on && !authorised()) return;
  const int n = WiFi.scanNetworks(false, false);
  JsonDocument doc;
  JsonArray arr = doc["networks"].to<JsonArray>();
  for (int i = 0; i < n && i < 20; ++i) {
    JsonObject o = arr.add<JsonObject>();
    o["ssid"] = WiFi.SSID(i);
    o["rssi"] = WiFi.RSSI(i);
    o["secure"] = WiFi.encryptionType(i) != WIFI_AUTH_OPEN;
  }
  WiFi.scanDelete();
  doc["ok"] = true;
  send_json(200, doc);
}

static void handle_setup() {
  JsonDocument in;
  if (deserializeJson(in, s_server.arg("plain")) != DeserializationError::Ok) {
    send_error(400, "Send JSON: {ssid, password, pin}.");
    return;
  }
  if (s.have_pin) {
    // Changing Wi-Fi on a device that already has a PIN needs that PIN (or the network reset).
    if (!authorised()) return;
  } else if (!tap_confirmed(millis())) {
    // The first PIN claims the device: the person must be at the gadget and press its button.
    JsonDocument doc;
    doc["ok"] = false;
    doc["need_tap"] = true;
    doc["message"] = "Press the button on the gadget once, then press Save again.";
    send_json(403, doc);
    return;
  }
  const String ssid = in["ssid"] | "";
  const String pass = in["password"] | "";
  const String pin = in["pin"] | "";
  if (ssid.length() < 1 || ssid.length() > 32) {
    send_error(400, "Choose your home Wi-Fi network (1 to 32 characters).");
    return;
  }
  if (pass.length() > 63) {
    send_error(400, "The Wi-Fi password is too long (63 characters at most).");
    return;
  }
  const bool new_pin = pin.length() > 0;
  if (!s.have_pin && !new_pin) {
    send_error(400, "Choose a parent PIN of 6 to 8 digits.");
    return;
  }
  if (new_pin && !gs::pin_format_ok(pin.c_str())) {
    send_error(400, "The parent PIN must be 6 to 8 digits.");
    return;
  }
  if (!save_creds(ssid, pass, new_pin ? pin.c_str() : nullptr)) {
    send_error(500, "Could not save the settings. Try again.");
    return;
  }
  s.tap_seen = false;
  start_sta();
  JsonDocument doc;
  doc["ok"] = true;
  doc["message"] = String("Saved. Joining ") + ssid + ". Then open http://" + host() + " on your home Wi-Fi.";
  send_json(200, doc);
}

static void handle_state() {
  if (!authorised()) return;
  JsonDocument doc;
  JsonObject root = doc.to<JsonObject>();
  if (s_hooks.state) s_hooks.state(root);
  send_json(200, doc);
}

static void handle_action() {
  if (!authorised()) return;
  JsonDocument in;
  if (deserializeJson(in, s_server.arg("plain")) != DeserializationError::Ok) {
    send_error(400, "Send JSON: {\"action\": \"...\"}.");
    return;
  }
  const char* name = in["action"] | "";
  JsonDocument out;
  JsonObject o = out.to<JsonObject>();
  int status = 404;
  if (strcmp(name, "forget_wifi") == 0) {
    if (!(in["confirm"] | false)) {
      status = 400;
      o["ok"] = false;
      o["message"] = "Confirm to forget the home Wi-Fi.";
    } else {
      forget_wifi();
      s.restart = true;
      s.restart_at = millis() + 1500;
      status = 200;
      o["ok"] = true;
      o["message"] = "Wi-Fi forgotten. Restarting. Hold the button for 5 s while plugging in to set up again.";
    }
  } else if (strcmp(name, "restart") == 0) {
    s.restart = true;
    s.restart_at = millis() + 1000;
    status = 200;
    o["ok"] = true;
    o["message"] = "Restarting.";
  } else if (s_hooks.action) {
    status = s_hooks.action(name, in.as<JsonObjectConst>(), o);
  }
  send_json(status, out);
}

static void handle_clip() {
  if (!authorised()) return;
#if GS_HAS_SD
  const long id = s_server.arg("id").toInt();
  const String path = (id > 0 && id < 256 && s_hooks.clip_path) ? s_hooks.clip_path(uint8_t(id)) : String("");
  if (!path.length() || !gsboard::sd_mounted()) {
    send_error(404, "No voice clip for that favourite.");
    return;
  }
  File f = SD.open(path, FILE_READ);
  if (!f) {
    send_error(404, "The voice clip file is missing from the card.");
    return;
  }
  // Clips are encrypted on the card; the parent hears them decrypted, only over this PIN-checked call.
  GsFsFile ff(f);
  gs::ClipReader rd;
  uint8_t key[gs::kClipKeyBytes];
  const bool have_key = gsclips::key(key, false);
  const bool opened = rd.open(&ff, have_key ? key : nullptr);
  memset(key, 0, sizeof(key));
  if (!opened) {
    f.close();
    send_error(404, "That voice clip cannot be read on this device.");
    return;
  }
  s_server.sendHeader("Cache-Control", "no-store");
  s_server.setContentLength(rd.plain_size());
  s_server.send(200, "audio/wav", "");
  uint8_t buf[512];
  size_t n;
  while ((n = rd.read(buf, sizeof(buf))) > 0) s_server.sendContent(reinterpret_cast<const char*>(buf), n);
  f.close();
#else
  send_error(404, "This design has no microSD card.");
#endif
}

// POST /api/name_clip?id=N, multipart field with a 16-bit mono WAV of at most 64 KB (MAJ-33): the
// spoken clip for a typed favourite name, stored as /names/<slug>.wav. Written to a temporary file,
// checked with the same wav_parse the player uses, then renamed into place.
static void handle_name_clip_upload() {
#if GS_HAS_SD
  HTTPUpload& up = s_server.upload();
  if (up.status == UPLOAD_FILE_START) {
    s.up_error = "";
    s.up_status = 0;
    s.up_bytes = 0;
    s.up_target = "";
    s.up_auth = pin_check();
    if (s.up_auth) return;
    const long id = s_server.arg("id").toInt();
    s.up_target = (id > 0 && id < 256 && s_hooks.name_clip_path) ? s_hooks.name_clip_path(uint8_t(id)) : String("");
    if (!s.up_target.length()) {
      s.up_status = 404;
      s.up_error = "No favourite with a typed name has that id.";
      return;
    }
    if (!gsboard::sd_mounted()) {
      s.up_status = 503;
      s.up_error = "No microSD card. Insert the story card and restart.";
      return;
    }
    gsboard::sd_mkdirs("/names");
    gsboard::sd_remove(kNameTmp);
    s.up_file = SD.open(kNameTmp, FILE_WRITE);
    if (!s.up_file) {
      s.up_status = 500;
      s.up_error = "Could not write to the card.";
    }
  } else if (up.status == UPLOAD_FILE_WRITE) {
    if (s.up_auth || s.up_status) return;
    if (s.up_bytes + up.currentSize > kNameClipMaxBytes) {
      s.up_status = 413;
      s.up_error = "The name clip is too large (64 KB at most).";
      s.up_file.close();
      return;
    }
    if (s.up_file.write(up.buf, up.currentSize) != up.currentSize) {
      s.up_status = 500;
      s.up_error = "Could not write to the card.";
      s.up_file.close();
      return;
    }
    s.up_bytes += uint32_t(up.currentSize);
  } else if (up.status == UPLOAD_FILE_END) {
    if (s.up_file) s.up_file.close();
  } else if (up.status == UPLOAD_FILE_ABORTED) {
    if (s.up_file) s.up_file.close();
    s.up_status = 400;
    s.up_error = "Upload interrupted. Nothing changed.";
  }
#endif
}

static void handle_name_clip_done() {
#if GS_HAS_SD
  if (s.up_auth) {
    send_auth_error(s.up_auth);
    return;
  }
  if (s.up_status) {
    gsboard::sd_remove(kNameTmp);
    send_error(s.up_status, s.up_error.c_str());
    return;
  }
  bool valid = false;
  File f = SD.open(kNameTmp, FILE_READ);
  if (f) {
    uint8_t head[512];
    const size_t got = f.read(head, sizeof(head));
    gs::WavInfo wi;
    valid = gs::wav_parse(head, got, uint32_t(f.size()), &wi) && wi.channels == 1 && wi.bits == 16 &&
            wi.data_bytes > 0 && wi.sample_rate >= 8000 && wi.sample_rate <= 48000 &&
            wi.data_offset + wi.data_bytes <= f.size();
    f.close();
  }
  if (!valid) {
    gsboard::sd_remove(kNameTmp);
    send_error(400, "Send a 16-bit mono WAV (8 to 48 kHz) of 64 KB or less.");
    return;
  }
  if (!gsboard::sd_rename(kNameTmp, s.up_target.c_str())) {
    gsboard::sd_remove(kNameTmp);
    send_error(500, "Could not save the name clip on the card.");
    return;
  }
  JsonDocument doc;
  doc["ok"] = true;
  doc["message"] = "Spoken name saved.";
  doc["file"] = s.up_target;
  doc["bytes"] = s.up_bytes;
  doc["name_audio"] = true;
  send_json(200, doc);
#else
  send_error(404, "This design has no microSD card.");
#endif
}

static void handle_ota_upload() {
  HTTPUpload& up = s_server.upload();
  if (up.status == UPLOAD_FILE_START) {
    s.ota_ok = false;
    s.ota_error = "";
    s.ota_auth = pin_check();
    if (s.ota_auth) return;
    gsota::upload_begin(s_server.header("X-GS-Signature").c_str(), &s.ota_error);
  } else if (up.status == UPLOAD_FILE_WRITE) {
    if (!s.ota_auth && s.ota_error.length() == 0) gsota::upload_write(up.buf, up.currentSize, &s.ota_error);
  } else if (up.status == UPLOAD_FILE_END) {
    if (!s.ota_auth && s.ota_error.length() == 0) s.ota_ok = gsota::upload_end(&s.ota_error);
  } else if (up.status == UPLOAD_FILE_ABORTED) {
    gsota::upload_abort();
    s.ota_error = "Upload interrupted. Nothing changed.";
  }
}

static void handle_ota_done() {
  if (s.ota_auth) {
    send_auth_error(s.ota_auth);
    return;
  }
  if (!s.ota_ok) {
    gsota::upload_abort();
    send_error(400, s.ota_error.length() ? s.ota_error.c_str() : "No firmware file received.");
    return;
  }
  JsonDocument doc;
  doc["ok"] = true;
  doc["message"] = "Update installed and verified. Restarting; the old version comes back if the new one fails its checks.";
  doc["bytes"] = gsota::upload_bytes();
  send_json(200, doc);
  s.restart = true;
  s.restart_at = millis() + 1500;
}

static void handle_not_found() {
  if (s.ap_on) {
    handle_captive();
    return;
  }
  send_error(404, "Not found.");
}

// ------------------------------------------------------------------ lifecycle
void begin(const Hooks& hooks, bool force_setup) {
  s = State();
  s_hooks = hooks;
  load_creds();
  load_guard();
  WiFi.persistent(false);  // credentials live in our own namespace, not duplicated by the Wi-Fi driver
  WiFi.setHostname(GS_DEVICE_SLUG);
  // The setup hotspot opens only on a 5 s button hold at power-up (force_setup), never by itself.
  if (force_setup) start_ap();
  if (s.ssid.length() && s.have_pin) {
    start_sta();
  } else if (!force_setup) {
    WiFi.mode(WIFI_OFF);
    s.mode = Mode::Offline;
    Serial.println("GS-NET {\"event\":\"not_set_up\",\"hint\":\"hold the button for 5 s while plugging in to open the setup hotspot\"}");
  }
  s_server.collectHeaders(kHeaders, 2);
  s_server.on("/", HTTP_GET, handle_page);
  s_server.on("/api/info", HTTP_GET, handle_info);
  s_server.on("/api/scan", HTTP_GET, handle_scan);
  s_server.on("/api/setup", HTTP_POST, handle_setup);
  s_server.on("/api/state", HTTP_GET, handle_state);
  s_server.on("/api/action", HTTP_POST, handle_action);
  s_server.on("/api/clip", HTTP_GET, handle_clip);
  s_server.on("/api/name_clip", HTTP_POST, handle_name_clip_done, handle_name_clip_upload);
  s_server.on("/api/ota", HTTP_POST, handle_ota_done, handle_ota_upload);
  const char* probes[] = {"/generate_204", "/gen_204", "/hotspot-detect.html", "/connecttest.txt", "/ncsi.txt",
                          "/fwlink", "/redirect", "/canonical.html", "/success.txt"};
  for (const char* p : probes) s_server.on(p, HTTP_ANY, handle_captive);
  s_server.onNotFound(handle_not_found);
  s_server.begin();
}

void loop(uint32_t now) {
  if (s.dns_on) s_dns.processNextRequest();
  s_server.handleClient();
  const bool connected = WiFi.status() == WL_CONNECTED;
  if (connected && s.mode != Mode::Station) {
    s.mode = Mode::Station;
    if (!s.mdns_on) {
      s.mdns_on = MDNS.begin(GS_DEVICE_SLUG);
      if (s.mdns_on) {
        MDNS.addService("http", "tcp", 80);
        MDNS.addServiceTxt("http", "tcp", "template", GS_TEMPLATE);
      }
    }
    Serial.printf("GS-NET {\"event\":\"joined\",\"ip\":\"%s\",\"host\":\"%s\"}\n", WiFi.localIP().toString().c_str(),
                  host().c_str());
    // Keep the setup hotspot a minute so the setup page can show the new address, then close it.
    if (s.ap_on) s.ap_started = now - (uint32_t(GS_SETUP_AP_MINUTES) * 60000u) + 60000u;
  }
  if (!connected && s.mode == Mode::Station) s.mode = Mode::Connecting;
  // Close the hotspot after its window if nobody is connected to it. It never reopens by itself.
  if (s.ap_on && now - s.ap_started > uint32_t(GS_SETUP_AP_MINUTES) * 60000u && WiFi.softAPgetStationNum() == 0) {
    stop_ap();
    if (!connected) s.mode = s.ssid.length() ? Mode::Connecting : Mode::Offline;
    Serial.println("GS-NET {\"event\":\"setup_hotspot_closed\"}");
  }
  if (s.restart && static_cast<int32_t>(now - s.restart_at) >= 0) {
    delay(100);
    ESP.restart();
  }
}

}  // namespace gsnet
