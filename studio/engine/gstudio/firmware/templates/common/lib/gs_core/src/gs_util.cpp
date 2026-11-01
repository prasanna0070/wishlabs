#include "gs_util.h"

namespace gs {

uint32_t crc32(const uint8_t* data, size_t len, uint32_t crc) {
  crc = ~crc;
  for (size_t i = 0; i < len; ++i) {
    crc ^= data[i];
    for (int k = 0; k < 8; ++k) crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
  }
  return ~crc;
}

bool copy_str(char* dst, size_t cap, const char* src) {
  if (cap == 0) return false;
  size_t i = 0;
  if (src) {
    for (; src[i] && i + 1 < cap; ++i) dst[i] = src[i];
  }
  dst[i] = '\0';
  return !(src && src[i]);
}

}  // namespace gs
