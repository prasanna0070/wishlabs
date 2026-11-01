// Native-test shim of the ESP-IDF NVS flash init subset used by src/gs_kv_nvs.cpp.
#pragma once
#include "nvs.h"

inline esp_err_t nvs_flash_init_partition(const char*) {
  return gs_host::nvs_fail_init ? ESP_ERR_NVS_NO_FREE_PAGES : ESP_OK;
}
inline esp_err_t nvs_flash_deinit_partition(const char*) { return ESP_OK; }
inline esp_err_t nvs_flash_erase_partition(const char* part) {
  const std::string pre = std::string(part) + "/";
  for (auto it = gs_host::nvs.begin(); it != gs_host::nvs.end();) {
    if (it->first.compare(0, pre.size(), pre) == 0) it = gs_host::nvs.erase(it);
    else ++it;
  }
  gs_host::nvs_fail_init = false;
  return ESP_OK;
}
