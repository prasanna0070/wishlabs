// Crash-safe memory record (favourites + parent settings).
//
// Design (QB-43 equivalent on a microcontroller):
// - Two slots ("memA", "memB") hold complete, versioned records: magic, sequence number,
//   length, payload, CRC-32. A change is written to the slot that does NOT hold the current
//   record, read back and verified before it becomes current. A power cut mid-write leaves the
//   previous record intact in the other slot.
// - On load, the valid record with the highest sequence number wins. If records exist but none
//   is valid, the store reports Corrupt and REFUSES every write, so corrupt data is never
//   silently replaced. Only an explicit parent "reset memory" action clears it.
// - The backend (KvBackend) is NVS on the device and an in-memory map in the unit tests.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "gs_names.h"

namespace gs {

constexpr uint8_t kMaxFavourites = 12;
constexpr size_t kAppBytes = 16;
constexpr size_t kRecordMax = 512;

enum FavFlags : uint8_t { kFavHasName = 1, kFavHasClip = 2 };

// Memory.app[] byte the story_player uses for its pending-wipe flag (MAJ-17): set when clip files
// could not be deleted (card absent or a remove failed); the next card mount finishes the wipe.
constexpr size_t kAppPendingWipe = 15;

struct Favourite {
  uint8_t id = 0;     // 1..255, stable for the clip file name /fav/<id>.wav
  uint8_t flags = 0;  // FavFlags
  char name[kNameMax + 1] = {0};
};

struct Memory {
  uint8_t count = 0;
  uint8_t next_id = 1;
  uint8_t volume = 35;          // software scale 0..100 (not a measured sound level)
  uint8_t night_minutes = 20;   // night-light timer
  uint8_t night_level = 12;     // percent of LED full scale
  uint8_t night_enabled = 1;
  int8_t last_story = -1;       // index of the last story played
  uint8_t last_hero = 0;        // favourite id used last, 0 = none
  uint8_t app[kAppBytes] = {0}; // small template-specific settings
  Favourite fav[kMaxFavourites];
};

struct MemoryLimits {
  uint8_t max_favourites = kMaxFavourites;
  uint8_t volume_max = 60;
  uint8_t night_minutes_max = 120;
  uint8_t night_level_max = 60;
};

class KvBackend {
 public:
  virtual ~KvBackend() = default;
  // Returns bytes read, -1 if the key is absent, -2 on a storage error.
  virtual int get(const char* key, uint8_t* buf, size_t cap) = 0;
  // Returns true only when the value is durably stored.
  virtual bool put(const char* key, const uint8_t* buf, size_t len) = 0;
  virtual bool erase(const char* key) = 0;
};

enum class StoreStatus : uint8_t {
  Empty,         // nothing saved yet: defaults in use
  Ok,            // a valid record loaded
  Recovered,     // one slot was damaged; the other valid record loaded (last change may be lost)
  Corrupt,       // records exist but none is valid: writes refused until a parent resets memory
  StorageError,  // the backend failed to read: writes refused
};

enum class StoreResult : uint8_t {
  Ok,
  NoChange,      // e.g. adding a name that is already saved (case-insensitive)
  InvalidName,
  Full,
  NotFound,
  OutOfRange,
  Refused,       // memory is corrupt or unreadable
  StorageError,  // the write or its read-back verification failed; nothing changed
};

const char* store_status_name(StoreStatus s);
const char* store_result_name(StoreResult r);

class MemoryStore {
 public:
  MemoryStore(KvBackend& kv, const MemoryLimits& limits, const Memory& defaults);

  StoreStatus load();
  StoreStatus status() const { return status_; }
  bool writable() const { return status_ != StoreStatus::Corrupt && status_ != StoreStatus::StorageError; }
  const Memory& mem() const { return mem_; }
  const MemoryLimits& limits() const { return limits_; }
  uint32_t seq() const { return seq_; }
  const Favourite* find(uint8_t id) const;
  const Favourite* find_name(const char* name) const;

  StoreResult add_name(const char* name, uint8_t* id_out = nullptr);
  StoreResult add_clip(uint8_t* id_out);
  StoreResult set_name(uint8_t id, const char* name);  // empty name clears it (clip must remain)
  StoreResult remove(uint8_t id);
  StoreResult erase_all();
  // Drops the clip of each listed favourite in ONE commit: a favourite that also has a typed name
  // keeps the name; a clip-only favourite is removed. *changed = how many records changed.
  StoreResult drop_clips(const uint8_t* ids, uint8_t n, uint8_t* changed = nullptr);
  StoreResult set_volume(uint8_t volume);
  StoreResult set_night(bool enabled, uint8_t minutes, uint8_t level);
  StoreResult note_played(int8_t story, uint8_t hero_id);
  StoreResult set_app_byte(size_t index, uint8_t value);
  // Parent action: wipe both slots and write defaults. The only way out of Corrupt.
  StoreResult reset_memory();

  // Exposed for tests.
  static size_t encode(const Memory& m, uint32_t seq, uint8_t* buf, size_t cap);
  static bool decode(const uint8_t* buf, size_t len, Memory* out, uint32_t* seq);
  bool validate(const Memory& m) const;

 private:
  StoreResult commit(const Memory& next);
  uint8_t alloc_id(const Memory& m) const;
  void clamp_to_limits(Memory* m) const;

  KvBackend& kv_;
  MemoryLimits limits_;
  Memory defaults_;
  Memory mem_;
  StoreStatus status_ = StoreStatus::Empty;
  uint32_t seq_ = 0;
  int active_ = -1;  // 0 = memA, 1 = memB, -1 = none yet
};

}  // namespace gs
