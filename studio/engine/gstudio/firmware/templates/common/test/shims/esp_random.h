// Native-test shim: deterministic hardware RNG (seed with gs_host::rng_state).
#pragma once
#include "gs_host.h"

inline uint32_t esp_random() { return gs_host::next_random(); }
inline void esp_fill_random(void* buf, size_t len) {
  uint8_t* p = static_cast<uint8_t*>(buf);
  for (size_t i = 0; i < len; ++i) p[i] = uint8_t(gs_host::next_random() >> 24);
}
