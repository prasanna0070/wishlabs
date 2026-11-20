#include "gs_selftest.h"

#include <ArduinoJson.h>
#include <FS.h>
#include <WiFi.h>

#include "config.h"
#include "gs_audio.h"
#include "gs_board.h"
#include "gs_device.h"
#include "gs_display.h"
#include "gs_led.h"
#include "gs_ota.h"
#include "gs_sensors.h"
#include "hardware.h"

#if GS_HAS_SD
#include <SD.h>
#endif

namespace gsselftest {

static bool s_running = false;
static char s_line[48];
static size_t s_len = 0;

struct Tally {
  int passed = 0;
  int failed = 0;
  int skipped = 0;
  JsonDocument operator_checks;
  Tally() { operator_checks.to<JsonArray>(); }
};

static void emit(Tally& t, const char* id, const char* status, JsonDocument& detail) {
  JsonDocument doc;
  doc["id"] = id;
  doc["status"] = status;
  doc["detail"] = detail;
  Serial.print("GS-TEST ");
  serializeJson(doc, Serial);
  Serial.println();
  if (strcmp(status, "pass") == 0) ++t.passed;
  else if (strcmp(status, "fail") == 0) ++t.failed;
  else if (strcmp(status, "skipped") == 0) ++t.skipped;
  else t.operator_checks.as<JsonArray>().add(id);
}

static void skip(Tally& t, const char* id, const char* why) {
  JsonDocument d;
  d["reason"] = why;
  emit(t, id, "skipped", d);
}

static void test_firmware(Tally& t) {
  JsonDocument d;
  d["name"] = GS_DEVICE_NAME;
  d["template"] = GS_TEMPLATE;
  d["firmware"] = GS_FW_VERSION;
  d["build"] = GS_BUILD_ID;
  d["partition"] = gsota::running_partition();
  d["ota_state"] = gsota::state_name();
  d["mac"] = WiFi.macAddress();
  d["heap_free"] = ESP.getFreeHeap();
  d["psram_bytes"] = ESP.getPsramSize();
  d["flash_bytes"] = ESP.getFlashChipSize();
  // The core module from the library fixes the expected memory (hardware.h, from modules.json).
  const bool ok = ESP.getPsramSize() >= uint32_t(GS_CORE_PSRAM_MB) * 1024u * 1024u * 9u / 10u &&
                  ESP.getFlashChipSize() >= uint32_t(GS_CORE_FLASH_MB) * 1024u * 1024u;
  d["expect"] = String(GS_CORE_MODULE) + ": " + GS_CORE_FLASH_MB + " MB flash, " + GS_CORE_PSRAM_MB + " MB PSRAM";
  emit(t, "core_board", ok ? "pass" : "fail", d);
}

static void test_memory(Tally& t) {
  JsonDocument d;
  d["nvs"] = gs_kv().error();
  d["store"] = gs::store_status_name(gs_store().status());
  d["records_written"] = gs_store().seq();
  emit(t, "memory", gs_store().writable() ? "pass" : "fail", d);
}

static void test_sd(Tally& t) {
#if GS_HAS_SD
  JsonDocument d;
  if (!gsboard::sd_mounted()) gsboard::sd_remount();
  if (!gsboard::sd_mounted()) {
    d["error"] = "no card, or the card is not FAT32";
    emit(t, "microsd", "fail", d);
    return;
  }
  d["total_mb"] = uint32_t(gsboard::sd_total_bytes() / (1024 * 1024));
  d["used_mb"] = uint32_t(gsboard::sd_used_bytes() / (1024 * 1024));
  const char* path = "/gs_selftest.tmp";
  uint8_t pattern[512];
  for (int i = 0; i < 512; ++i) pattern[i] = uint8_t(i * 7 + 3);
  bool ok = false;
  File w = SD.open(path, FILE_WRITE);
  if (w) {
    ok = w.write(pattern, sizeof(pattern)) == sizeof(pattern);
    w.close();
  }
  if (ok) {
    File r = SD.open(path, FILE_READ);
    uint8_t back[512];
    ok = r && r.read(back, sizeof(back)) == sizeof(back) && memcmp(back, pattern, sizeof(back)) == 0;
    if (r) r.close();
  }
  SD.remove(path);
  d["write_read_512_bytes"] = ok;
  d["story_pack"] = SD.exists("/stories.json");
  emit(t, "microsd", ok ? "pass" : "fail", d);
#else
  skip(t, "microsd", "no microSD module in this design");
#endif
}

static void test_led(Tally& t) {
  if (!gsled::present()) {
    skip(t, "led", "no WS2812B light in this design");
    return;
  }
  const gs::Rgb seq[4] = {{255, 0, 0}, {0, 255, 0}, {0, 0, 255}, {255, 255, 255}};
  gsled::set_cap(GS_CFG_LED_CAP);
  for (const gs::Rgb& c : seq) {
    gsled::set_all(c);
    gsled::show();
    delay(500);
  }
  gsled::set_all(gs::Rgb());
  gsled::show();
  JsonDocument d;
  d["shown"] = "red, green, blue, white, 0.5 s each";
  d["count"] = gsled::count();
  d["confirm"] = "every LED lit in each colour";
  emit(t, "led", "operator_check", d);
}

static void test_display(Tally& t) {
#if GS_DISPLAY_KIND != GS_DISP_NONE
  JsonDocument d;
  if (!gsdisp::present()) {
    d["error"] = "display did not answer";
    emit(t, "display", "fail", d);
    return;
  }
  gsdisp::clear();
  gsdisp::fill_rect(0, 0, gsdisp::width(), 2, gsdisp::kWhite);
  gsdisp::fill_rect(0, gsdisp::height() - 2, gsdisp::width(), 2, gsdisp::kWhite);
  gsdisp::fill_rect(0, 0, 2, gsdisp::height(), gsdisp::kWhite);
  gsdisp::fill_rect(gsdisp::width() - 2, 0, 2, gsdisp::height(), gsdisp::kWhite);
  gsdisp::text(8, 8, "SELF TEST", 1, gsdisp::kWhite);
  gsdisp::flush();
  d["width"] = gsdisp::width();
  d["height"] = gsdisp::height();
  d["confirm"] = "a full white border and the words SELF TEST";
  emit(t, "display", "operator_check", d);
#else
  skip(t, "display", "no display in this design");
#endif
}

static void test_button(Tally& t) {
  if (gsboard::button_count() == 0) {
    skip(t, "button", "no button in this design");
    return;
  }
  for (uint8_t i = 0; i < gsboard::button_count(); ++i) {
    Serial.printf("GS-PROMPT {\"text\":\"Press and release the %s within 10 s\"}\n", gsboard::button_id(i));
    const uint32_t t0 = millis();
    bool pressed = false, released = false;
    while (millis() - t0 < 10000 && !released) {
      const bool level = gsboard::button_raw(i);
      if (level) pressed = true;
      if (pressed && !level) released = true;
      delay(5);
    }
    JsonDocument d;
    d["button"] = gsboard::button_id(i);
    d["pressed"] = pressed;
    d["released"] = released;
    d["seconds"] = (millis() - t0) / 1000.0;
    char id[32];
    snprintf(id, sizeof(id), "button_%u", unsigned(i + 1));
    emit(t, id, released ? "pass" : "fail", d);
  }
}

static void test_amp(Tally& t) {
#if GS_HAS_AMP
  JsonDocument d;
  if (!gsaudio::ready()) {
    d["error"] = "I2S output did not start";
    emit(t, "speaker", "fail", d);
    return;
  }
  gsaudio::set_volume(GS_CFG_VOLUME_DEFAULT, GS_CFG_VOLUME_MAX);
  gsaudio::play_tone(1000, 700, 60);
  const uint32_t t0 = millis();
  while (gsaudio::busy() && millis() - t0 < 3000) delay(10);
  gsaudio::take_finished();
  d["played"] = "1 kHz tone, 0.7 s, software volume " + String(GS_CFG_VOLUME_DEFAULT);
  d["confirm"] = "a clear beep from the speaker, no crackle";
  emit(t, "speaker", "operator_check", d);
#else
  skip(t, "speaker", "no amplifier in this design");
#endif
}

static void test_mic(Tally& t) {
#if GS_HAS_MIC
  JsonDocument d;
  if (!gsaudio::mic_begin()) {
    d["error"] = "I2S input did not start";
    emit(t, "microphone", "fail", d);
    return;
  }
  float quiet_db = -120, loud_db = -120;
  int16_t quiet_peak = 0, loud_peak = 0;
  bool stuck = true;
  const bool got_quiet = gsaudio::mic_measure(500, &quiet_db, &quiet_peak, &stuck);
  Serial.println("GS-PROMPT {\"text\":\"Clap once near the microphone now\"}");
  bool stuck2 = true;
  const bool got_loud = gsaudio::mic_measure(1500, &loud_db, &loud_peak, &stuck2);
  const float loud_peak_db = loud_peak > 0 ? 20.0f * log10f(float(loud_peak) / 32768.0f) : -120.0f;
  d["quiet_rms_dbfs"] = quiet_db;
  d["quiet_peak"] = quiet_peak;
  d["clap_rms_dbfs"] = loud_db;
  d["clap_peak_dbfs"] = loud_peak_db;
  d["samples_vary"] = !(stuck && stuck2);
  d["note"] = "levels are readings in firmware units, not an acoustic calibration";
  const bool ok = got_quiet && got_loud && !stuck2 && loud_peak_db > quiet_db + 12.0f;
  if (!ok) d["hint"] = "no response: check the mic cable (J_MIC) or clap closer";
  emit(t, "microphone", ok ? "pass" : "fail", d);
#else
  skip(t, "microphone", "no microphone in this design");
#endif
}

static void test_i2c_and_sensors(Tally& t) {
#if GS_HAS_I2C
  uint8_t found[16];
  const int n = gsboard::i2c_scan(found, 16);
  JsonDocument d;
  JsonArray arr = d["found"].to<JsonArray>();
  char hex[8];
  for (int i = 0; i < n; ++i) {
    snprintf(hex, sizeof(hex), "0x%02X", found[i]);
    arr.add(hex);
  }
  const uint8_t expected[] = GS_I2C_EXPECTED;
  JsonArray missing = d["missing"].to<JsonArray>();
  bool all = true;
  for (uint8_t e : expected) {
    if (e == 0) continue;
    bool seen = false;
    for (int i = 0; i < n; ++i) seen = seen || found[i] == e;
    if (!seen) {
      all = false;
      snprintf(hex, sizeof(hex), "0x%02X", e);
      missing.add(hex);
    }
  }
  emit(t, "i2c_bus", all ? "pass" : "fail", d);
#else
  skip(t, "i2c_bus", "no I2C module in this design");
#endif
  if (gssensors::sensor_count() == 0) return;
  gssensors::Reading r[12];
  const int m = gssensors::read_all(r, 12);
  for (int i = 0; i < m; ++i) {
    JsonDocument d;
    d["sensor"] = r[i].sensor;
    d["value"] = r[i].value;
    d["unit"] = r[i].unit;
    bool plausible = r[i].ok;
    if (strcmp(r[i].quantity, "temperature") == 0) plausible = plausible && r[i].value > -20 && r[i].value < 70;
    if (strcmp(r[i].quantity, "humidity") == 0) plausible = plausible && r[i].value >= 0 && r[i].value <= 100;
    if (strcmp(r[i].quantity, "pressure") == 0) plausible = plausible && r[i].value > 500 && r[i].value < 1100;
    if (strcmp(r[i].quantity, "soil") == 0) {
      // A probe in air or soil sits between the rails; 0 or full scale means a short or an open lead.
      d["raw"] = r[i].raw;
      plausible = plausible && r[i].raw > 50 && r[i].raw < 4050;
      if (!plausible) d["hint"] = "check the soil probe lead (J_IO) and that the probe works at 3.3 V";
    }
    if (strcmp(r[i].quantity, "water") == 0) {
      d["state"] = r[i].value > 0.5f ? "water up (contact closed)" : "low or not connected (contact open)";
      d["hint"] = "lift the float and let it drop: the state must follow";
    }
    d["note"] = "a reading, not a calibration";
    char id[40];
    snprintf(id, sizeof(id), "%s_%s", r[i].sensor, r[i].quantity);
    emit(t, id, plausible ? "pass" : "fail", d);
  }
}

static void run_all() {
  s_running = true;
  gsaudio::stop();
  Tally t;
  Serial.println("GS-SELFTEST-BEGIN {\"version\":1}");
  test_firmware(t);
  test_memory(t);
  test_sd(t);
  test_led(t);
  test_display(t);
  test_amp(t);
  test_mic(t);
  test_i2c_and_sensors(t);
  test_button(t);
  JsonDocument sum;
  sum["ok"] = t.failed == 0;
  sum["passed"] = t.passed;
  sum["failed"] = t.failed;
  sum["skipped"] = t.skipped;
  sum["operator_checks"] = t.operator_checks;
  sum["firmware"] = GS_FW_VERSION;
  sum["scope"] = "automatic checks only; operator_checks need a person to confirm; not a physical or safety certification";
  Serial.print("GS-SELFTEST ");
  serializeJson(sum, Serial);
  Serial.println();
  s_running = false;
}

static void print_info() {
  JsonDocument d;
  d["name"] = GS_DEVICE_NAME;
  d["slug"] = GS_DEVICE_SLUG;
  d["template"] = GS_TEMPLATE;
  d["firmware"] = GS_FW_VERSION;
  d["build"] = GS_BUILD_ID;
  d["partition"] = gsota::running_partition();
  d["ota_state"] = gsota::state_name();
  d["uptime_s"] = millis() / 1000;
  Serial.print("GS-INFO ");
  serializeJson(d, Serial);
  Serial.println();
}

void poll() {
  while (Serial.available()) {
    const int c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      s_line[s_len] = '\0';
      if (strcmp(s_line, "selftest") == 0) {
        run_all();
      } else if (strcmp(s_line, "info") == 0) {
        print_info();
      } else if (s_len > 0) {
        Serial.println("GS-HELP {\"commands\":[\"selftest\",\"info\"]}");
      }
      s_len = 0;
    } else if (s_len + 1 < sizeof(s_line)) {
      s_line[s_len++] = char(c);
    }
  }
}

bool running() { return s_running; }

bool boot_checks(String* detail) {
  bool ok = true;
  *detail = "";
  if (!gs_kv().ok() || gs_store().status() == gs::StoreStatus::StorageError) {
    ok = false;
    *detail += "memory storage unreadable; ";
  }
#if GS_HAS_AMP
  if (!gsaudio::ready()) {
    ok = false;
    *detail += "speaker output did not start; ";
  }
#endif
  if (ESP.getFreeHeap() < 40000) {
    ok = false;
    *detail += "low memory; ";
  }
  if (ok) *detail = "ok";
  return ok;
}

}  // namespace gsselftest

bool gs_selftest_running() { return gsselftest::running(); }
