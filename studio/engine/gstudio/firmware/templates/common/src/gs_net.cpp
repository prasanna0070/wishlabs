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
#include "gs_board.h"
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
static Mode s_mode = Mode::Offline;
static bool s_ap_on = false;
static bool s_dns_on = false;
static bool s_mdns_on = false;
static uint32_t s_ap_started = 0;
static uint32_t s_connect_started = 0;
static uint32_t s_restart_at = 0;
static bool s_restart = false;
static gs::PinGuard s_guard;
static String s_ssid;
static uint8_t s_pin_salt[16];
static uint8_t s_pin_hash[32];
static bool s_have_pin = false;
static bool s_ota_ok = false;
static String s_ota_error;
static bool s_ota_authed = false;
static String s_ap_name;
static bool s_fallback_done = false;

static const char* kHeaders[] = {"X-GS-PIN", "X-GS-Signature"};

// ------------------------------------------------------------------ credentials and PIN
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
  s_ssid = s_prefs.getString("ssid", "");
  s_have_pin = s_prefs.getBytes("salt", s_pin_salt, 16) == 16 && s_prefs.getBytes("pinh", s_pin_hash, 32) == 32;
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
      memcpy(s_pin_salt, salt, 16);
      memcpy(s_pin_hash, h, 32);
      s_have_pin = true;
    }
  }
  s_prefs.end();
  if (ok) s_ssid = ssid;
  return ok;
}

static String saved_pass() {
  s_prefs.begin("gswifi", true);
  String p = s_prefs.getString("pass", "");
  s_prefs.end();
  return p;
}

static bool pin_matches(const String& pin) {
  if (!s_have_pin || !gs::pin_format_ok(pin.c_str())) return false;
  uint8_t h[32];
  hash_pin(pin.c_str(), s_pin_salt, h);
  return gs::const_time_equal(h, s_pin_hash, 32);
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

// Checks the parent PIN. Sends the error reply itself and returns false when it fails.
static bool authorised() {
  const uint32_t now = millis();
  if (!s_have_pin) {
    send_error(403, "Finish the Wi-Fi setup and choose a parent PIN first.");
    return false;
  }
  if (s_guard.locked(now)) {
    JsonDocument doc;
    doc["ok"] = false;
    doc["message"] = "Too many wrong PINs. Wait, then try again.";
    doc["retry_after_s"] = (s_guard.retry_after_ms(now) + 999) / 1000;
    send_json(429, doc);
    return false;
  }
  if (!pin_matches(s_server.header("X-GS-PIN"))) {
    s_guard.failure(now);
    send_error(401, "Wrong parent PIN.");
    return false;
  }
  s_guard.success();
  return true;
}

const char* mode() {
  switch (s_mode) {
    case Mode::SetupAp: return "setup_hotspot";
    case Mode::Connecting: return "connecting";
    case Mode::Station: return "home_wifi";
    default: return "offline";
  }
}

String ip() {
  if (WiFi.status() == WL_CONNECTED) return WiFi.localIP().toString();
  if (s_ap_on) return WiFi.softAPIP().toString();
  return "";
}

String host() { return String(GS_DEVICE_SLUG) + ".local"; }
bool pin_set() { return s_have_pin; }
bool wifi_saved() { return s_ssid.length() > 0; }
bool restart_requested() { return s_restart; }

void fill_state(JsonObject out) {
  out["mode"] = mode();
  out["ssid"] = s_ssid;
  out["ip"] = ip();
  out["host"] = host();
  out["rssi"] = WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0;
  out["setup_hotspot"] = s_ap_on ? s_ap_name : String("");
}

// ------------------------------------------------------------------ Wi-Fi modes
static void start_ap() {
  if (s_ap_on) return;
  uint8_t mac[6];
  WiFi.macAddress(mac);
  char suffix[8];
  snprintf(suffix, sizeof(suffix), "%02X%02X", mac[4], mac[5]);
  s_ap_name = String(GS_AP_PREFIX) + "-setup-" + suffix;
  WiFi.mode(s_ssid.length() ? WIFI_AP_STA : WIFI_AP);
  WiFi.softAP(s_ap_name.c_str(), nullptr, 6, 0, 2);
  s_dns.setErrorReplyCode(DNSReplyCode::NoError);
  s_dns_on = s_dns.start(53, "*", WiFi.softAPIP());
  s_ap_on = true;
  s_ap_started = millis();
  if (s_mode != Mode::Station) s_mode = Mode::SetupAp;
  Serial.printf("GS-NET {\"event\":\"setup_hotspot\",\"ssid\":\"%s\",\"ip\":\"%s\"}\n", s_ap_name.c_str(),
                WiFi.softAPIP().toString().c_str());
}

static void stop_ap() {
  if (!s_ap_on) return;
  if (s_dns_on) s_dns.stop();
  s_dns_on = false;
  WiFi.softAPdisconnect(true);
  s_ap_on = false;
  WiFi.mode(s_ssid.length() ? WIFI_STA : WIFI_OFF);
}

static void start_sta() {
  if (!s_ssid.length()) return;
  WiFi.mode(s_ap_on ? WIFI_AP_STA : WIFI_STA);
  WiFi.setHostname(GS_DEVICE_SLUG);
  WiFi.setAutoReconnect(true);
  WiFi.begin(s_ssid.c_str(), saved_pass().c_str());
  s_connect_started = millis();
  if (s_mode != Mode::SetupAp) s_mode = Mode::Connecting;
}

bool forget_wifi() {
  s_prefs.begin("gswifi", false);
  const bool ok = s_prefs.remove("ssid") && s_prefs.remove("pass");
  s_prefs.end();
  s_ssid = "";
  WiFi.disconnect(true, true);
  return ok;
}

// ------------------------------------------------------------------ handlers
static void handle_page() {
  if (s_ap_on && s_server.hostHeader() != WiFi.softAPIP().toString() && s_mode == Mode::SetupAp &&
      !s_server.hostHeader().endsWith(".local")) {
    s_server.sendHeader("Location", String("http://") + WiFi.softAPIP().toString() + "/", true);
    s_server.send(302, "text/plain", "");
    return;
  }
  s_server.sendHeader("Cache-Control", "no-store");
  s_server.send_P(200, "text/html; charset=utf-8", GS_PARENT_PAGE);
}

static void handle_captive() {
  if (!s_ap_on) {
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
  doc["setup"] = s_ap_on;
  doc["wifi_saved"] = wifi_saved();
  doc["pin_set"] = s_have_pin;
  doc["mode"] = mode();
  doc["ip"] = ip();
  doc["host"] = host();
  doc["child_directed"] = GS_CHILD_DIRECTED ? true : false;
  send_json(200, doc);
}

static void handle_scan() {
  if (!s_ap_on && !authorised()) return;
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
  // Changing Wi-Fi on a device that already has a PIN needs that PIN (or the network reset).
  if (s_have_pin && !authorised()) return;
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
  if (!s_have_pin && !new_pin) {
    send_error(400, "Choose a parent PIN of 4 to 8 digits.");
    return;
  }
  if (new_pin && !gs::pin_format_ok(pin.c_str())) {
    send_error(400, "The parent PIN must be 4 to 8 digits.");
    return;
  }
  if (!save_creds(ssid, pass, new_pin ? pin.c_str() : nullptr)) {
    send_error(500, "Could not save the settings. Try again.");
    return;
  }
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
  s_hooks.state(root);
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
      s_restart = true;
      s_restart_at = millis() + 1500;
      status = 200;
      o["ok"] = true;
      o["message"] = "Wi-Fi forgotten. Restarting into the setup hotspot.";
    }
  } else if (strcmp(name, "restart") == 0) {
    s_restart = true;
    s_restart_at = millis() + 1000;
    status = 200;
    o["ok"] = true;
    o["message"] = "Restarting.";
  } else {
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
  s_server.sendHeader("Cache-Control", "no-store");
  s_server.streamFile(f, "audio/wav");
  f.close();
#else
  send_error(404, "This design has no microSD card.");
#endif
}

static void handle_ota_upload() {
  HTTPUpload& up = s_server.upload();
  if (up.status == UPLOAD_FILE_START) {
    s_ota_ok = false;
    s_ota_error = "";
    s_ota_authed = s_have_pin && !s_guard.locked(millis()) && pin_matches(s_server.header("X-GS-PIN"));
    if (!s_ota_authed) {
      s_guard.failure(millis());
      s_ota_error = "Wrong parent PIN.";
      return;
    }
    gsota::upload_begin(s_server.header("X-GS-Signature").c_str(), &s_ota_error);
  } else if (up.status == UPLOAD_FILE_WRITE) {
    if (s_ota_authed && s_ota_error.length() == 0) gsota::upload_write(up.buf, up.currentSize, &s_ota_error);
  } else if (up.status == UPLOAD_FILE_END) {
    if (s_ota_authed && s_ota_error.length() == 0) s_ota_ok = gsota::upload_end(&s_ota_error);
  } else if (up.status == UPLOAD_FILE_ABORTED) {
    gsota::upload_abort();
    s_ota_error = "Upload interrupted. Nothing changed.";
  }
}

static void handle_ota_done() {
  if (!s_ota_authed) {
    send_error(401, s_ota_error.length() ? s_ota_error.c_str() : "Wrong parent PIN.");
    return;
  }
  if (!s_ota_ok) {
    gsota::upload_abort();
    send_error(400, s_ota_error.length() ? s_ota_error.c_str() : "No firmware file received.");
    return;
  }
  JsonDocument doc;
  doc["ok"] = true;
  doc["message"] = "Update installed and verified. Restarting; the old version comes back if the new one fails its checks.";
  doc["bytes"] = gsota::upload_bytes();
  send_json(200, doc);
  s_restart = true;
  s_restart_at = millis() + 1500;
}

static void handle_not_found() {
  if (s_ap_on) {
    handle_captive();
    return;
  }
  send_error(404, "Not found.");
}

// ------------------------------------------------------------------ lifecycle
void begin(const Hooks& hooks, bool force_setup) {
  s_hooks = hooks;
  load_creds();
  WiFi.persistent(false);  // credentials live in our own namespace, not duplicated by the Wi-Fi driver
  WiFi.setHostname(GS_DEVICE_SLUG);
  if (force_setup || !s_ssid.length() || !s_have_pin) {
    start_ap();
    if (s_ssid.length() && s_have_pin) start_sta();
  } else {
    start_sta();
  }
  s_server.collectHeaders(kHeaders, 2);
  s_server.on("/", HTTP_GET, handle_page);
  s_server.on("/api/info", HTTP_GET, handle_info);
  s_server.on("/api/scan", HTTP_GET, handle_scan);
  s_server.on("/api/setup", HTTP_POST, handle_setup);
  s_server.on("/api/state", HTTP_GET, handle_state);
  s_server.on("/api/action", HTTP_POST, handle_action);
  s_server.on("/api/clip", HTTP_GET, handle_clip);
  s_server.on("/api/ota", HTTP_POST, handle_ota_done, handle_ota_upload);
  const char* probes[] = {"/generate_204", "/gen_204", "/hotspot-detect.html", "/connecttest.txt", "/ncsi.txt",
                          "/fwlink", "/redirect", "/canonical.html", "/success.txt"};
  for (const char* p : probes) s_server.on(p, HTTP_ANY, handle_captive);
  s_server.onNotFound(handle_not_found);
  s_server.begin();
}

void loop(uint32_t now) {
  if (s_dns_on) s_dns.processNextRequest();
  s_server.handleClient();
  const bool connected = WiFi.status() == WL_CONNECTED;
  if (connected && s_mode != Mode::Station) {
    s_mode = Mode::Station;
    if (!s_mdns_on) {
      s_mdns_on = MDNS.begin(GS_DEVICE_SLUG);
      if (s_mdns_on) {
        MDNS.addService("http", "tcp", 80);
        MDNS.addServiceTxt("http", "tcp", "template", GS_TEMPLATE);
      }
    }
    Serial.printf("GS-NET {\"event\":\"joined\",\"ip\":\"%s\",\"host\":\"%s\"}\n", WiFi.localIP().toString().c_str(),
                  host().c_str());
    // Keep the setup hotspot a minute so the setup page can show the new address, then close it.
    if (s_ap_on) s_ap_started = now - (uint32_t(GS_SETUP_AP_MINUTES) * 60000u) + 60000u;
  }
  if (!connected && s_mode == Mode::Station) s_mode = Mode::Connecting;
  // Saved Wi-Fi not reachable for 60 s: open the setup hotspot as well (the PIN still guards it).
  // Once per boot, so an unreachable router does not keep a hotspot cycling all night.
  if (!connected && !s_ap_on && !s_fallback_done && s_ssid.length() && s_connect_started &&
      now - s_connect_started > 60000) {
    s_fallback_done = true;
    start_ap();
  }
  // Close the hotspot after its window if nobody is connected to it.
  if (s_ap_on && now - s_ap_started > uint32_t(GS_SETUP_AP_MINUTES) * 60000u && WiFi.softAPgetStationNum() == 0) {
    stop_ap();
    if (!connected) s_mode = s_ssid.length() ? Mode::Connecting : Mode::Offline;
    Serial.println("GS-NET {\"event\":\"setup_hotspot_closed\"}");
  }
  if (s_restart && static_cast<int32_t>(now - s_restart_at) >= 0) {
    delay(100);
    ESP.restart();
  }
}

}  // namespace gsnet
