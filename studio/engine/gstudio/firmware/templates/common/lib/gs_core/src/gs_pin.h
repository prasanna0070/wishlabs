// Parent PIN rules: format check and a brute-force lockout for the local web API.
#pragma once
#include <stdint.h>

namespace gs {

// 4 to 8 ASCII digits.
bool pin_format_ok(const char* pin);

// Constant-time comparison of two equal-length byte strings.
bool const_time_equal(const uint8_t* a, const uint8_t* b, uint32_t n);

// After 5 wrong PINs the API locks for 60 s; each further lock doubles, up to 15 minutes.
class PinGuard {
 public:
  static constexpr uint8_t kFreeTries = 5;
  static constexpr uint32_t kBaseLockMs = 60000;
  static constexpr uint32_t kMaxLockMs = 15u * 60u * 1000u;

  bool locked(uint32_t now) const;
  uint32_t retry_after_ms(uint32_t now) const;
  void failure(uint32_t now);
  void success();
  uint8_t failures() const { return failures_; }

 private:
  uint8_t failures_ = 0;
  uint8_t locks_ = 0;
  bool lock_on_ = false;
  uint32_t lock_until_ = 0;
};

}  // namespace gs
