// Gadget Studio firmware: shared core. The behaviour lives in src/app_<template>.cpp.
#include <Arduino.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <esp_random.h>

#include "config.h"
#include "gs_app.h"
#include "gs_audio.h"
#include "gs_board.h"
#include "gs_device.h"
#include "gs_display.h"
#include "gs_led.h"
#include "gs_net.h"
#include "gs_ota.h"
#include "gs_selftest.h"
#include "gs_sensors.h"
#include "hardware.h"

// Web handlers, JSON and the memory record share the loop task: give it room.
SET_LOOP_TASK_STACK_SIZE(16 * 1024);

static NvsKv s_kv;
static gs::MemoryStore* s_store = nullptr;
static GsApp* s_app = nullptr;
static bool s_boot_ok = false;
static String s_boot_detail;

NvsKv& gs_kv() { return s_kv; }
gs::MemoryStore& gs_store() { return *s_store; }

static gs::MemoryLimits build_limits() {
  gs::MemoryLimits l;
  l.max_favourites = GS_CFG_MAX_FAVOURITES;
  l.volume_max = GS_CFG_VOLUME_MAX;
  l.night_minutes_max = GS_CFG_NIGHT_MINUTES_MAX;
  l.night_level_max = GS_CFG_NIGHT_LEVEL_MAX;
  return l;
}

static gs::Memory build_defaults() {
  gs::Memory m;
  m.volume = GS_CFG_VOLUME_DEFAULT;
  m.night_enabled = GS_CFG_NIGHT_LIGHT ? 1 : 0;
  m.night_minutes = GS_CFG_NIGHT_MINUTES;
  m.night_level = GS_CFG_NIGHT_LEVEL;
  return m;
}

// ---------------------------------------------------------------- shared API
static void api_state(JsonObject out) {
  const uint32_t now = millis();
  JsonObject dev = out["device"].to<JsonObject>();
  dev["name"] = GS_DEVICE_NAME;
  dev["slug"] = GS_DEVICE_SLUG;
  dev["template"] = GS_TEMPLATE;
  dev["firmware"] = GS_FW_VERSION;
  dev["build"] = GS_BUILD_ID;
  dev["uptime_s"] = now / 1000;
  dev["heap_free"] = ESP.getFreeHeap();
  dev["child_directed"] = GS_CHILD_DIRECTED ? true : false;
  JsonObject mem = out["memory"].to<JsonObject>();
  mem["status"] = gs::store_status_name(s_store->status());
  mem["writable"] = s_store->writable();
  mem["storage"] = s_kv.error();
  JsonObject net = out["network"].to<JsonObject>();
  gsnet::fill_state(net);
  JsonObject up = out["update"].to<JsonObject>();
  up["state"] = gsota::state_name();
  up["partition"] = gsota::running_partition();
  up["signed_updates"] = gsota::signing_enabled();
  up["boot_checks"] = s_boot_detail;
  JsonObject card = out["card"].to<JsonObject>();
  card["present"] = GS_HAS_SD ? true : false;
  card["mounted"] = gsboard::sd_mounted();
  card["total_mb"] = uint32_t(gsboard::sd_total_bytes() / (1024 * 1024));
  card["used_mb"] = uint32_t(gsboard::sd_used_bytes() / (1024 * 1024));
  JsonObject app = out["app"].to<JsonObject>();
  s_app->state(app, now);
}

static int api_action(const char* name, JsonObjectConst args, JsonObject out) {
  const uint32_t now = millis();
  if (strcmp(name, "reset_memory") == 0) {
    // Clears everything the device remembers (favourites, settings, clips). Needs a typed RESET.
    if (strcmp(args["confirm"] | "", "RESET") != 0) return gs_reply(out, 400, "Type RESET to clear the device memory.");
    bool ok = true;
    if (!s_kv.ok()) ok = s_kv.format();
    ok = ok && s_store->reset_memory() == gs::StoreResult::Ok;
    ok = ok && s_app->erase_user_data();
    return gs_reply(out, ok ? 200 : 500, ok ? "Memory cleared." : "Could not clear the memory storage.");
  }
  if (strcmp(name, "identify") == 0) {
    for (int i = 0; i < 6; ++i) {
      gsled::set_all(i % 2 ? gs::Rgb() : gs::Rgb{255, 255, 255});
      gsled::show();
      delay(150);
    }
    return gs_reply(out, 200, "Blinked the light.");
  }
  return s_app->action(name, args, out, now);
}

static String api_clip_path(uint8_t id);

// ---------------------------------------------------------------- boot
static bool hold_at_boot(uint32_t ms) {
  const uint32_t t0 = millis();
  while (millis() - t0 < ms) {
    bool any = false;
    for (uint8_t i = 0; i < gsboard::button_count(); ++i) any = any || gsboard::button_raw(i);
    if (!any) return false;
    gsled::set_all(gs::scale(gs::Rgb{150, 60, 255}, gs::breathe(millis(), 800)));
    gsled::show();
    delay(10);
  }
  return true;
}

void setup() {
  Serial.begin(115200);
  delay(50);
  Serial.printf("GS-BOOT {\"name\":\"%s\",\"template\":\"%s\",\"firmware\":\"%s\",\"build\":\"%s\"}\n", GS_DEVICE_NAME,
                GS_TEMPLATE, GS_FW_VERSION, GS_BUILD_ID);
  gsboard::begin();
  gsota::begin();
  gsled::begin();
  gsled::set_cap(GS_CFG_LED_CAP);

  // Hold the button while plugging in: 5 s = open the setup hotspot; 10 s = network reset
  // (forgets the home Wi-Fi and the parent PIN; favourites are kept).
  bool force_setup = false;
  if (gsboard::any_button_held_at_boot() && hold_at_boot(5000)) {
    force_setup = true;
    if (hold_at_boot(5000)) {
      Preferences p;
      p.begin("gswifi", false);
      p.clear();
      p.end();
      Serial.println("GS-NET {\"event\":\"network_reset\"}");
    }
  }
  gsled::set_all(gs::Rgb());
  gsled::show();

  s_kv.begin();
  static gs::MemoryStore store(s_kv, build_limits(), build_defaults());
  s_store = &store;
  s_store->load();
  Serial.printf("GS-MEMORY {\"status\":\"%s\",\"storage\":\"%s\"}\n", gs::store_status_name(s_store->status()),
                s_kv.error());

  gsdisp::begin();
  gsaudio::begin();
  gsaudio::set_volume(s_store->mem().volume, GS_CFG_VOLUME_MAX);
  gssensors::begin();

  gsnet::Hooks hooks;
  hooks.state = api_state;
  hooks.action = api_action;
  hooks.clip_path = api_clip_path;
  gsnet::begin(hooks, force_setup);

  randomSeed(esp_random());
  s_app = gs_make_app();
  s_app->begin(millis());
  s_boot_ok = gsselftest::boot_checks(&s_boot_detail) && s_app->healthy();
  Serial.printf("GS-READY {\"boot_checks\":\"%s\",\"ota_state\":\"%s\",\"network\":\"%s\"}\n", s_boot_detail.c_str(),
                gsota::state_name(), gsnet::mode());
}

void loop() {
  const uint32_t now = millis();
  gsselftest::poll();
  if (!gsselftest::running()) {
    for (uint8_t i = 0; i < gsboard::button_count(); ++i) {
      const gs::ButtonEvent ev = gsboard::button_update(i, now);
      if (ev != gs::ButtonEvent::None) s_app->on_button(i, ev, now);
    }
    s_app->loop(now);
  }
  gsnet::loop(now);
  gsota::loop(now, s_boot_ok && s_app->healthy());
  delay(2);
}

// The story_player app provides the real lookup; other templates have no clips.
__attribute__((weak)) String gs_app_clip_path(uint8_t id) {
  (void)id;
  return String("");
}
static String api_clip_path(uint8_t id) { return gs_app_clip_path(id); }
