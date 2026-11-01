// KvBackend over ESP-IDF NVS on the dedicated "gsnvs" partition (separate from Wi-Fi's "nvs").
// NVS writes are themselves designed to survive power loss; the store adds its own two-slot,
// CRC-checked records on top. A damaged partition is reported, never silently erased.
#pragma once
#include <nvs.h>

#include "gs_store.h"

class NvsKv : public gs::KvBackend {
 public:
  // Returns false if the partition cannot be opened (the store then reports StorageError).
  bool begin();
  bool ok() const { return handle_ != 0; }
  const char* error() const { return error_; }
  // Parent "reset memory" when the partition itself is damaged: erases and re-initialises it.
  bool format();

  int get(const char* key, uint8_t* buf, size_t cap) override;
  bool put(const char* key, const uint8_t* buf, size_t len) override;
  bool erase(const char* key) override;

 private:
  nvs_handle_t handle_ = 0;
  const char* error_ = "not started";
};
