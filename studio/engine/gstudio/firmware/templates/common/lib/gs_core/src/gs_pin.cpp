#include "gs_pin.h"

#include "gs_util.h"

namespace gs {

bool pin_format_ok(const char* pin) {
  if (!pin) return false;
  int n = 0;
  for (const char* p = pin; *p; ++p, ++n) {
    if (*p < '0' || *p > '9' || n >= 8) return false;
  }
  return n >= 4;
}

bool const_time_equal(const uint8_t* a, const uint8_t* b, uint32_t n) {
  uint8_t diff = 0;
  for (uint32_t i = 0; i < n; ++i) diff |= uint8_t(a[i] ^ b[i]);
  return diff == 0;
}

bool PinGuard::locked(uint32_t now) const { return lock_on_ && !time_reached(now, lock_until_); }

uint32_t PinGuard::retry_after_ms(uint32_t now) const { return locked(now) ? lock_until_ - now : 0; }

void PinGuard::failure(uint32_t now) {
  if (locked(now)) return;
  if (++failures_ >= kFreeTries) {
    uint32_t ms = kBaseLockMs;
    for (uint8_t i = 0; i < locks_ && ms < kMaxLockMs; ++i) ms *= 2;
    if (ms > kMaxLockMs) ms = kMaxLockMs;
    lock_on_ = true;
    lock_until_ = now + ms;
    if (locks_ < 8) ++locks_;
    failures_ = 0;
  }
}

void PinGuard::success() {
  failures_ = 0;
  locks_ = 0;
  lock_on_ = false;
}

}  // namespace gs
