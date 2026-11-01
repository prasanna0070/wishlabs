// story_player: bedtime stories from the microSD story pack, starring remembered favourites.
//
// Tap: a story (tap again to stop), with the night-light glow. Stories have a hero ({FAV}) and a
// friend ({FRIEND}) slot, each filled with a favourite's spoken name.
// parent_web mode (the default): names are typed on the parent page (or, with the network off,
// come from the story card made in the app); each plays as its spoken name clip from the card
// (/names/<slug>.wav), else the default hero / friend clip, else a soft chime. No microphone use.
// child_voice_clip mode (opt-in, needs the parent's consent in the brief): hold until the light
// turns listening-blue, hear "tell me a name", say it (up to GS_CFG_CLIP_MS); the clip plays back;
// tap within GS_CFG_KEEP_WINDOW_MS to keep it. Kept clips are AES-256 encrypted on the card with a
// key that never leaves this device, zeroed before they are deleted, and expire after
// GS_CFG_CLIP_RETENTION_HOURS powered-on hours. Nothing is sent to the internet.
#include <ArduinoJson.h>
#include <FS.h>
#include <esp_random.h>

#include "config.h"
#include "gs_app.h"
#include "gs_audio.h"
#include "gs_board.h"
#include "gs_clip.h"
#include "gs_clips.h"
#include "gs_device.h"
#include "gs_display.h"
#include "gs_fsfile.h"
#include "gs_led.h"
#include "gs_light.h"
#include "gs_listen.h"
#include "gs_story.h"
#include "gs_story_ctl.h"
#include "gs_templates.h"
#include "hardware.h"

#if GS_HAS_SD
#include <SD.h>
#endif

#if !GS_HAS_AMP
#error "story_player needs a speaker with the MAX98357A amplifier"
#endif

static const char* kTmpClip = "/fav/tmp.wav";
// Spoken prompts from the story pack (MIN-38); a chime plays when the card has none.
static const char* kPromptHello = "/prompts/hello.wav";
static const char* kPromptHold = "/prompts/hold_to_tell.wav";
static const char* kPromptKeep = "/prompts/tap_to_keep.wav";
static const char* kPromptSaved = "/prompts/saved.wav";
static const char* kPromptTryAgain = "/prompts/try_again.wav";

static bool card_exists(const char* path, void*) { return gsboard::sd_exists(path); }

// Collects up to kBatch card paths per pass, so deleting never depends on a fixed file count.
struct PathBatch {
  static constexpr int kBatch = 24;
  char path[kBatch][gs::kPathMax];
  int n = 0;
  bool more = false;
  bool (*keep)(const char* name, void* ctx) = nullptr;
  void* ctx = nullptr;
};

static void collect_fav(const char* name, void* ctx) {
  PathBatch* b = static_cast<PathBatch*>(ctx);
  if (b->keep && b->keep(name, b->ctx)) return;
  if (b->n >= PathBatch::kBatch) {
    b->more = true;
    return;
  }
  snprintf(b->path[b->n++], gs::kPathMax, "/fav/%s", name);
}

static int clip_id_from_name(const char* name) {
  // "f007.wav" -> 7; anything else -> -1
  if (strlen(name) != 8 || name[0] != 'f' || strcmp(name + 4, ".wav") != 0) return -1;
  int id = 0;
  for (int i = 1; i < 4; ++i) {
    if (name[i] < '0' || name[i] > '9') return -1;
    id = id * 10 + (name[i] - '0');
  }
  return id >= 1 && id <= 255 ? id : -1;
}

static bool keep_referenced_clip(const char* name, void*) {
  const int id = clip_id_from_name(name);
  if (id < 0) return false;
  const gs::Favourite* f = gs_store().find(uint8_t(id));
  return f && (f->flags & gs::kFavHasClip);
}

struct WipeReport {
  int removed = 0;
  int failed = 0;
  bool card = true;  // false: no card mounted, nothing could be checked
};

class StoryPlayerApp : public GsApp {
 public:
  const char* template_name() const override { return "story_player"; }

  void begin(uint32_t now) override {
    rng_.seed(esp_random());
    gs::StoryCtlConfig cfg;
    cfg.mode = static_cast<gs::CaptureMode>(GS_CFG_CAPTURE_MODE);
    cfg.clip_ms = GS_CFG_CLIP_MS;
    cfg.keep_window_ms = GS_CFG_KEEP_WINDOW_MS;
    cfg.min_clip_ms = 400;
    ctl_.set_config(cfg);
    gs::parse_hex_color(GS_CFG_NIGHT_COLOR, &palette_.night);
    if (cfg.mode == gs::CaptureMode::ChildVoiceClip) mic_ok_ = gsaudio::mic_begin();
    load_pack();
    prepare_card();
    import_card_names();
    apply_volume();
    face_.tick(now);
    if (!play_prompt(kPromptHello)) gsaudio::play_chime(gs::Chime::Ready);
    last_retention_check_ = now;
  }

  void loop(uint32_t now) override {
    ctl_.set_can_save(can_save());
    ctl_.tick(now);
    if (gsaudio::take_finished()) {
      if (pending_record_) {
        begin_recording(now);
      } else {
        ctl_.on_playback_done(now);
      }
    }
    bool ok = false;
    uint32_t ms = 0;
    if (gsaudio::take_record_result(&ok, &ms)) ctl_.on_record_done(ok, ms, now);
    run_commands(now);
    if (now - last_retention_check_ > 3600000u) {
      last_retention_check_ = now;
      expire_clips();
    }
    update_light(now);
    update_face(now);
  }

  void on_button(uint8_t index, gs::ButtonEvent ev, uint32_t now) override {
    if (index != 0) return;  // the first button is the story button
    face_.poke(now);
    ctl_.on_button(ev, now);
    run_commands(now);
  }

  void state(JsonObject out, uint32_t now) override {
    const gs::Memory& m = gs_store().mem();
    out["capture_mode"] = gs::capture_mode_name(ctl_.config().mode);
    out["network"] = GS_CFG_NETWORK ? "parent_page" : "off";
    out["clip_seconds"] = GS_CFG_CLIP_MS / 1000.0;
    out["keep_window_seconds"] = GS_CFG_KEEP_WINDOW_MS / 1000;
    JsonObject player = out["player"].to<JsonObject>();
    player["state"] = gs::story_state_name(ctl_.state());
    player["light"] = gs::indicator_name(ctl_.indicator(now));
    player["story_id"] = current_ >= 0 ? catalog_.get(size_t(current_))->id : "";
    player["story_title"] = current_ >= 0 ? catalog_.get(size_t(current_))->title : "";
    player["missing_audio_files"] = missing_;
    player["listening"] = gslisten::active();
    out["volume"] = m.volume;
    out["volume_max"] = GS_CFG_VOLUME_MAX;
    out["volume_note"] = "software setting, not a measured sound level";
    JsonObject night = out["night_light"].to<JsonObject>();
    night["enabled"] = m.night_enabled ? true : false;
    night["minutes"] = m.night_minutes;
    night["level"] = m.night_level;
    night["minutes_max"] = GS_CFG_NIGHT_MINUTES_MAX;
    night["level_max"] = GS_CFG_NIGHT_LEVEL_MAX;
    night["on"] = night_.active(now);
    night["remaining_s"] = night_.remaining_ms(now) / 1000;
    out["max_favourites"] = GS_CFG_MAX_FAVOURITES;
    out["pending_wipe"] = m.app[gs::kAppPendingWipe] ? true : false;
    JsonArray favs = out["favourites"].to<JsonArray>();
    for (uint8_t i = 0; i < m.count; ++i) {
      const gs::Favourite& f = m.fav[i];
      JsonObject o = favs.add<JsonObject>();
      o["id"] = f.id;
      o["name"] = (f.flags & gs::kFavHasName) ? f.name : "";
      o["has_clip"] = (f.flags & gs::kFavHasClip) != 0;
      char path[gs::kPathMax];
      if (f.flags & gs::kFavHasClip) {
        gs::fav_clip_path(f.id, path, sizeof(path));
        o["clip_on_card"] = gsboard::sd_exists(path);
      }
      if (f.flags & gs::kFavHasName) {
        gs::pack_name_path(f.name, path, sizeof(path));
        o["name_audio"] = path[0] && gsboard::sd_exists(path);  // false: "not spoken yet"
      }
    }
    JsonObject pack = out["story_pack"].to<JsonObject>();
    pack["loaded"] = pack_loaded_;
    pack["message"] = pack_message_;
    pack["language"] = pack_language_;
    pack["count"] = catalog_.count();
    JsonArray stories = out["stories"].to<JsonArray>();
    for (size_t i = 0; i < catalog_.count(); ++i) {
      const gs::StoryInfo* s = catalog_.get(i);
      JsonObject o = stories.add<JsonObject>();
      o["id"] = s->id;
      o["title"] = s->title;
      o["audio"] = story_has_audio(*s);
    }
    out["privacy"] = ctl_.config().mode == gs::CaptureMode::ChildVoiceClip
                         ? "Voice clips are encrypted on this device's microSD card with a key kept only on this device, "
                           "deleted (zeroed) on request or after the retention time, and never sent to the internet."
                         : "This gadget records no voice. Names are typed by a parent; nothing is sent to the internet.";
  }

  int action(const char* name, JsonObjectConst a, JsonObject out, uint32_t now) override {
    if (!strcmp(name, "play_story")) {
      const char* id = a["id"] | "";
      requested_ = id[0] ? catalog_.index_of(id) : -1;
      if (id[0] && requested_ < 0) return gs_reply(out, 404, "No story with that id on the card.");
      ctl_.request_play(now);
      run_commands(now);
      return gs_reply(out, 200, "Playing a story.");
    }
    if (!strcmp(name, "stop")) {
      ctl_.request_stop(now);
      run_commands(now);
      return gs_reply(out, 200, "Stopped.");
    }
    if (!strcmp(name, "set_volume")) {
      if (!a["volume"].is<int>()) return gs_reply(out, 400, "Send a whole-number volume.");
      const int v = a["volume"];
      if (v < 0 || v > GS_CFG_VOLUME_MAX) {
        out["volume_max"] = GS_CFG_VOLUME_MAX;
        return gs_reply(out, 400, "That is above this device's volume limit.");
      }
      const gs::StoreResult r = gs_store().set_volume(uint8_t(v));
      apply_volume();
      return store_reply(out, r, "Volume saved.");
    }
    if (!strcmp(name, "night_light")) {
      const gs::Memory& m = gs_store().mem();
      const bool enabled = a["enabled"] | (m.night_enabled != 0);
      const int minutes = a["minutes"] | int(m.night_minutes);
      const int level = a["level"] | int(m.night_level);
      if (minutes < 0 || minutes > GS_CFG_NIGHT_MINUTES_MAX || level < 0 || level > GS_CFG_NIGHT_LEVEL_MAX)
        return gs_reply(out, 400, "Night-light minutes or brightness is outside this device's limits.");
      const gs::StoreResult r = gs_store().set_night(enabled, uint8_t(minutes), uint8_t(level));
      if (a["on"].is<bool>()) {
        if (a["on"].as<bool>()) {
          night_.start(now, uint32_t(minutes) * 60000u);
        } else {
          night_.stop(now);
        }
      }
      return store_reply(out, r, "Night light saved.");
    }
    if (!strcmp(name, "fav_add")) {
      if (!(a["confirm"] | false)) return gs_reply(out, 400, "Confirm the name before saving it.");
      uint8_t id = 0;
      const gs::StoreResult r = gs_store().add_name(a["name"] | "", &id);
      out["id"] = id;
      out["name_audio"] = name_audio(id);
      if (r == gs::StoreResult::NoChange) return gs_reply(out, 200, "That name is already saved.");
      return store_reply(out, r, "Saved.");
    }
    if (!strcmp(name, "fav_rename")) {
      if (!(a["confirm"] | false)) return gs_reply(out, 400, "Confirm the change before saving it.");
      const int id = a["id"] | 0;
      const gs::StoreResult r = gs_store().set_name(uint8_t(id), a["name"] | "");
      out["name_audio"] = name_audio(uint8_t(id));
      if (r == gs::StoreResult::NoChange) return gs_reply(out, 409, "Another favourite already has that name.");
      return store_reply(out, r, "Saved.");
    }
    if (!strcmp(name, "fav_remove")) {
      if (!(a["confirm"] | false)) return gs_reply(out, 400, "Confirm before removing a favourite.");
      const int id = a["id"] | 0;
      const gs::Favourite* f = gs_store().find(uint8_t(id));
      const bool had_clip = f && (f->flags & gs::kFavHasClip);
      const gs::StoreResult r = gs_store().remove(uint8_t(id));
      if (r != gs::StoreResult::Ok) return store_reply(out, r, "Removed.");
      if (!had_clip) return gs_reply(out, 200, "Removed.");
      if (!wipe_clip(uint8_t(id))) {
        set_pending_wipe(true);
        out["pending_wipe"] = true;
        return gs_reply(out, 500,
                        "Favourite removed, but its voice clip could not be deleted from the card. Insert the card and "
                        "restart the gadget; it finishes deleting the clip then.");
      }
      return gs_reply(out, 200, "Removed, including its voice clip.");
    }
    if (!strcmp(name, "fav_erase_all")) {
      if (strcmp(a["confirm"] | "", "ERASE") != 0) return gs_reply(out, 400, "Type ERASE to delete every favourite.");
      const gs::StoreResult r = gs_store().erase_all();
      if (r != gs::StoreResult::Ok) return store_reply(out, r, "All favourites deleted.");
      const WipeReport w = wipe_all_clips();
      out["clips_deleted"] = w.removed;
      out["clips_left"] = w.failed;
      if (!w.card || w.failed) {
        out["pending_wipe"] = true;
        char msg[160];
        if (!w.card)
          snprintf(msg, sizeof(msg), "Favourites cleared but the voice clips could not be deleted (no card found); insert the card and retry.");
        else
          snprintf(msg, sizeof(msg), "Favourites cleared but %d voice clips could not be deleted; insert the card and retry.", w.failed);
        return gs_reply(out, 500, msg);
      }
      return gs_reply(out, 200, "All favourites and voice clips deleted.");
    }
    return gs_reply(out, 404, "Unknown action for story_player.");
  }

  bool healthy() override { return gsaudio::ready(); }

  // Parent "erase everything" (and reset memory): every clip goes, and so does the clip key.
  bool erase_user_data() override {
    const WipeReport w = wipe_all_clips();
    return w.card && w.failed == 0;
  }

  String clip_path(uint8_t id) const {
    const gs::Favourite* f = gs_store().find(id);
    if (!f || !(f->flags & gs::kFavHasClip)) return String("");
    char path[gs::kPathMax];
    gs::fav_clip_path(id, path, sizeof(path));
    return String(path);
  }

  String name_clip_path(uint8_t id) override {
    const gs::Favourite* f = gs_store().find(id);
    if (!f || !(f->flags & gs::kFavHasName)) return String("");
    char path[gs::kPathMax];
    gs::pack_name_path(f->name, path, sizeof(path));
    return String(path);
  }

 private:
  int store_reply(JsonObject out, gs::StoreResult r, const char* ok_message) {
    switch (r) {
      case gs::StoreResult::Ok:
      case gs::StoreResult::NoChange: return gs_reply(out, 200, ok_message);
      case gs::StoreResult::InvalidName:
        return gs_reply(out, 400, "Use 1 to 32 letters, spaces, apostrophes, hyphens or full stops (English letters for now).");
      case gs::StoreResult::Full: return gs_reply(out, 409, "Memory is full. Remove a favourite first.");
      case gs::StoreResult::NotFound: return gs_reply(out, 404, "That favourite is not saved.");
      case gs::StoreResult::OutOfRange: return gs_reply(out, 400, "That value is outside this device's limits.");
      case gs::StoreResult::Refused:
        return gs_reply(out, 409, "The device memory is damaged and was not changed. Use Reset memory to start again.");
      case gs::StoreResult::StorageError: return gs_reply(out, 500, "Saving failed; nothing was changed. Try again.");
    }
    return gs_reply(out, 500, "Unknown result.");
  }

  bool name_audio(uint8_t id) {
    const String p = name_clip_path(id);
    return p.length() && gsboard::sd_exists(p.c_str());
  }

  bool can_save() const {
    return mic_ok_ && gsboard::sd_mounted() && gs_store().writable() &&
           gs_store().mem().count < GS_CFG_MAX_FAVOURITES;
  }

  void apply_volume() { gsaudio::set_volume(gs_store().mem().volume, GS_CFG_VOLUME_MAX); }

  bool play_prompt(const char* path) { return gsboard::sd_exists(path) && gsaudio::play_file(path); }

  void play_cue(gs::Chime ch) {
    const char* p = nullptr;
    if (ch == gs::Chime::Saved) p = kPromptSaved;
    else if (ch == gs::Chime::Discarded || ch == gs::Chime::TooShort) p = kPromptTryAgain;
    if (!p || !play_prompt(p)) gsaudio::play_chime(ch);
  }

  bool story_has_audio(const gs::StoryInfo& s) const {
    char path[gs::kPathMax];
    gs::story_segment_path(s.id, 0, path, sizeof(path));
    return gsboard::sd_exists(path);
  }

  void set_pending_wipe(bool on) {
    if ((gs_store().mem().app[gs::kAppPendingWipe] != 0) != on) gs_store().set_app_byte(gs::kAppPendingWipe, on ? 1 : 0);
  }

  // ---------------------------------------------------------------- card: clips
  // A card pulled out after mounting still looks mounted; a failed lookup is checked by remounting.
  bool card_ok(const char* path) {
    if (gsboard::sd_mounted() && gsboard::sd_exists(path)) return true;
    gsaudio::stop();  // never remount under a file the player has open
    return gsboard::sd_remount();
  }

  bool wipe_clip(uint8_t id) {
    char path[gs::kPathMax];
    gs::fav_clip_path(id, path, sizeof(path));
    return card_ok(path) && gsboard::sd_wipe(path);
  }

  // Deletes the files in /fav that keep() does not protect, in passes until none is left.
  WipeReport wipe_fav_files(bool (*keep)(const char*, void*)) {
    WipeReport w;
    if (!card_ok("/fav")) {
      w.card = false;
      return w;
    }
    for (int pass = 0; pass < 64; ++pass) {
      PathBatch b;
      b.keep = keep;
      if (!gsboard::sd_list("/fav", collect_fav, &b)) break;  // no /fav on a mounted card: nothing to wipe
      if (b.n == 0) break;
      int ok = 0;
      w.failed = 0;
      for (int i = 0; i < b.n; ++i) {
        if (gsboard::sd_wipe(b.path[i])) ++ok;
        else ++w.failed;
      }
      w.removed += ok;
      if (ok == 0) break;  // nothing more can be removed now; the rest is reported
      if (!b.more && w.failed == 0) break;
    }
    return w;
  }

  WipeReport wipe_all_clips() {
    gsclips::destroy_key();  // first: whatever stays on the card can no longer be decrypted
    const WipeReport w = wipe_fav_files(nullptr);
    set_pending_wipe(!w.card || w.failed > 0);
    return w;
  }

  // MIN-17: a power cut after the record was saved but before the clip was renamed leaves a
  // favourite with no clip file. Drop those clips from memory in one commit.
  void repair_records() {
    if (!gs_store().writable()) return;
    const gs::Memory& m = gs_store().mem();
    uint8_t ids[gs::kMaxFavourites];
    uint8_t n = 0;
    for (uint8_t i = 0; i < m.count; ++i) {
      if (!(m.fav[i].flags & gs::kFavHasClip)) continue;
      char path[gs::kPathMax];
      gs::fav_clip_path(m.fav[i].id, path, sizeof(path));
      if (!gsboard::sd_exists(path)) ids[n++] = m.fav[i].id;
    }
    if (!n) return;
    uint8_t changed = 0;
    gs_store().drop_clips(ids, n, &changed);
    Serial.printf("GS-MEMORY {\"repaired\":%u}\n", unsigned(changed));
  }

  // Clips older than the retention time (in powered-on hours) are wiped and dropped from memory.
  void expire_clips() {
#if GS_HAS_SD
    if (!gsboard::sd_mounted() || !gs_store().writable()) return;
    const uint32_t now_h = gsclips::hours();
    const gs::Memory& m = gs_store().mem();
    uint8_t ids[gs::kMaxFavourites];
    uint8_t n = 0;
    for (uint8_t i = 0; i < m.count; ++i) {
      if (!(m.fav[i].flags & gs::kFavHasClip)) continue;
      char path[gs::kPathMax];
      gs::fav_clip_path(m.fav[i].id, path, sizeof(path));
      File f = SD.open(path, FILE_READ);
      if (!f) continue;
      uint8_t head[gs::kClipHeaderBytes];
      gs::ClipHeader h;
      const bool ok = f.read(head, sizeof(head)) == sizeof(head) && gs::clip_header_decode(head, sizeof(head), &h);
      f.close();
      if (ok && now_h >= h.saved_hour && now_h - h.saved_hour >= uint32_t(GS_CFG_CLIP_RETENTION_HOURS)) {
        if (gsboard::sd_wipe(path)) ids[n++] = m.fav[i].id;
      }
    }
    if (n) {
      uint8_t changed = 0;
      gs_store().drop_clips(ids, n, &changed);
      Serial.printf("GS-MEMORY {\"expired_clips\":%u}\n", unsigned(changed));
    }
#endif
  }

  void prepare_card() {
#if GS_HAS_SD
    if (!gsboard::sd_mounted()) return;
    gsboard::sd_mkdirs("/fav");
    gsboard::sd_wipe(kTmpClip);  // an unconfirmed take never survives a restart
    repair_records();
    // Files no favourite refers to (an interrupted save, or a wipe that could not finish before).
    if (gs_store().writable()) {
      const WipeReport w = wipe_fav_files(keep_referenced_clip);
      if (w.card && w.failed == 0) set_pending_wipe(false);
      if (w.removed) Serial.printf("GS-MEMORY {\"wiped_orphan_clips\":%d}\n", w.removed);
    }
    expire_clips();
#endif
  }

  // ---------------------------------------------------------------- card: story pack
  void load_pack() {
    catalog_.clear();
    pack_loaded_ = false;
    card_names_ = 0;
#if GS_HAS_SD
    if (!gsboard::sd_mounted()) {
      pack_message_ = "No microSD card found. Insert the story card and restart.";
      return;
    }
    File f = SD.open("/stories.json", FILE_READ);
    if (!f) {
      pack_message_ = "The card has no stories.json. Copy the story pack from the app onto the card.";
      return;
    }
    JsonDocument filter;
    filter["language"] = true;
    filter["stories"][0]["id"] = true;
    filter["stories"][0]["title"] = true;
    filter["stories"][0]["segments"] = true;
    filter["stories"][0]["slots"] = true;
    filter["names"][0]["name"] = true;
    JsonDocument doc;
    const DeserializationError err = deserializeJson(doc, f, DeserializationOption::Filter(filter));
    f.close();
    if (err) {
      pack_message_ = String("stories.json could not be read: ") + err.c_str();
      return;
    }
    pack_language_ = doc["language"] | "";
    for (JsonObjectConst s : doc["stories"].as<JsonArrayConst>()) {
      uint16_t mask = 0;
      uint8_t k = 0;
      for (JsonVariantConst slot : s["slots"].as<JsonArrayConst>()) {
        if (k < 15 && strcmp(slot | "", "FRIEND") == 0) mask = uint16_t(mask | (1u << k));
        ++k;
      }
      catalog_.add(s["id"] | "", s["title"] | "", uint8_t(s["segments"] | 0), mask);
    }
    for (JsonObjectConst nm : doc["names"].as<JsonArrayConst>()) {
      if (card_names_ < gs::kMaxFavourites && gs::copy_str(card_name_[card_names_], sizeof(card_name_[0]), nm["name"] | ""))
        ++card_names_;
    }
    pack_loaded_ = catalog_.count() > 0;
    size_t with_audio = 0;
    for (size_t i = 0; i < catalog_.count(); ++i) with_audio += story_has_audio(*catalog_.get(i)) ? 1 : 0;
    if (!pack_loaded_) {
      pack_message_ = "stories.json lists no valid stories.";
    } else if (with_audio == 0) {
      pack_message_ = "Story text is on the card but no audio yet. Generate the story audio in the app.";
    } else {
      pack_message_ = String(with_audio) + " of " + String(catalog_.count()) + " stories have audio.";
    }
#else
    pack_message_ = "This design has no microSD card.";
#endif
  }

  // With the network off there is no parent page: the favourites are the names the parent typed in
  // the app, which the story card carries with their spoken clips.
  void import_card_names() {
    if (GS_CFG_NETWORK || !gs_store().writable()) return;
    for (uint8_t i = 0; i < card_names_; ++i) {
      char path[gs::kPathMax];
      gs::pack_name_path(card_name_[i], path, sizeof(path));
      if (!path[0] || !gsboard::sd_exists(path) || gs_store().find_name(card_name_[i])) continue;
      if (gs_store().add_name(card_name_[i]) == gs::StoreResult::Full) break;
    }
  }

  // ---------------------------------------------------------------- playing
  void start_story(uint32_t now) {
    if (catalog_.count() == 0) {
      missing_ = 1;
      gsaudio::play_chime(gs::Chime::Error);
      ctl_.on_playback_done(now);
      return;
    }
    const gs::Memory& m = gs_store().mem();
    int idx = requested_;
    requested_ = -1;
    if (idx < 0) idx = gs::pick_story(catalog_.count(), m.last_story, rng_, GS_CFG_STORY_ORDER_SEQUENTIAL != 0);
    const gs::StoryInfo& s = *catalog_.get(size_t(idx));
    if (!story_has_audio(s)) {
      missing_ = s.segments;
      gsaudio::play_chime(gs::Chime::Error);
      ctl_.on_playback_done(now);
      return;
    }
    const gs::CaptureMode mode = ctl_.config().mode;
    const uint8_t hero = gs::pick_hero(m, mode, card_exists, nullptr, rng_);
    const uint8_t pal = gs::pick_friend(m, mode, card_exists, nullptr, rng_, hero);
    gs::PlayItem items[gs::kMaxPlayItems];
    const size_t n = gs::build_playlist(s, m, hero, pal, mode, card_exists, nullptr, items, gs::kMaxPlayItems);
    current_ = idx;
    missing_ = 0;
    apply_volume();
    gsaudio::play_list(items, n);
    if (m.night_enabled) night_.start(now, uint32_t(m.night_minutes) * 60000u);
    gs_store().note_played(int8_t(idx), hero);
  }

  // The spoken cue ("tell me a name") plays first; the mic opens only after it, so the clip never
  // holds the cue, and the listening light is already on from the press.
  void cue_then_record(uint32_t now) {
    gsaudio::stop();
    gsboard::sd_wipe(kTmpClip);
    pending_record_ = true;
    if (play_prompt(kPromptHold) || gsaudio::play_tone(1047, 120, 50)) return;
    begin_recording(now);
  }

  void begin_recording(uint32_t now) {
    pending_record_ = false;
    if (!gsaudio::record_start(kTmpClip, GS_CFG_CLIP_MS)) {
      ctl_.on_record_done(false, 0, now);
      return;
    }
    ctl_.on_record_started(now);
  }

  void play_clip_and_prompt() {
    gs::PlayItem items[2];
    items[0].kind = gs::PlayKind::Segment;
    gs::copy_str(items[0].path, sizeof(items[0].path), kTmpClip);
    if (gsboard::sd_exists(kPromptKeep)) {
      items[1].kind = gs::PlayKind::Segment;
      gs::copy_str(items[1].path, sizeof(items[1].path), kPromptKeep);
    } else {
      items[1].kind = gs::PlayKind::Chime;
    }
    gsaudio::play_list(items, 2);
  }

  void run_commands(uint32_t now) {
    gs::Command c;
    while (ctl_.pop(&c)) {
      switch (c.cmd) {
        case gs::Cmd::StartStory: start_story(now); break;
        case gs::Cmd::Stop: gsaudio::stop(); break;
        case gs::Cmd::StartRecord: cue_then_record(now); break;
        case gs::Cmd::StopRecord:
          if (pending_record_) {  // released during the cue: nothing was recorded
            pending_record_ = false;
            gsaudio::stop();
            gsaudio::take_finished();
            ctl_.on_record_done(true, 0, now);
          } else {
            gsaudio::record_stop();
          }
          break;
        case gs::Cmd::PlayClip: play_clip_and_prompt(); break;
        case gs::Cmd::KeepClip: keep_clip(now); break;
        case gs::Cmd::DiscardClip:
          pending_record_ = false;
          if (gsaudio::recording()) {
            gsaudio::record_stop();
            const uint32_t t0 = millis();
            while (gsaudio::recording() && millis() - t0 < 500) delay(5);
            gsaudio::take_record_result(nullptr, nullptr);
          }
          gsboard::sd_wipe(kTmpClip);
          break;
        case gs::Cmd::Chime: play_cue(c.chime); break;
        default: break;
      }
    }
  }

  void keep_clip(uint32_t now) {
    uint8_t id = 0;
    if (gs_store().add_clip(&id) != gs::StoreResult::Ok) {
      ctl_.on_keep_result(false, now);
      return;
    }
    char path[gs::kPathMax];
    gs::fav_clip_path(id, path, sizeof(path));
    if (!gsboard::sd_rename(kTmpClip, path)) {
      gs_store().remove(id);  // keep memory and card consistent
      ctl_.on_keep_result(false, now);
      return;
    }
    face_.poke(now);
    ctl_.on_keep_result(true, now);
  }

  void update_light(uint32_t now) {
    if (now - last_led_ < 20) return;
    last_led_ = now;
    const gs::Memory& m = gs_store().mem();
    // MAJ-29: the night glow never exceeds GS_CFG_NIGHT_LEVEL_MAX % (then the global LED cap applies).
    const uint8_t level = m.night_level > GS_CFG_NIGHT_LEVEL_MAX ? uint8_t(GS_CFG_NIGHT_LEVEL_MAX) : m.night_level;
    palette_.night_level = uint8_t(uint16_t(level) * 255u / 100u);
    gs::Indicator ind = ctl_.indicator(now);
    if (gslisten::active()) ind = gs::Indicator::Listening;
    const gs::Rgb c = gs::indicator_color(ind, now, palette_, night_.level(now));
    gsled::set_all(c);
    gsled::show();
  }

  void update_face(uint32_t now) {
    if (!gsdisp::present()) return;
    face_.tick(now);
    gs::Expression e = face_.expression(now);
    const gs::StoryState st = ctl_.state();
    if (ctl_.indicator(now) == gs::Indicator::Listening || gslisten::active()) e = gs::Expression::Listening;
    else if (ctl_.indicator(now) == gs::Indicator::Saved) e = gs::Expression::Happy;
    else if (st == gs::StoryState::Playing && e != gs::Expression::Blink) e = gs::Expression::Sleepy;
    const uint32_t frame = e == gs::Expression::Listening ? now / 250 : 0;
    if (e == last_face_ && frame == last_frame_) return;
    last_face_ = e;
    last_frame_ = frame;
    gsdisp::draw_face(e, now, gsdisp::kWhite);
  }

  gs::StoryController ctl_;
  gs::StoryCatalog catalog_;
  gs::Rng rng_;
  gs::NightLight night_;
  gs::LightPalette palette_;
  gs::FaceFsm face_{0x5EED, 10u * 60u * 1000u};
  bool mic_ok_ = false;
  bool pack_loaded_ = false;
  bool pending_record_ = false;
  String pack_message_ = "not loaded";
  String pack_language_;
  char card_name_[gs::kMaxFavourites][gs::kNameMax + 1];
  uint8_t card_names_ = 0;
  int current_ = -1;
  int requested_ = -1;
  uint8_t missing_ = 0;
  uint32_t last_led_ = 0;
  uint32_t last_retention_check_ = 0;
  gs::Expression last_face_ = gs::Expression::Neutral;
  uint32_t last_frame_ = 1;
};

static StoryPlayerApp s_app;

GsApp* gs_make_app() { return &s_app; }

String gs_app_clip_path(uint8_t fav_id) { return s_app.clip_path(fav_id); }
