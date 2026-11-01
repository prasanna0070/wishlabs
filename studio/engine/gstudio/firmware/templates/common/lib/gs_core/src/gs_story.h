// Story sequencing: which story next, which favourites star in it, and the exact list of audio
// files to play. A story's text is split at its name slots into segments: {FAV} (the hero) and
// {FRIEND} (a second favourite). Segment k is played, then the name for slot k, then segment k+1.
// One audio file per segment.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "gs_store.h"
#include "gs_util.h"

namespace gs {

constexpr size_t kMaxStories = 24;
constexpr size_t kStoryIdMax = 23;
constexpr size_t kStoryTitleMax = 47;
constexpr size_t kPathMax = 64;
constexpr size_t kMaxSegments = 12;
constexpr size_t kMaxPlayItems = kMaxSegments * 2;

enum class CaptureMode : uint8_t { None = 0, ChildVoiceClip = 1, ParentWeb = 2 };
const char* capture_mode_name(CaptureMode m);

struct StoryInfo {
  char id[kStoryIdMax + 1] = {0};
  char title[kStoryTitleMax + 1] = {0};
  uint8_t segments = 0;      // number of audio segments; name slots = segments - 1
  uint16_t friend_mask = 0;  // bit k set: slot k (after segment k) is {FRIEND}, else {FAV}
};

class StoryCatalog {
 public:
  bool add(const char* id, const char* title, uint8_t segments, uint16_t friend_mask = 0);
  size_t count() const { return count_; }
  const StoryInfo* get(size_t i) const { return i < count_ ? &items_[i] : nullptr; }
  int index_of(const char* id) const;
  void clear() { count_ = 0; }

 private:
  StoryInfo items_[kMaxStories];
  size_t count_ = 0;
};

// Next story index: shuffled with no immediate repeat (when more than one story exists), or
// sequential. Returns -1 when the catalog is empty.
int pick_story(size_t count, int last, Rng& rng, bool sequential);

enum class PlayKind : uint8_t { Segment, Name, Chime };

struct PlayItem {
  PlayKind kind = PlayKind::Segment;
  char path[kPathMax] = {0};  // empty for Chime
};

// Answers "does this file exist on the card?" for the playlist builder.
typedef bool (*FileExistsFn)(const char* path, void* ctx);

// Paths used on the microSD card.
void story_segment_path(const char* story_id, uint8_t seg, char* out, size_t cap);  // /stories/<id>/sNN.wav
void fav_clip_path(uint8_t fav_id, char* out, size_t cap);                         // /fav/f<id>.wav
void pack_name_path(const char* name, char* out, size_t cap);                     // /names/<slug>.wav
constexpr const char* kDefaultHeroClip = "/names/_default.wav";
constexpr const char* kDefaultFriendClip = "/names/_friend.wav";

// The audio to insert for one favourite under a capture mode ("" means no usable insert).
// ChildVoiceClip -> the child's own recorded clip; ParentWeb -> the story pack's name clip.
bool favourite_insert_path(const Favourite& f, CaptureMode mode, FileExistsFn exists, void* ctx, char* out,
                           size_t cap);

// Chooses the favourite to star in the next story: random among favourites with a usable insert,
// avoiding the last hero when there is a choice. Returns the favourite id, or 0 for "no favourite"
// (the default hero clip /names/_default.wav, else a soft chime, is used).
uint8_t pick_hero(const Memory& mem, CaptureMode mode, FileExistsFn exists, void* ctx, Rng& rng);

// Chooses a second favourite for the {FRIEND} slots: random among the other favourites with a
// usable insert. Returns 0 when there is none (the default friend clip, else the hero, is used).
uint8_t pick_friend(const Memory& mem, CaptureMode mode, FileExistsFn exists, void* ctx, Rng& rng, uint8_t hero_id);

// Builds the full playlist for one story. Returns the number of items written.
// {FAV} slots: the hero's insert, else /names/_default.wav, else a soft chime.
// {FRIEND} slots: the friend's insert, else /names/_friend.wav, else the {FAV} insert.
size_t build_playlist(const StoryInfo& story, const Memory& mem, uint8_t hero_id, uint8_t friend_id, CaptureMode mode,
                      FileExistsFn exists, void* ctx, PlayItem* out, size_t cap);

}  // namespace gs
