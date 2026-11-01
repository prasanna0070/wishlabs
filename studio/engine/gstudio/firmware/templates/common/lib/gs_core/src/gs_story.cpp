#include "gs_story.h"

#include <stdio.h>
#include <string.h>

namespace gs {

const char* capture_mode_name(CaptureMode m) {
  switch (m) {
    case CaptureMode::ChildVoiceClip: return "child_voice_clip";
    case CaptureMode::ParentWeb: return "parent_web";
    default: return "none";
  }
}

static bool valid_story_id(const char* id) {
  if (!id || !id[0]) return false;
  size_t n = 0;
  for (const char* p = id; *p; ++p, ++n) {
    const char c = *p;
    const bool ok = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-';
    if (!ok || n >= kStoryIdMax) return false;
  }
  return true;
}

bool StoryCatalog::add(const char* id, const char* title, uint8_t segments, uint16_t friend_mask) {
  if (count_ >= kMaxStories || !valid_story_id(id) || segments == 0 || segments > kMaxSegments) return false;
  if (index_of(id) >= 0) return false;
  StoryInfo& s = items_[count_];
  copy_str(s.id, sizeof(s.id), id);
  copy_str(s.title, sizeof(s.title), title ? title : id);
  s.segments = segments;
  s.friend_mask = uint16_t(friend_mask & ((1u << (segments - 1)) - 1u));
  ++count_;
  return true;
}

int StoryCatalog::index_of(const char* id) const {
  for (size_t i = 0; i < count_; ++i)
    if (strcmp(items_[i].id, id) == 0) return static_cast<int>(i);
  return -1;
}

int pick_story(size_t count, int last, Rng& rng, bool sequential) {
  if (count == 0) return -1;
  if (count == 1) return 0;
  if (sequential) return (last < 0 || last >= static_cast<int>(count) - 1) ? 0 : last + 1;
  if (last < 0 || last >= static_cast<int>(count)) return static_cast<int>(rng.below(static_cast<uint32_t>(count)));
  // Choose among the other count-1 stories uniformly, never the last one.
  int k = static_cast<int>(rng.below(static_cast<uint32_t>(count - 1)));
  return k >= last ? k + 1 : k;
}

void story_segment_path(const char* story_id, uint8_t seg, char* out, size_t cap) {
  snprintf(out, cap, "/stories/%s/s%02u.wav", story_id, static_cast<unsigned>(seg));
}

void fav_clip_path(uint8_t fav_id, char* out, size_t cap) {
  snprintf(out, cap, "/fav/f%03u.wav", static_cast<unsigned>(fav_id));
}

void pack_name_path(const char* name, char* out, size_t cap) {
  char slug[kNameMax + 1];
  name_slug(name, slug, sizeof(slug));
  if (!slug[0]) {
    if (cap) out[0] = '\0';
    return;
  }
  snprintf(out, cap, "/names/%s.wav", slug);
}

bool favourite_insert_path(const Favourite& f, CaptureMode mode, FileExistsFn exists, void* ctx, char* out,
                           size_t cap) {
  if (cap) out[0] = '\0';
  char path[kPathMax];
  path[0] = '\0';
  if (mode == CaptureMode::ChildVoiceClip && (f.flags & kFavHasClip)) {
    fav_clip_path(f.id, path, sizeof(path));
    if (exists && exists(path, ctx)) return copy_str(out, cap, path);
  }
  // A typed name (parent page) can use the story pack's name clip in any mode.
  if (f.flags & kFavHasName) {
    pack_name_path(f.name, path, sizeof(path));
    if (path[0] && exists && exists(path, ctx)) return copy_str(out, cap, path);
  }
  return false;
}

uint8_t pick_hero(const Memory& mem, CaptureMode mode, FileExistsFn exists, void* ctx, Rng& rng) {
  uint8_t usable[kMaxFavourites];
  uint8_t n = 0;
  char tmp[kPathMax];
  for (uint8_t i = 0; i < mem.count && i < kMaxFavourites; ++i) {
    if (favourite_insert_path(mem.fav[i], mode, exists, ctx, tmp, sizeof(tmp))) usable[n++] = mem.fav[i].id;
  }
  if (n == 0) return 0;
  if (n == 1) return usable[0];
  uint8_t pool[kMaxFavourites];
  uint8_t m = 0;
  for (uint8_t i = 0; i < n; ++i)
    if (usable[i] != mem.last_hero) pool[m++] = usable[i];
  if (m == 0) return usable[rng.below(n)];
  return pool[rng.below(m)];
}

uint8_t pick_friend(const Memory& mem, CaptureMode mode, FileExistsFn exists, void* ctx, Rng& rng, uint8_t hero_id) {
  uint8_t usable[kMaxFavourites];
  uint8_t n = 0;
  char tmp[kPathMax];
  for (uint8_t i = 0; i < mem.count && i < kMaxFavourites; ++i) {
    if (mem.fav[i].id == hero_id) continue;
    if (favourite_insert_path(mem.fav[i], mode, exists, ctx, tmp, sizeof(tmp))) usable[n++] = mem.fav[i].id;
  }
  return n ? usable[rng.below(n)] : 0;
}

static bool insert_for(const Memory& mem, uint8_t id, CaptureMode mode, FileExistsFn exists, void* ctx, char* out,
                       size_t cap) {
  for (uint8_t i = 0; i < mem.count && id; ++i)
    if (mem.fav[i].id == id) return favourite_insert_path(mem.fav[i], mode, exists, ctx, out, cap);
  return false;
}

size_t build_playlist(const StoryInfo& story, const Memory& mem, uint8_t hero_id, uint8_t friend_id, CaptureMode mode,
                      FileExistsFn exists, void* ctx, PlayItem* out, size_t cap) {
  char hero[kPathMax];
  char pal[kPathMax];
  hero[0] = pal[0] = '\0';
  bool have_hero = insert_for(mem, hero_id, mode, exists, ctx, hero, sizeof(hero));
  if (!have_hero && exists && exists(kDefaultHeroClip, ctx)) have_hero = copy_str(hero, sizeof(hero), kDefaultHeroClip);
  bool have_friend = friend_id != hero_id && insert_for(mem, friend_id, mode, exists, ctx, pal, sizeof(pal));
  if (!have_friend && exists && exists(kDefaultFriendClip, ctx)) have_friend = copy_str(pal, sizeof(pal), kDefaultFriendClip);
  size_t n = 0;
  for (uint8_t seg = 0; seg < story.segments && n < cap; ++seg) {
    if (seg > 0 && n < cap) {
      const bool is_friend = (story.friend_mask >> (seg - 1)) & 1u;
      const char* name = is_friend ? (have_friend ? pal : (have_hero ? hero : nullptr)) : (have_hero ? hero : nullptr);
      PlayItem& slot = out[n++];
      if (name) {
        slot.kind = PlayKind::Name;
        copy_str(slot.path, sizeof(slot.path), name);
      } else {
        slot.kind = PlayKind::Chime;
        slot.path[0] = '\0';
      }
    }
    if (n >= cap) break;
    PlayItem& item = out[n++];
    item.kind = PlayKind::Segment;
    story_segment_path(story.id, seg, item.path, sizeof(item.path));
  }
  return n;
}

}  // namespace gs
