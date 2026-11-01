// Gadget Studio core: small portable helpers (no Arduino, no ESP-IDF).
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace gs {

// Wrap-safe "a is at or after b" for millisecond counters that overflow every ~49 days.
inline bool time_reached(uint32_t now, uint32_t deadline) {
  return static_cast<int32_t>(now - deadline) >= 0;
}

// Wrap-safe elapsed time.
inline uint32_t elapsed(uint32_t now, uint32_t since) { return now - since; }

// CRC-32 (IEEE 802.3, reflected, poly 0xEDB88320). crc32("123456789") == 0xCBF43926.
uint32_t crc32(const uint8_t* data, size_t len, uint32_t crc = 0);

// Bounded string copy that always terminates (when cap > 0). Returns false if truncated.
bool copy_str(char* dst, size_t cap, const char* src);

// Deterministic, seedable random source (xorshift32). The device seeds it from the hardware RNG.
class Rng {
 public:
  explicit Rng(uint32_t seed = 0x9E3779B9u) : s_(seed ? seed : 0x9E3779B9u) {}
  uint32_t next() {
    uint32_t x = s_;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    s_ = x;
    return x;
  }
  // Uniform-ish integer in [0, n). n == 0 returns 0.
  uint32_t below(uint32_t n) { return n ? next() % n : 0; }
  void seed(uint32_t s) { s_ = s ? s : 0x9E3779B9u; }

 private:
  uint32_t s_;
};

// Little-endian byte writer/reader over a caller buffer, with overflow tracking.
class ByteWriter {
 public:
  ByteWriter(uint8_t* buf, size_t cap) : buf_(buf), cap_(cap) {}
  void u8(uint8_t v) { put(&v, 1); }
  void u16(uint16_t v) {
    uint8_t b[2] = {uint8_t(v), uint8_t(v >> 8)};
    put(b, 2);
  }
  void u32(uint32_t v) {
    uint8_t b[4] = {uint8_t(v), uint8_t(v >> 8), uint8_t(v >> 16), uint8_t(v >> 24)};
    put(b, 4);
  }
  void bytes(const void* p, size_t n) { put(static_cast<const uint8_t*>(p), n); }
  size_t size() const { return pos_; }
  bool ok() const { return ok_; }
  uint8_t* data() { return buf_; }

 private:
  void put(const uint8_t* p, size_t n) {
    if (!ok_ || pos_ + n > cap_) {
      ok_ = false;
      return;
    }
    for (size_t i = 0; i < n; ++i) buf_[pos_ + i] = p[i];
    pos_ += n;
  }
  uint8_t* buf_;
  size_t cap_;
  size_t pos_ = 0;
  bool ok_ = true;
};

class ByteReader {
 public:
  ByteReader(const uint8_t* buf, size_t len) : buf_(buf), len_(len) {}
  uint8_t u8() {
    uint8_t b = 0;
    get(&b, 1);
    return b;
  }
  uint16_t u16() {
    uint8_t b[2] = {0, 0};
    get(b, 2);
    return uint16_t(b[0] | (b[1] << 8));
  }
  uint32_t u32() {
    uint8_t b[4] = {0, 0, 0, 0};
    get(b, 4);
    return uint32_t(b[0]) | (uint32_t(b[1]) << 8) | (uint32_t(b[2]) << 16) | (uint32_t(b[3]) << 24);
  }
  void bytes(void* p, size_t n) { get(static_cast<uint8_t*>(p), n); }
  size_t pos() const { return pos_; }
  size_t remaining() const { return ok_ ? len_ - pos_ : 0; }
  bool ok() const { return ok_; }

 private:
  void get(uint8_t* p, size_t n) {
    if (!ok_ || pos_ + n > len_) {
      ok_ = false;
      for (size_t i = 0; i < n; ++i) p[i] = 0;
      return;
    }
    for (size_t i = 0; i < n; ++i) p[i] = buf_[pos_ + i];
    pos_ += n;
  }
  const uint8_t* buf_;
  size_t len_;
  size_t pos_ = 0;
  bool ok_ = true;
};

}  // namespace gs
