// story_player interaction model (one button, one light). Pure state machine: it consumes button
// events and audio callbacks and emits commands; the device glue executes them.
//
//   Idle      tap -> StartStory (Playing)
//             press (child_voice_clip mode) -> light and face show listening at once (armed)
//             hold (child_voice_clip mode) -> StartRecord (Recording): the glue plays the spoken
//             cue, then opens the mic and reports on_record_started()
//   Playing   press -> Stop (the release that follows is ignored); hold keeps going into Recording
//   Recording release or clip time -> StopRecord; recorder reports -> PlayClip (Reviewing)
//   Reviewing press -> stop playback and open the keep window (that tap never keeps)
//             playback ends -> open the keep window (AwaitKeep)
//   AwaitKeep tap within the window -> KeepClip (saved chime)
//             hold -> DiscardClip and record again
//             window expires -> DiscardClip (discard chime)
//
// The microphone is only opened by a hold in child_voice_clip mode, and the light is
// listening-blue for at least as long as the recorder runs (from the press, before the mic opens).
#pragma once
#include <stdint.h>

#include "gs_button.h"
#include "gs_story.h"

namespace gs {

enum class StoryState : uint8_t { Idle, Playing, Recording, Reviewing, AwaitKeep };
const char* story_state_name(StoryState s);

enum class Indicator : uint8_t { Off, Story, Listening, KeepPrompt, Saved, Discarded, Error };
const char* indicator_name(Indicator i);

enum class Cmd : uint8_t { None, StartStory, Stop, StartRecord, StopRecord, PlayClip, KeepClip, DiscardClip, Chime };
enum class Chime : uint8_t { None, Ready, Saved, Discarded, Error, Full, TooShort };
const char* cmd_name(Cmd c);
const char* chime_name(Chime c);

struct Command {
  Cmd cmd = Cmd::None;
  Chime chime = Chime::None;
};

struct StoryCtlConfig {
  CaptureMode mode = CaptureMode::ChildVoiceClip;
  uint32_t clip_ms = 3000;          // recording length
  uint32_t min_clip_ms = 400;       // shorter recordings are discarded
  uint32_t keep_window_ms = 10000;  // tap within this window to keep the clip
  uint32_t flash_ms = 1500;         // how long saved / discarded / error colours show
};

class StoryController {
 public:
  explicit StoryController(StoryCtlConfig cfg = StoryCtlConfig()) : cfg_(cfg) {}

  void on_button(ButtonEvent ev, uint32_t now);
  void on_playback_done(uint32_t now);
  void on_record_done(bool ok, uint32_t recorded_ms, uint32_t now);
  void on_keep_result(bool ok, uint32_t now);
  // The glue opened the mic (after the spoken cue): the recording watchdog counts from here.
  void on_record_started(uint32_t now);
  void tick(uint32_t now);

  // Parent page / app requests.
  void request_play(uint32_t now);
  void request_stop(uint32_t now);

  // Facts the glue keeps current.
  void set_can_save(bool can_save) { can_save_ = can_save; }
  void set_config(const StoryCtlConfig& cfg) { cfg_ = cfg; }

  bool pop(Command* out);
  StoryState state() const { return state_; }
  Indicator indicator(uint32_t now) const;
  uint32_t keep_deadline() const { return keep_deadline_; }
  uint32_t record_started() const { return record_started_; }
  const StoryCtlConfig& config() const { return cfg_; }

 private:
  void emit(Cmd c, Chime ch = Chime::None);
  void flash(Indicator ind, uint32_t now);
  void start_recording(uint32_t now);
  void open_keep_window(uint32_t now);

  StoryCtlConfig cfg_;
  StoryState state_ = StoryState::Idle;
  bool consumed_ = false;  // the current press already did something; ignore its release
  bool armed_ = false;     // child_voice_clip: pressed in Idle, the hold may start a recording
  bool can_save_ = true;
  uint32_t record_started_ = 0;
  uint32_t keep_deadline_ = 0;
  Indicator flash_ind_ = Indicator::Off;
  uint32_t flash_until_ = 0;
  bool flash_on_ = false;

  static constexpr int kQueue = 8;
  Command q_[kQueue];
  int head_ = 0;
  int len_ = 0;
};

}  // namespace gs
