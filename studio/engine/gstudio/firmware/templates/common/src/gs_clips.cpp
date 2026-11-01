#include "gs_clips.h"

#include <Arduino.h>
#include <esp_random.h>
#include <string.h>

namespace gsclips {

static gs::KvBackend* s_kv = nullptr;
static uint32_t s_hours = 0;
static uint32_t s_hour_mark = 0;
static const char* kKeyName = "clipkey";
static const char* kHoursName = "uphours";

void begin(gs::KvBackend* kv) {
  s_kv = kv;
  s_hours = 0;
  s_hour_mark = millis();
  uint8_t b[4];
  if (s_kv && s_kv->get(kHoursName, b, sizeof(b)) == 4)
    s_hours = uint32_t(b[0]) | (uint32_t(b[1]) << 8) | (uint32_t(b[2]) << 16) | (uint32_t(b[3]) << 24);
}

bool key(uint8_t out[gs::kClipKeyBytes], bool create) {
  if (!s_kv) return false;
  if (s_kv->get(kKeyName, out, gs::kClipKeyBytes) == int(gs::kClipKeyBytes)) return true;
  if (!create) return false;
  esp_fill_random(out, gs::kClipKeyBytes);
  if (!s_kv->put(kKeyName, out, gs::kClipKeyBytes)) {
    memset(out, 0, gs::kClipKeyBytes);
    return false;
  }
  return true;
}

bool has_key() {
  uint8_t k[gs::kClipKeyBytes];
  const bool ok = key(k, false);
  memset(k, 0, sizeof(k));
  return ok;
}

bool destroy_key() { return s_kv && s_kv->erase(kKeyName); }

void new_nonce(uint8_t out[gs::kClipNonceBytes]) { esp_fill_random(out, gs::kClipNonceBytes); }

uint32_t hours() { return s_hours; }

void loop(uint32_t now) {
  if (now - s_hour_mark < 3600000u) return;
  s_hour_mark += 3600000u;
  ++s_hours;
  const uint8_t b[4] = {uint8_t(s_hours), uint8_t(s_hours >> 8), uint8_t(s_hours >> 16), uint8_t(s_hours >> 24)};
  if (s_kv) s_kv->put(kHoursName, b, sizeof(b));
}

}  // namespace gsclips
