// Host (native test) state behind the Arduino / ESP-IDF shims in this folder. Header-only, C++17.
// The shims let the real device sources (src/gs_net.cpp, gs_board.cpp, gs_audio.cpp, gs_kv_nvs.cpp
// and the template app) compile and run on a computer against in-memory fakes:
//   a clock the test advances, GPIO levels, an SD card, NVS, Preferences, Wi-Fi, a web server,
//   I2S channels fed from a sample source, and FreeRTOS tasks that the test runs ("pumps") by hand.
// Nothing here is used by the ESP32-S3 build (the s3 environment never adds test/shims).
#pragma once
#include <stdint.h>
#include <string.h>

#include <functional>
#include <map>
#include <set>
#include <string>
#include <vector>

namespace gs_host {

// ------------------------------------------------------------------ clock and GPIO
inline uint32_t now_ms = 0;
inline std::map<int, int> gpio_in;    // pin -> level read by digitalRead (default HIGH = not pressed)
inline std::function<int(int pin, uint32_t now)> gpio_fn;  // when set, overrides gpio_in (button scripts)
inline std::map<int, int> gpio_out;   // pin -> last level written
inline std::string serial_out;        // everything printed on Serial
inline std::string serial_in;         // what Serial.read() returns next
inline int restarts = 0;

// ------------------------------------------------------------------ SD card
struct SdCard {
  std::map<std::string, std::vector<uint8_t>> files;
  std::set<std::string> dirs{"/"};
  bool present = true;               // a card is in the slot
  std::set<std::string> fail_remove;  // paths whose remove() fails
  bool fail_all_removes = false;
  bool fail_rename = false;
  int removes = 0;
  std::map<std::string, std::vector<uint8_t>> removed;  // bytes each file held when it was removed
  std::vector<std::string> reads;  // every path opened for reading, in order
};
inline SdCard sd;

// ------------------------------------------------------------------ NVS and Preferences (survive a simulated reboot)
inline std::map<std::string, std::map<std::string, std::vector<uint8_t>>> nvs;   // partition/namespace -> key -> blob
inline std::map<std::string, std::map<std::string, std::vector<uint8_t>>> prefs; // namespace -> key -> bytes

// ------------------------------------------------------------------ Wi-Fi
struct WiFiState {
  int mode = 0;
  bool ap_on = false;
  std::string ap_ssid, ap_pass;
  int softap_calls = 0;
  int station_num = 0;
  bool connected = false;
  std::string sta_ssid;
  uint32_t client_ip = 0x0A000005;  // 10.0.0.5: the address requests come from
};
inline WiFiState wifi;

// ------------------------------------------------------------------ random
inline uint32_t rng_state = 0x12345678u;
inline uint32_t next_random() {
  uint32_t x = rng_state;
  x ^= x << 13;
  x ^= x >> 17;
  x ^= x << 5;
  rng_state = x;
  return x;
}

// ------------------------------------------------------------------ FreeRTOS tasks (pumped by the test)
struct TaskExit {};  // thrown when a task would block forever: the pump returns
struct Task {
  void (*fn)(void*) = nullptr;
  void* arg = nullptr;
  uint32_t notify = 0;
};
inline std::map<std::string, Task> tasks;
inline Task* current = nullptr;  // the task being pumped (nullptr = the loop task, i.e. the test itself)
// Runs a task's function until it would block with nothing to do. Returns false if no such task.
inline bool pump_task(const char* name) {
  auto it = tasks.find(name);
  if (it == tasks.end() || !it->second.fn) return false;
  Task* prev = current;
  current = &it->second;
  try {
    it->second.fn(it->second.arg);
  } catch (const TaskExit&) {
  }
  current = prev;
  return true;
}

// ------------------------------------------------------------------ I2S
struct I2s {
  bool tx_enabled = false, rx_enabled = false;
  int rx_enables = 0;
  uint32_t tx_rate = 0;
  std::vector<int16_t> spk;  // every sample written to the speaker channel
  std::function<bool(int32_t*)> mic;  // next raw INMP441 slot; false = no more data (read times out)
  std::function<void(bool)> on_rx;     // called when the microphone channel is enabled / disabled
};
inline I2s i2s;

// ------------------------------------------------------------------ helpers for tests
inline void reboot_volatile() {
  // A restart: the clock starts again; the card, NVS and Preferences stay. (The firmware's own RAM
  // statics are re-initialised by its begin() functions; FreeRTOS tasks keep their entry points.)
  now_ms = 0;
  serial_out.clear();
  current = nullptr;
  i2s.spk.clear();
  i2s.mic = nullptr;
  wifi.mode = 0;
  wifi.ap_on = false;
  wifi.connected = false;
}

inline void reset_all() {
  reboot_volatile();
  gpio_in.clear();
  gpio_out.clear();
  gpio_fn = nullptr;
  sd = SdCard();
  nvs.clear();
  prefs.clear();
  wifi = WiFiState();
  restarts = 0;
}

inline bool all_zero(const std::vector<uint8_t>& v) {
  for (uint8_t b : v)
    if (b) return false;
  return true;
}

}  // namespace gs_host
