// Parent PIN rules and the brute-force lockout for the local web API.
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace gs {

// New PINs are 6 to 8 ASCII digits. A PIN saved by an older firmware (4 or 5 digits) still unlocks
// the device, so an update never locks a parent out; only choosing a PIN needs 6 digits.
constexpr uint8_t kPinMinDigits = 6;
constexpr uint8_t kPinLegacyMinDigits = 4;
constexpr uint8_t kPinMaxDigits = 8;
bool pin_format_ok(const char* pin, uint8_t min_digits = kPinMinDigits);

// Constant-time comparison of two equal-length byte strings.
bool const_time_equal(const uint8_t* a, const uint8_t* b, uint32_t n);

// After free_tries wrong PINs the guard locks for 60 s; each further lock doubles, up to 15 minutes.
class PinGuard {
 public:
  static constexpr uint8_t kFreeTries = 5;
  static constexpr uint32_t kBaseLockMs = 60000;
  static constexpr uint32_t kMaxLockMs = 15u * 60u * 1000u;

  explicit PinGuard(uint8_t free_tries = kFreeTries) : free_tries_(free_tries) {}
  bool locked(uint32_t now) const;
  uint32_t retry_after_ms(uint32_t now) const;
  void failure(uint32_t now);
  void success();
  uint8_t failures() const { return failures_; }
  uint8_t locks() const { return locks_; }
  // Restores a saved guard: a lock resumes with the time it had left when it was saved.
  void restore(uint8_t failures, uint8_t locks, uint32_t remaining_ms, uint32_t now);

 private:
  uint8_t free_tries_;
  uint8_t failures_ = 0;
  uint8_t locks_ = 0;
  bool lock_on_ = false;
  uint32_t lock_until_ = 0;
};

// One guard per client address (the 8 most recently seen), plus a device-wide guard with a higher
// threshold, so neither one address nor many addresses can try PINs quickly. A lock tripped from one
// address does not lock out the others. The table is saved after every change (persist()) and
// loaded at boot, so a power cycle neither resets the counts nor lifts a lock.
class PinGuardTable {
 public:
  static constexpr uint8_t kSlots = 8;
  static constexpr uint8_t kGlobalFreeTries = 20;
  static constexpr size_t kBlobBytes = 1 + 6 + kSlots * 10 + 4;

  PinGuardTable() : global_(kGlobalFreeTries) {}
  bool locked(uint32_t ip, uint32_t now) const;
  uint32_t retry_after_ms(uint32_t ip, uint32_t now) const;
  void failure(uint32_t ip, uint32_t now);
  void success(uint32_t ip);
  uint8_t failures(uint32_t ip) const;

  // Persistence (little-endian blob with a CRC-32). load() returns false for a damaged blob, which
  // is then ignored (the guard starts empty) rather than trusted.
  size_t save(uint8_t* buf, size_t cap, uint32_t now) const;
  bool load(const uint8_t* buf, size_t len, uint32_t now);
  bool dirty() const { return dirty_; }
  void clear_dirty() { dirty_ = false; }

 private:
  struct Slot {
    uint32_t ip = 0;
    bool used = false;
    uint32_t seen = 0;  // use order, for replacing the oldest slot
    PinGuard guard;
  };
  const Slot* find(uint32_t ip) const;
  Slot* find_or_add(uint32_t ip);

  Slot slots_[kSlots];
  PinGuard global_;
  uint32_t tick_ = 0;
  bool dirty_ = false;
};

}  // namespace gs
