// Native-test shim: Arduino-ESP32 Preferences over gs_host::prefs (kept across a simulated reboot).
#pragma once
#include "Arduino.h"

class Preferences {
 public:
  bool begin(const char* ns, bool read_only = false) {
    ns_ = ns ? ns : "";
    ro_ = read_only;
    open_ = true;
    return true;
  }
  void end() { open_ = false; }
  bool clear() {
    if (!open_ || ro_) return false;
    gs_host::prefs[ns_].clear();
    return true;
  }
  bool remove(const char* key) {
    if (!open_ || ro_) return false;
    return gs_host::prefs[ns_].erase(key) > 0;
  }
  bool isKey(const char* key) { return open_ && gs_host::prefs[ns_].count(key) > 0; }
  size_t putString(const char* key, const String& v) {
    if (!open_ || ro_) return 0;
    gs_host::prefs[ns_][key] = std::vector<uint8_t>(v.c_str(), v.c_str() + v.length());
    return v.length();
  }
  String getString(const char* key, const String& def = String()) {
    auto& m = gs_host::prefs[ns_];
    auto it = m.find(key);
    if (!open_ || it == m.end()) return def;
    return String(std::string(it->second.begin(), it->second.end()));
  }
  size_t putBytes(const char* key, const void* buf, size_t len) {
    if (!open_ || ro_) return 0;
    const uint8_t* p = static_cast<const uint8_t*>(buf);
    gs_host::prefs[ns_][key] = std::vector<uint8_t>(p, p + len);
    return len;
  }
  size_t getBytesLength(const char* key) {
    auto& m = gs_host::prefs[ns_];
    auto it = m.find(key);
    return it == m.end() ? 0 : it->second.size();
  }
  size_t getBytes(const char* key, void* buf, size_t cap) {
    auto& m = gs_host::prefs[ns_];
    auto it = m.find(key);
    if (!open_ || it == m.end() || it->second.size() > cap) return 0;
    memcpy(buf, it->second.data(), it->second.size());
    return it->second.size();
  }

 private:
  std::string ns_;
  bool ro_ = false;
  bool open_ = false;
};
