// Native-test shim of the Arduino-ESP32 SD library over gs_host::sd.
#pragma once
#include "FS.h"
#include "SPI.h"

typedef enum { CARD_NONE, CARD_MMC, CARD_SD, CARD_SDHC, CARD_UNKNOWN } sdcard_type_t;

class SDFS : public fs::FS {
 public:
  bool begin(uint8_t = 0, SPIClass& = SPI, uint32_t = 4000000, const char* = "/sd", uint8_t = 5, bool = false) {
    mounted_ = gs_host::sd.present;
    return mounted_;
  }
  void end() { mounted_ = false; }
  sdcard_type_t cardType() { return mounted_ && gs_host::sd.present ? CARD_SDHC : CARD_NONE; }
  uint64_t totalBytes() { return mounted_ ? 32ull * 1000 * 1000 * 1000 : 0; }
  uint64_t usedBytes() {
    uint64_t n = 0;
    for (const auto& kv : gs_host::sd.files) n += kv.second.size();
    return n;
  }
  bool mounted() const { return mounted_; }
};

inline SDFS SD;
