// story_player: bedtime stories from the microSD story pack, starring a remembered favourite.
//
// Tap: a story (tap again to stop), with the night-light glow.
// child_voice_clip mode: hold until the light turns listening-blue and say the name (about
// GS_CFG_CLIP_MS); the clip plays back; tap within GS_CFG_KEEP_WINDOW_MS to keep it, otherwise it
// is deleted. Clips stay on this device's microSD card. Nothing is sent to the internet.
// parent_web mode: names come from the parent page; each uses the story pack's name clip, or a
// soft chime when the pack has no clip for that name.
#include <ArduinoJson.h>
#include <FS.h>
#include <esp_random.h>

#include "config.h"
#include "gs_app.h"
#include "gs_audio.h"
#include "gs_board.h"
#include "gs_device.h"
#include "gs_display.h"
#include "gs_led.h"
#include "gs_light.h"
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

static bool card_exists(const char* path, void*) { return gsboard::sd_exists(path); }

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
    apply_volume();
    face_.tick(now);
    gsaudio::play_chime(gs::Chime::Ready);
  }

  void loop(uint32_t now) override {
    ctl_.set_can_save(can_save());
    ctl_.tick(now);
    if (gsaudio::take_finished()) ctl_.on_playback_done(now);
    bool ok = false;
    uint32_t ms = 0;
    if (gsaudio::take_record_result(&ok, &ms)) ctl_.on_record_done(ok, ms, now);
    run_commands(now);
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
    out["clip_seconds"] = GS_CFG_CLIP_MS / 1000.0;
    out["keep_window_seconds"] = GS_CFG_KEEP_WINDOW_MS / 1000;
    JsonObject player = out["player"].to<JsonObject>();
    player["state"] = gs::story_state_name(ctl_.state());
    player["light"] = gs::indicator_name(ctl_.indicator(now));
    player["story_id"] = current_ >= 0 ? catalog_.get(size_t(current_))->id : "";
    player["story_title"] = current_ >= 0 ? catalog_.get(size_t(current_))->title : "";
    player["missing_audio_files"] = missing_;
    player["listening"] = ctl_.state() == gs::StoryState::Recording;
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
        o["name_audio"] = path[0] && gsboard::sd_exists(path);
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
    out["privacy"] = "Voice clips are stored only on this device's microSD card and are never sent to the internet.";
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
      if (r == gs::StoreResult::NoChange) return gs_reply(out, 200, "That name is already saved.");
      return store_reply(out, r, "Saved.");
    }
    if (!strcmp(name, "fav_rename")) {
      if (!(a["confirm"] | false)) return gs_reply(out, 400, "Confirm the change before saving it.");
      const int id = a["id"] | 0;
      const gs::StoreResult r = gs_store().set_name(uint8_t(id), a["name"] | "");
      if (r == gs::StoreResult::NoChange) return gs_reply(out, 409, "Another favourite already has that name.");
      return store_reply(out, r, "Saved.");
    }
    if (!strcmp(name, "fav_remove")) {
      if (!(a["confirm"] | false)) return gs_reply(out, 400, "Confirm before removing a favourite.");
      const int id = a["id"] | 0;
      const gs::StoreResult r = gs_store().remove(uint8_t(id));
      if (r == gs::StoreResult::Ok) delete_clip(uint8_t(id));
      return store_reply(out, r, "Removed, including its voice clip.");
    }
    if (!strcmp(name, "fav_erase_all")) {
      if (strcmp(a["confirm"] | "", "ERASE") != 0) return gs_reply(out, 400, "Type ERASE to delete every favourite.");
      const gs::StoreResult r = gs_store().erase_all();
      if (r == gs::StoreResult::Ok) erase_user_data();
      return store_reply(out, r, "All favourites and voice clips deleted.");
    }
    return gs_reply(out, 404, "Unknown action for story_player.");
  }

  bool healthy() override { return gsaudio::ready(); }

  bool erase_user_data() override {
#if GS_HAS_SD
    if (!gsboard::sd_mounted()) return true;
    File dir = SD.open("/fav");
    if (!dir) return true;
    String names[64];
    int n = 0;
    for (File f = dir.openNextFile(); f && n < 64; f = dir.openNextFile()) {
      if (!f.isDirectory()) names[n++] = String("/fav/") + f.name();
      f.close();
    }
    dir.close();
    bool ok = true;
    for (int i = 0; i < n; ++i) ok = gsboard::sd_remove(names[i].c_str()) && ok;
    return ok;
#else
    return true;
#endif
  }

  String clip_path(uint8_t id) const {
    const gs::Favourite* f = gs_store().find(id);
    if (!f || !(f->flags & gs::kFavHasClip)) return String("");
    char path[gs::kPathMax];
    gs::fav_clip_path(id, path, sizeof(path));
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

  bool can_save() const {
    return mic_ok_ && gsboard::sd_mounted() && gs_store().writable() &&
           gs_store().mem().count < GS_CFG_MAX_FAVOURITES;
  }

  void apply_volume() { gsaudio::set_volume(gs_store().mem().volume, GS_CFG_VOLUME_MAX); }

  bool story_has_audio(const gs::StoryInfo& s) const {
    char path[gs::kPathMax];
    gs::story_segment_path(s.id, 0, path, sizeof(path));
    return gsboard::sd_exists(path);
  }

  void load_pack() {
    catalog_.clear();
    pack_loaded_ = false;
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
    JsonDocument doc;
    const DeserializationError err = deserializeJson(doc, f, DeserializationOption::Filter(filter));
    f.close();
    if (err) {
      pack_message_ = String("stories.json could not be read: ") + err.c_str();
      return;
    }
    pack_language_ = doc["language"] | "";
    for (JsonObjectConst s : doc["stories"].as<JsonArrayConst>()) {
      catalog_.add(s["id"] | "", s["title"] | "", uint8_t(s["segments"] | 0));
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

  void prepare_card() {
#if GS_HAS_SD
    if (!gsboard::sd_mounted()) return;
    gsboard::sd_mkdirs("/fav");
    gsboard::sd_remove(kTmpClip);  // an unconfirmed take never survives a restart
    // Delete clip files that no favourite refers to (for example after a power cut mid-save).
    File dir = SD.open("/fav");
    if (!dir) return;
    String orphans[16];
    int n = 0;
    for (File f = dir.openNextFile(); f && n < 16; f = dir.openNextFile()) {
      const String nm = f.name();
      f.close();
      if (nm.length() == 8 && nm.startsWith("f") && nm.endsWith(".wav")) {
        const long id = nm.substring(1, 4).toInt();
        const gs::Favourite* fav = gs_store().find(uint8_t(id));
        if (gs_store().writable() && (!fav || !(fav->flags & gs::kFavHasClip))) orphans[n++] = String("/fav/") + nm;
      }
    }
    dir.close();
    for (int i = 0; i < n; ++i) gsboard::sd_remove(orphans[i].c_str());
#endif
  }

  void delete_clip(uint8_t id) {
    char path[gs::kPathMax];
    gs::fav_clip_path(id, path, sizeof(path));
    gsboard::sd_remove(path);
  }

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
    const uint8_t hero = gs::pick_hero(m, ctl_.config().mode, card_exists, nullptr, rng_);
    gs::PlayItem items[gs::kMaxPlayItems];
    const size_t n = gs::build_playlist(s, m, hero, ctl_.config().mode, card_exists, nullptr, items, gs::kMaxPlayItems);
    current_ = idx;
    missing_ = 0;
    apply_volume();
    gsaudio::play_list(items, n);
    if (m.night_enabled) night_.start(now, uint32_t(m.night_minutes) * 60000u);
    gs_store().note_played(int8_t(idx), hero);
  }

  void run_commands(uint32_t now) {
    gs::Command c;
    while (ctl_.pop(&c)) {
      switch (c.cmd) {
        case gs::Cmd::StartStory: start_story(now); break;
        case gs::Cmd::Stop: gsaudio::stop(); break;
        case gs::Cmd::StartRecord:
          gsaudio::stop();
          gsboard::sd_remove(kTmpClip);
          if (!gsaudio::record_start(kTmpClip, GS_CFG_CLIP_MS)) ctl_.on_record_done(false, 0, now);
          break;
        case gs::Cmd::StopRecord: gsaudio::record_stop(); break;
        case gs::Cmd::PlayClip: gsaudio::play_file(kTmpClip); break;
        case gs::Cmd::KeepClip: keep_clip(now); break;
        case gs::Cmd::DiscardClip:
          if (gsaudio::recording()) {
            gsaudio::record_stop();
            const uint32_t t0 = millis();
            while (gsaudio::recording() && millis() - t0 < 500) delay(5);
            gsaudio::take_record_result(nullptr, nullptr);
          }
          gsboard::sd_remove(kTmpClip);
          break;
        case gs::Cmd::Chime: gsaudio::play_chime(c.chime); break;
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
    palette_.night_level = uint8_t(uint16_t(m.night_level) * 255u / 100u);
    const gs::Rgb c = gs::indicator_color(ctl_.indicator(now), now, palette_, night_.level(now));
    gsled::set_all(c);
    gsled::show();
  }

  void update_face(uint32_t now) {
    if (!gsdisp::present()) return;
    face_.tick(now);
    gs::Expression e = face_.expression(now);
    const gs::StoryState st = ctl_.state();
    if (st == gs::StoryState::Recording) e = gs::Expression::Listening;
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
  String pack_message_ = "not loaded";
  String pack_language_;
  int current_ = -1;
  int requested_ = -1;
  uint8_t missing_ = 0;
  uint32_t last_led_ = 0;
  gs::Expression last_face_ = gs::Expression::Neutral;
  uint32_t last_frame_ = 1;
};

static StoryPlayerApp s_app;

GsApp* gs_make_app() { return &s_app; }

String gs_app_clip_path(uint8_t fav_id) { return s_app.clip_path(fav_id); }
