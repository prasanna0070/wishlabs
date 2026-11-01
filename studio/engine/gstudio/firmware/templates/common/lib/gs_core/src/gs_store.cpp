#include "gs_store.h"

#include "gs_util.h"

namespace gs {

static const char* const kSlotKey[2] = {"memA", "memB"};
static const uint8_t kMagic[4] = {'G', 'S', 'M', '1'};

const char* store_status_name(StoreStatus s) {
  switch (s) {
    case StoreStatus::Empty: return "empty";
    case StoreStatus::Ok: return "ok";
    case StoreStatus::Recovered: return "recovered";
    case StoreStatus::Corrupt: return "corrupt";
    case StoreStatus::StorageError: return "storage_error";
  }
  return "unknown";
}

const char* store_result_name(StoreResult r) {
  switch (r) {
    case StoreResult::Ok: return "ok";
    case StoreResult::NoChange: return "no_change";
    case StoreResult::InvalidName: return "invalid_name";
    case StoreResult::Full: return "full";
    case StoreResult::NotFound: return "not_found";
    case StoreResult::OutOfRange: return "out_of_range";
    case StoreResult::Refused: return "refused";
    case StoreResult::StorageError: return "storage_error";
  }
  return "unknown";
}

MemoryStore::MemoryStore(KvBackend& kv, const MemoryLimits& limits, const Memory& defaults)
    : kv_(kv), limits_(limits), defaults_(defaults), mem_(defaults) {
  if (limits_.max_favourites > kMaxFavourites) limits_.max_favourites = kMaxFavourites;
  clamp_to_limits(&defaults_);
  mem_ = defaults_;
}

size_t MemoryStore::encode(const Memory& m, uint32_t seq, uint8_t* buf, size_t cap) {
  ByteWriter w(buf, cap);
  w.bytes(kMagic, 4);
  w.u32(seq);
  w.u16(0);  // payload length, patched below
  const size_t payload_start = w.size();
  w.u8(m.count);
  w.u8(m.next_id);
  w.u8(m.volume);
  w.u8(m.night_minutes);
  w.u8(m.night_level);
  w.u8(m.night_enabled);
  w.u8(static_cast<uint8_t>(m.last_story));
  w.u8(m.last_hero);
  w.bytes(m.app, kAppBytes);
  for (uint8_t i = 0; i < m.count && i < kMaxFavourites; ++i) {
    const Favourite& f = m.fav[i];
    size_t len = 0;
    while (len < kNameMax && f.name[len]) ++len;
    w.u8(f.id);
    w.u8(f.flags);
    w.u8(static_cast<uint8_t>(len));
    w.bytes(f.name, len);
  }
  if (!w.ok()) return 0;
  const size_t payload_len = w.size() - payload_start;
  buf[8] = uint8_t(payload_len);
  buf[9] = uint8_t(payload_len >> 8);
  const uint32_t crc = crc32(buf, w.size());
  w.u32(crc);
  return w.ok() ? w.size() : 0;
}

bool MemoryStore::decode(const uint8_t* buf, size_t len, Memory* out, uint32_t* seq) {
  if (!buf || len < 10 + 24 + 4) return false;
  for (int i = 0; i < 4; ++i)
    if (buf[i] != kMagic[i]) return false;
  ByteReader r(buf, len);
  uint8_t magic[4];
  r.bytes(magic, 4);
  const uint32_t s = r.u32();
  const uint16_t payload_len = r.u16();
  if (size_t(10) + payload_len + 4 != len) return false;
  const uint32_t want = crc32(buf, 10 + payload_len);
  const uint32_t got = uint32_t(buf[len - 4]) | (uint32_t(buf[len - 3]) << 8) | (uint32_t(buf[len - 2]) << 16) |
                       (uint32_t(buf[len - 1]) << 24);
  if (want != got) return false;

  Memory m;
  m.count = r.u8();
  m.next_id = r.u8();
  m.volume = r.u8();
  m.night_minutes = r.u8();
  m.night_level = r.u8();
  m.night_enabled = r.u8();
  m.last_story = static_cast<int8_t>(r.u8());
  m.last_hero = r.u8();
  r.bytes(m.app, kAppBytes);
  if (!r.ok() || m.count > kMaxFavourites || m.volume > 100 || m.night_enabled > 1) return false;
  for (uint8_t i = 0; i < m.count; ++i) {
    Favourite& f = m.fav[i];
    f.id = r.u8();
    f.flags = r.u8();
    const uint8_t nlen = r.u8();
    if (!r.ok() || nlen > kNameMax || f.id == 0 || (f.flags & ~(kFavHasName | kFavHasClip)) || f.flags == 0)
      return false;
    r.bytes(f.name, nlen);
    f.name[nlen] = '\0';
    if (!r.ok()) return false;
    const bool has_name = (f.flags & kFavHasName) != 0;
    if (has_name != (nlen > 0)) return false;
    if (has_name) {
      char clean[kNameMax + 1];
      if (!clean_name(f.name, clean, sizeof(clean))) return false;
      for (size_t k = 0; k <= nlen; ++k)
        if (clean[k] != f.name[k]) return false;
    }
    for (uint8_t j = 0; j < i; ++j)
      if (m.fav[j].id == f.id) return false;
  }
  if (r.pos() != size_t(10) + payload_len) return false;
  *out = m;
  if (seq) *seq = s;
  return true;
}

bool MemoryStore::validate(const Memory& m) const {
  uint8_t buf[kRecordMax];
  const size_t n = encode(m, 1, buf, sizeof(buf));
  if (n == 0) return false;
  Memory check;
  if (!decode(buf, n, &check, nullptr)) return false;
  if (m.count > limits_.max_favourites) return false;
  if (m.volume > limits_.volume_max) return false;
  if (m.night_minutes > limits_.night_minutes_max || m.night_level > limits_.night_level_max) return false;
  return true;
}

void MemoryStore::clamp_to_limits(Memory* m) const {
  if (m->volume > limits_.volume_max) m->volume = limits_.volume_max;
  if (m->night_minutes > limits_.night_minutes_max) m->night_minutes = limits_.night_minutes_max;
  if (m->night_level > limits_.night_level_max) m->night_level = limits_.night_level_max;
  if (m->next_id == 0) m->next_id = 1;
}

StoreStatus MemoryStore::load() {
  bool present[2] = {false, false};
  bool valid[2] = {false, false};
  bool read_error = false;
  Memory slot_mem[2];
  uint32_t slot_seq[2] = {0, 0};
  for (int i = 0; i < 2; ++i) {
    uint8_t buf[kRecordMax];
    const int n = kv_.get(kSlotKey[i], buf, sizeof(buf));
    if (n == -1) continue;
    if (n < 0) {
      read_error = true;
      continue;
    }
    present[i] = true;
    valid[i] = decode(buf, static_cast<size_t>(n), &slot_mem[i], &slot_seq[i]);
  }
  int pick = -1;
  if (valid[0] && valid[1]) {
    pick = static_cast<int32_t>(slot_seq[1] - slot_seq[0]) > 0 ? 1 : 0;
  } else if (valid[0]) {
    pick = 0;
  } else if (valid[1]) {
    pick = 1;
  }
  if (pick >= 0) {
    mem_ = slot_mem[pick];
    if (mem_.count > limits_.max_favourites) {
      // A record from a build with a larger limit: keep it readable but do not drop names.
      limits_.max_favourites = mem_.count;
    }
    clamp_to_limits(&mem_);
    seq_ = slot_seq[pick];
    active_ = pick;
    const int other = 1 - pick;
    status_ = (present[other] && !valid[other]) || read_error ? StoreStatus::Recovered : StoreStatus::Ok;
    return status_;
  }
  mem_ = defaults_;
  seq_ = 0;
  active_ = -1;
  if (read_error) {
    status_ = StoreStatus::StorageError;
  } else if (present[0] || present[1]) {
    status_ = StoreStatus::Corrupt;
  } else {
    status_ = StoreStatus::Empty;
  }
  return status_;
}

StoreResult MemoryStore::commit(const Memory& next) {
  if (!writable()) return StoreResult::Refused;
  if (!validate(next)) return StoreResult::OutOfRange;
  const uint32_t new_seq = seq_ + 1;
  const int target = active_ == 0 ? 1 : 0;
  uint8_t buf[kRecordMax];
  const size_t n = encode(next, new_seq, buf, sizeof(buf));
  if (n == 0) return StoreResult::StorageError;
  if (!kv_.put(kSlotKey[target], buf, n)) return StoreResult::StorageError;
  uint8_t back[kRecordMax];
  const int got = kv_.get(kSlotKey[target], back, sizeof(back));
  if (got != static_cast<int>(n)) return StoreResult::StorageError;
  for (size_t i = 0; i < n; ++i)
    if (back[i] != buf[i]) return StoreResult::StorageError;
  mem_ = next;
  seq_ = new_seq;
  active_ = target;
  status_ = StoreStatus::Ok;
  return StoreResult::Ok;
}

const Favourite* MemoryStore::find(uint8_t id) const {
  for (uint8_t i = 0; i < mem_.count; ++i)
    if (mem_.fav[i].id == id) return &mem_.fav[i];
  return nullptr;
}

const Favourite* MemoryStore::find_name(const char* name) const {
  for (uint8_t i = 0; i < mem_.count; ++i)
    if ((mem_.fav[i].flags & kFavHasName) && name_equal_ci(mem_.fav[i].name, name)) return &mem_.fav[i];
  return nullptr;
}

uint8_t MemoryStore::alloc_id(const Memory& m) const {
  uint8_t id = m.next_id ? m.next_id : 1;
  for (int tries = 0; tries < 255; ++tries) {
    bool used = false;
    for (uint8_t i = 0; i < m.count; ++i) used = used || m.fav[i].id == id;
    if (!used) return id;
    id = static_cast<uint8_t>(id == 255 ? 1 : id + 1);
  }
  return 0;
}

StoreResult MemoryStore::add_name(const char* name, uint8_t* id_out) {
  if (!writable()) return StoreResult::Refused;
  char clean[kNameMax + 1];
  if (!clean_name(name, clean, sizeof(clean))) return StoreResult::InvalidName;
  if (const Favourite* f = find_name(clean)) {
    if (id_out) *id_out = f->id;
    return StoreResult::NoChange;
  }
  if (mem_.count >= limits_.max_favourites) return StoreResult::Full;
  Memory next = mem_;
  const uint8_t id = alloc_id(next);
  if (!id) return StoreResult::Full;
  Favourite& f = next.fav[next.count++];
  f = Favourite();
  f.id = id;
  f.flags = kFavHasName;
  copy_str(f.name, sizeof(f.name), clean);
  next.next_id = static_cast<uint8_t>(id == 255 ? 1 : id + 1);
  const StoreResult r = commit(next);
  if (r == StoreResult::Ok && id_out) *id_out = id;
  return r;
}

StoreResult MemoryStore::add_clip(uint8_t* id_out) {
  if (!writable()) return StoreResult::Refused;
  if (mem_.count >= limits_.max_favourites) return StoreResult::Full;
  Memory next = mem_;
  const uint8_t id = alloc_id(next);
  if (!id) return StoreResult::Full;
  Favourite& f = next.fav[next.count++];
  f = Favourite();
  f.id = id;
  f.flags = kFavHasClip;
  next.next_id = static_cast<uint8_t>(id == 255 ? 1 : id + 1);
  const StoreResult r = commit(next);
  if (r == StoreResult::Ok && id_out) *id_out = id;
  return r;
}

StoreResult MemoryStore::set_name(uint8_t id, const char* name) {
  if (!writable()) return StoreResult::Refused;
  Memory next = mem_;
  Favourite* f = nullptr;
  for (uint8_t i = 0; i < next.count; ++i)
    if (next.fav[i].id == id) f = &next.fav[i];
  if (!f) return StoreResult::NotFound;
  if (!name || !name[0]) {
    if (!(f->flags & kFavHasClip)) return StoreResult::InvalidName;  // would leave an empty favourite
    f->flags = kFavHasClip;
    f->name[0] = '\0';
    return commit(next);
  }
  char clean[kNameMax + 1];
  if (!clean_name(name, clean, sizeof(clean))) return StoreResult::InvalidName;
  const Favourite* same = find_name(clean);
  if (same && same->id != id) return StoreResult::NoChange;
  f->flags = static_cast<uint8_t>(f->flags | kFavHasName);
  copy_str(f->name, sizeof(f->name), clean);
  return commit(next);
}

StoreResult MemoryStore::remove(uint8_t id) {
  if (!writable()) return StoreResult::Refused;
  Memory next = mem_;
  uint8_t w = 0;
  bool found = false;
  for (uint8_t i = 0; i < next.count; ++i) {
    if (next.fav[i].id == id) {
      found = true;
      continue;
    }
    next.fav[w++] = next.fav[i];
  }
  if (!found) return StoreResult::NotFound;
  for (uint8_t i = w; i < next.count; ++i) next.fav[i] = Favourite();
  next.count = w;
  if (next.last_hero == id) next.last_hero = 0;
  return commit(next);
}

StoreResult MemoryStore::erase_all() {
  if (!writable()) return StoreResult::Refused;
  Memory next = mem_;
  for (uint8_t i = 0; i < kMaxFavourites; ++i) next.fav[i] = Favourite();
  next.count = 0;
  next.last_hero = 0;
  return commit(next);
}

StoreResult MemoryStore::set_volume(uint8_t volume) {
  if (!writable()) return StoreResult::Refused;
  if (volume > limits_.volume_max) return StoreResult::OutOfRange;
  if (volume == mem_.volume) return StoreResult::NoChange;
  Memory next = mem_;
  next.volume = volume;
  return commit(next);
}

StoreResult MemoryStore::set_night(bool enabled, uint8_t minutes, uint8_t level) {
  if (!writable()) return StoreResult::Refused;
  if (minutes > limits_.night_minutes_max || level > limits_.night_level_max) return StoreResult::OutOfRange;
  Memory next = mem_;
  next.night_enabled = enabled ? 1 : 0;
  next.night_minutes = minutes;
  next.night_level = level;
  return commit(next);
}

StoreResult MemoryStore::note_played(int8_t story, uint8_t hero_id) {
  if (!writable()) return StoreResult::Refused;
  if (story == mem_.last_story && hero_id == mem_.last_hero) return StoreResult::NoChange;
  Memory next = mem_;
  next.last_story = story;
  next.last_hero = hero_id;
  return commit(next);
}

StoreResult MemoryStore::set_app_byte(size_t index, uint8_t value) {
  if (!writable()) return StoreResult::Refused;
  if (index >= kAppBytes) return StoreResult::OutOfRange;
  if (mem_.app[index] == value) return StoreResult::NoChange;
  Memory next = mem_;
  next.app[index] = value;
  return commit(next);
}

StoreResult MemoryStore::reset_memory() {
  const bool a = kv_.erase(kSlotKey[0]);
  const bool b = kv_.erase(kSlotKey[1]);
  if (!a || !b) return StoreResult::StorageError;
  mem_ = defaults_;
  seq_ = 0;
  active_ = -1;
  status_ = StoreStatus::Empty;
  return commit(defaults_);
}

}  // namespace gs
