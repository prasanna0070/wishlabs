#include "gs_kv_nvs.h"

#include <nvs_flash.h>

static const char* kPartition = "gsnvs";
static const char* kNamespace = "gsmem";

bool NvsKv::begin() {
  const esp_err_t init = nvs_flash_init_partition(kPartition);
  if (init != ESP_OK) {
    // ESP_ERR_NVS_NO_FREE_PAGES / NEW_VERSION_FOUND mean the partition needs erasing. That would
    // destroy the parent's data, so it is left for an explicit "reset memory".
    error_ = esp_err_to_name(init);
    handle_ = 0;
    return false;
  }
  const esp_err_t op = nvs_open_from_partition(kPartition, kNamespace, NVS_READWRITE, &handle_);
  if (op != ESP_OK) {
    error_ = esp_err_to_name(op);
    handle_ = 0;
    return false;
  }
  error_ = "ok";
  return true;
}

bool NvsKv::format() {
  if (handle_) {
    nvs_close(handle_);
    handle_ = 0;
  }
  nvs_flash_deinit_partition(kPartition);
  if (nvs_flash_erase_partition(kPartition) != ESP_OK) return false;
  return begin();
}

int NvsKv::get(const char* key, uint8_t* buf, size_t cap) {
  if (!handle_) return -2;
  size_t len = cap;
  const esp_err_t e = nvs_get_blob(handle_, key, buf, &len);
  if (e == ESP_ERR_NVS_NOT_FOUND) return -1;
  if (e != ESP_OK) return -2;
  return static_cast<int>(len);
}

bool NvsKv::put(const char* key, const uint8_t* buf, size_t len) {
  if (!handle_) return false;
  if (nvs_set_blob(handle_, key, buf, len) != ESP_OK) return false;
  return nvs_commit(handle_) == ESP_OK;
}

bool NvsKv::erase(const char* key) {
  if (!handle_) return false;
  const esp_err_t e = nvs_erase_key(handle_, key);
  if (e != ESP_OK && e != ESP_ERR_NVS_NOT_FOUND) return false;
  return nvs_commit(handle_) == ESP_OK;
}
