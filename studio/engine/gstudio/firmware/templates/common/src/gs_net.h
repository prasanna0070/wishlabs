// Wi-Fi setup, mDNS and the local parent web page + JSON API (behaviour setting network = parent_page).
//
// The setup hotspot "<Name>-setup-XXXX" opens ONLY after the button is held for 5 s while the
// gadget is plugged in; it never opens by itself. It is WPA2 with a per-device 8-character
// passphrase that the gadget shows on its screen (and prints on the serial port). Saving the first
// parent PIN (6 to 8 digits) also needs a tap on the gadget's own button, so someone who joins the
// hotspot cannot claim the device from another room. Afterwards the device joins the home Wi-Fi
// and answers at http://<slug>.local (and its IP address).
// Every API call except /api/info and the setup calls needs the PIN in the X-GS-PIN header. Wrong
// PINs lock the caller's address out (5 tries) and the whole API (20 tries), and the counts and
// locks survive a restart. Nothing here talks to the internet; the API is reachable only on the
// local network, over plain HTTP (no certificate on a home device).
// With network = off (the default for a child-directed design) none of this starts: no radio.
#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>

namespace gsnet {

struct Hooks {
  void (*state)(JsonObject out) = nullptr;
  int (*action)(const char* name, JsonObjectConst args, JsonObject out) = nullptr;
  // microSD path of a favourite's voice clip, or "" if none (parent listening).
  String (*clip_path)(uint8_t fav_id) = nullptr;
  // microSD path where a favourite's spoken name clip belongs (/names/<slug>.wav), or "" (MAJ-33).
  String (*name_clip_path)(uint8_t fav_id) = nullptr;
  // Shows the hotspot name and passphrase on the gadget's screen (nullptr when there is none).
  void (*show_setup)(const char* ssid, const char* passphrase) = nullptr;
};

constexpr uint32_t kTapConfirmMs = 120000;   // a device tap confirms a first PIN for 2 minutes
constexpr size_t kNameClipMaxBytes = 65536;  // spoken name clips: 16-bit mono WAV, at most 64 KB

void begin(const Hooks& hooks, bool force_setup);
void loop(uint32_t now);
const char* mode();
String ip();
String host();
bool pin_set();
bool wifi_saved();
bool setup_open();
void fill_state(JsonObject out);
// The gadget's button was tapped (main.cpp calls this for every tap).
void note_device_tap(uint32_t now);
// Parent actions handled by the network layer.
bool forget_wifi();
bool restart_requested();

}  // namespace gsnet
