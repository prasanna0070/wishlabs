// Native-test shim of the ESP-IDF NVS API subset used by src/gs_kv_nvs.cpp, over gs_host::nvs.
#pragma once
#include "Arduino.h"

typedef uint32_t nvs_handle_t;
typedef enum { NVS_READONLY, NVS_READWRITE } nvs_open_mode_t;
#define ESP_ERR_NVS_NOT_FOUND 0x1102
#define ESP_ERR_NVS_NO_FREE_PAGES 0x110d
#define ESP_ERR_NVS_INVALID_LENGTH 0x110c

namespace gs_host {
inline std::map<nvs_handle_t, std::string> nvs_handles;
inline nvs_handle_t nvs_next = 1;
inline bool nvs_fail_init = false;
}  // namespace gs_host

inline const char* esp_err_to_name(esp_err_t e) {
  switch (e) {
    case ESP_OK: return "ESP_OK";
    case ESP_ERR_NVS_NOT_FOUND: return "ESP_ERR_NVS_NOT_FOUND";
    case ESP_ERR_NVS_NO_FREE_PAGES: return "ESP_ERR_NVS_NO_FREE_PAGES";
    default: return "ESP_FAIL";
  }
}
inline esp_err_t nvs_open_from_partition(const char* part, const char* ns, nvs_open_mode_t, nvs_handle_t* h) {
  const nvs_handle_t id = gs_host::nvs_next++;
  gs_host::nvs_handles[id] = std::string(part) + "/" + ns;
  *h = id;
  return ESP_OK;
}
inline void nvs_close(nvs_handle_t h) { gs_host::nvs_handles.erase(h); }
inline esp_err_t nvs_get_blob(nvs_handle_t h, const char* key, void* out, size_t* len) {
  auto& m = gs_host::nvs[gs_host::nvs_handles[h]];
  auto it = m.find(key);
  if (it == m.end()) return ESP_ERR_NVS_NOT_FOUND;
  if (out && *len < it->second.size()) return ESP_ERR_NVS_INVALID_LENGTH;
  if (out) memcpy(out, it->second.data(), it->second.size());
  *len = it->second.size();
  return ESP_OK;
}
inline esp_err_t nvs_set_blob(nvs_handle_t h, const char* key, const void* v, size_t len) {
  const uint8_t* p = static_cast<const uint8_t*>(v);
  gs_host::nvs[gs_host::nvs_handles[h]][key] = std::vector<uint8_t>(p, p + len);
  return ESP_OK;
}
inline esp_err_t nvs_erase_key(nvs_handle_t h, const char* key) {
  return gs_host::nvs[gs_host::nvs_handles[h]].erase(key) ? ESP_OK : ESP_ERR_NVS_NOT_FOUND;
}
inline esp_err_t nvs_commit(nvs_handle_t) { return ESP_OK; }
