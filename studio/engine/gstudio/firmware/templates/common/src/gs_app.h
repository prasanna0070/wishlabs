// The contract between the shared device core (main.cpp, web API, self-test) and a behaviour
// template (src/app_<template>.cpp). Exactly one template app is compiled into a build.
#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>

#include "gs_button.h"

class GsApp {
 public:
  virtual ~GsApp() {}
  virtual const char* template_name() const = 0;
  // Called once after the hardware, memory and network are up.
  virtual void begin(uint32_t now) = 0;
  // Called every loop (about every 5 ms).
  virtual void loop(uint32_t now) = 0;
  virtual void on_button(uint8_t index, gs::ButtonEvent ev, uint32_t now) = 0;
  // Fills the template part of GET /api/state.
  virtual void state(JsonObject out, uint32_t now) = 0;
  // Handles POST /api/action. Returns an HTTP status; writes {"ok":..,"message":..} into out.
  virtual int action(const char* name, JsonObjectConst args, JsonObject out, uint32_t now) = 0;
  // Template checks for the post-update self-test (OTA rollback decision).
  virtual bool healthy() { return true; }
  // Parent "erase everything": the template deletes what it stores (for example voice clips).
  virtual bool erase_user_data() { return true; }
  // microSD path for a favourite's spoken name clip (/names/<slug>.wav), or "" (story_player).
  virtual String name_clip_path(uint8_t fav_id) {
    (void)fav_id;
    return String("");
  }
  // The microphone just opened (true) or closed (false). Return true when the template shows that
  // itself; otherwise the shared listening light and face are used (gs_listen.cpp).
  virtual bool show_listening(bool on) {
    (void)on;
    return false;
  }
};

// Defined by the template's app file.
GsApp* gs_make_app();

// microSD path of a favourite's voice clip for parent listening ("" if none). The story_player app
// defines it; a weak default in main.cpp returns "" for templates without clips.
String gs_app_clip_path(uint8_t fav_id);

// Small helpers shared by the apps for consistent API replies.
inline int gs_reply(JsonObject out, int status, const char* message) {
  out["ok"] = status >= 200 && status < 300;
  out["message"] = message;
  return status;
}
