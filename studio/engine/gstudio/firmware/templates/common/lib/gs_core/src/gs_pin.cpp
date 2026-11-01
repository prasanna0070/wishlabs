#include "gs_pin.h"

#include "gs_util.h"

namespace gs {

bool pin_format_ok(const char* pin, uint8_t min_digits) {
  if (!pin) return false;
  int n = 0;
  for (const char* p = pin; *p; ++p, ++n) {
    if (*p < '0' || *p > '9' || n >= kPinMaxDigits) return false;
  }
  return n >= min_digits;
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
  if (++failures_ >= free_tries_) {
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

void PinGuard::restore(uint8_t failures, uint8_t locks, uint32_t remaining_ms, uint32_t now) {
  failures_ = failures < free_tries_ ? failures : uint8_t(free_tries_ - 1);
  locks_ = locks > 8 ? 8 : locks;
  if (remaining_ms > kMaxLockMs) remaining_ms = kMaxLockMs;
  lock_on_ = remaining_ms > 0;
  lock_until_ = now + remaining_ms;
}

// ------------------------------------------------------------------ table

const PinGuardTable::Slot* PinGuardTable::find(uint32_t ip) const {
  for (const Slot& s : slots_)
    if (s.used && s.ip == ip) return &s;
  return nullptr;
}

PinGuardTable::Slot* PinGuardTable::find_or_add(uint32_t ip) {
  Slot* oldest = &slots_[0];
  for (Slot& s : slots_) {
    if (s.used && s.ip == ip) return &s;
    if (!s.used) {
      oldest = &s;
      break;
    }
    if (s.seen < oldest->seen) oldest = &s;
  }
  *oldest = Slot();
  oldest->used = true;
  oldest->ip = ip;
  return oldest;
}

bool PinGuardTable::locked(uint32_t ip, uint32_t now) const {
  if (global_.locked(now)) return true;
  const Slot* s = find(ip);
  return s && s->guard.locked(now);
}

uint32_t PinGuardTable::retry_after_ms(uint32_t ip, uint32_t now) const {
  uint32_t ms = global_.retry_after_ms(now);
  const Slot* s = find(ip);
  if (s && s->guard.retry_after_ms(now) > ms) ms = s->guard.retry_after_ms(now);
  return ms;
}

void PinGuardTable::failure(uint32_t ip, uint32_t now) {
  Slot* s = find_or_add(ip);
  s->seen = ++tick_;
  s->guard.failure(now);
  global_.failure(now);
  dirty_ = true;
}

void PinGuardTable::success(uint32_t ip) {
  Slot* s = find_or_add(ip);
  s->seen = ++tick_;
  if (s->guard.failures() || s->guard.locks()) dirty_ = true;
  s->guard.success();
  // A right PIN does not clear the device-wide count: other addresses may still be guessing.
}

uint8_t PinGuardTable::failures(uint32_t ip) const {
  const Slot* s = find(ip);
  return s ? s->guard.failures() : 0;
}

size_t PinGuardTable::save(uint8_t* buf, size_t cap, uint32_t now) const {
  if (cap < kBlobBytes) return 0;
  ByteWriter w(buf, cap);
  w.u8(1);  // version
  w.u8(global_.failures());
  w.u8(global_.locks());
  w.u32(global_.retry_after_ms(now));
  for (const Slot& s : slots_) {
    w.u32(s.used ? s.ip : 0);
    w.u8(s.used ? s.guard.failures() : 0);
    w.u8(s.used ? s.guard.locks() : 0);
    w.u32(s.used ? s.guard.retry_after_ms(now) : 0);
  }
  const uint32_t crc = crc32(buf, w.size());
  w.u32(crc);
  return w.ok() ? w.size() : 0;
}

bool PinGuardTable::load(const uint8_t* buf, size_t len, uint32_t now) {
  if (!buf || len != kBlobBytes) return false;
  ByteReader crc_r(buf + kBlobBytes - 4, 4);
  if (crc32(buf, kBlobBytes - 4) != crc_r.u32()) return false;
  ByteReader r(buf, kBlobBytes - 4);
  if (r.u8() != 1) return false;
  const uint8_t gf = r.u8();
  const uint8_t gl = r.u8();
  const uint32_t gms = r.u32();
  global_ = PinGuard(kGlobalFreeTries);
  global_.restore(gf, gl, gms, now);
  for (Slot& s : slots_) {
    const uint32_t ip = r.u32();
    const uint8_t f = r.u8();
    const uint8_t l = r.u8();
    const uint32_t ms = r.u32();
    s = Slot();
    if (ip || f || l || ms) {
      s.used = true;
      s.ip = ip;
      s.seen = ++tick_;
      s.guard.restore(f, l, ms, now);
    }
  }
  dirty_ = false;
  return r.ok();
}

}  // namespace gs
