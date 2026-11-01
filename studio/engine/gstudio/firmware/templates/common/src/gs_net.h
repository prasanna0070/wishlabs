// Wi-Fi setup, mDNS and the local parent web page + JSON API.
//
// First boot (or after a network reset): an open setup hotspot "<Name>-setup-XXXX" with a captive
// page where the parent picks the home Wi-Fi and sets a 4 to 8 digit parent PIN. Afterwards the
// device joins the home Wi-Fi and answers at http://<slug>.local (and its IP address).
// Every API call except /api/info and the setup calls needs the PIN in the X-GS-PIN header.
// Nothing here talks to the internet; the API is reachable only on the local network.
#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>

namespace gsnet {

struct Hooks {
  void (*state)(JsonObject out);
  int (*action)(const char* name, JsonObjectConst args, JsonObject out);
  // Returns the microSD path of a favourite's voice clip, or "" if none (parent listening).
  String (*clip_path)(uint8_t fav_id);
};

void begin(const Hooks& hooks, bool force_setup);
void loop(uint32_t now);
const char* mode();
String ip();
String host();
bool pin_set();
bool wifi_saved();
void fill_state(JsonObject out);
// Parent actions handled by the network layer.
bool forget_wifi();
bool restart_requested();

}  // namespace gsnet
