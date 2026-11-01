#include <unity.h>

#include <vector>

#include "gs_button.h"
#include "gs_light.h"
#include "gs_story_ctl.h"

using gs::ButtonEvent;
using gs::Chime;
using gs::Cmd;
using gs::Indicator;
using gs::StoryController;
using gs::StoryState;

static std::vector<gs::Command> drain(StoryController& c) {
  std::vector<gs::Command> out;
  gs::Command cmd;
  while (c.pop(&cmd)) out.push_back(cmd);
  return out;
}

static bool has(const std::vector<gs::Command>& v, Cmd c) {
  for (auto& x : v)
    if (x.cmd == c) return true;
  return false;
}

static bool has_chime(const std::vector<gs::Command>& v, Chime ch) {
  for (auto& x : v)
    if (x.cmd == Cmd::Chime && x.chime == ch) return true;
  return false;
}

static void tap(StoryController& c, uint32_t t) {
  c.on_button(ButtonEvent::Press, t);
  c.on_button(ButtonEvent::Tap, t + 120);
}

static void test_tap_plays_and_tap_during_story_stops() {
  StoryController c;
  tap(c, 1000);
  auto v = drain(c);
  TEST_ASSERT_EQUAL(1, v.size());
  TEST_ASSERT_EQUAL(Cmd::StartStory, v[0].cmd);
  TEST_ASSERT_EQUAL(StoryState::Playing, c.state());
  TEST_ASSERT_EQUAL(Indicator::Story, c.indicator(1200));
  tap(c, 5000);  // the tap that stops a story must not start another
  v = drain(c);
  TEST_ASSERT_EQUAL(1, v.size());
  TEST_ASSERT_EQUAL(Cmd::Stop, v[0].cmd);
  TEST_ASSERT_EQUAL(StoryState::Idle, c.state());
}

static void test_story_end_returns_to_idle() {
  StoryController c;
  tap(c, 0);
  drain(c);
  c.on_playback_done(60000);
  TEST_ASSERT_EQUAL(StoryState::Idle, c.state());
  tap(c, 61000);
  TEST_ASSERT_TRUE(has(drain(c), Cmd::StartStory));
}

static void test_hold_records_listens_blue_plays_back_and_tap_keeps() {
  StoryController c;  // child_voice_clip, 2 s clip, 10 s keep window
  c.on_button(ButtonEvent::Press, 0);
  TEST_ASSERT_EQUAL(0, drain(c).size());
  c.on_button(ButtonEvent::LongStart, 600);
  auto v = drain(c);
  TEST_ASSERT_TRUE(has(v, Cmd::StartRecord));
  TEST_ASSERT_EQUAL(StoryState::Recording, c.state());
  TEST_ASSERT_EQUAL(Indicator::Listening, c.indicator(700));
  gs::LightPalette pal;
  const gs::Rgb blue = gs::indicator_color(c.indicator(700), 700, pal, 0);
  TEST_ASSERT_TRUE(blue.b > 0 && blue.r == 0);  // visibly blue even with the night light off
  c.on_button(ButtonEvent::LongRelease, 2700);
  TEST_ASSERT_TRUE(has(drain(c), Cmd::StopRecord));
  c.on_record_done(true, 2000, 2710);
  v = drain(c);
  TEST_ASSERT_TRUE(has(v, Cmd::PlayClip));
  TEST_ASSERT_EQUAL(StoryState::Reviewing, c.state());
  TEST_ASSERT_NOT_EQUAL(Indicator::Listening, c.indicator(2720));  // mic closed: not blue
  c.on_playback_done(4800);
  TEST_ASSERT_EQUAL(StoryState::AwaitKeep, c.state());
  TEST_ASSERT_EQUAL_UINT32(14800, c.keep_deadline());
  tap(c, 9000);
  v = drain(c);
  TEST_ASSERT_EQUAL(1, v.size());
  TEST_ASSERT_EQUAL(Cmd::KeepClip, v[0].cmd);
  c.on_keep_result(true, 9200);
  v = drain(c);
  TEST_ASSERT_TRUE(has_chime(v, Chime::Saved));
  TEST_ASSERT_EQUAL(Indicator::Saved, c.indicator(9300));
  TEST_ASSERT_EQUAL(StoryState::Idle, c.state());
}

static void test_no_tap_within_window_discards() {
  StoryController c;
  c.on_button(ButtonEvent::Press, 0);
  c.on_button(ButtonEvent::LongStart, 600);
  c.on_button(ButtonEvent::LongRelease, 2600);
  c.on_record_done(true, 2000, 2600);
  c.on_playback_done(4600);
  drain(c);
  c.tick(14599);
  TEST_ASSERT_EQUAL(StoryState::AwaitKeep, c.state());
  c.tick(14600);
  auto v = drain(c);
  TEST_ASSERT_TRUE(has(v, Cmd::DiscardClip));
  TEST_ASSERT_TRUE(has_chime(v, Chime::Discarded));
  TEST_ASSERT_EQUAL(StoryState::Idle, c.state());
  TEST_ASSERT_EQUAL(Indicator::Discarded, c.indicator(14700));
  c.tick(20000);
  TEST_ASSERT_EQUAL(Indicator::Off, c.indicator(20000));
  tap(c, 21000);  // a late tap plays a story; it never saves
  v = drain(c);
  TEST_ASSERT_FALSE(has(v, Cmd::KeepClip));
  TEST_ASSERT_TRUE(has(v, Cmd::StartStory));
}

static void test_tap_that_interrupts_playback_does_not_keep() {
  StoryController c;
  c.on_button(ButtonEvent::Press, 0);
  c.on_button(ButtonEvent::LongStart, 600);
  c.on_button(ButtonEvent::LongRelease, 2600);
  c.on_record_done(true, 2000, 2600);
  drain(c);
  tap(c, 3000);  // during the read-back
  auto v = drain(c);
  TEST_ASSERT_TRUE(has(v, Cmd::Stop));
  TEST_ASSERT_FALSE(has(v, Cmd::KeepClip));
  TEST_ASSERT_EQUAL(StoryState::AwaitKeep, c.state());
  tap(c, 5000);  // a deliberate second tap keeps it
  TEST_ASSERT_TRUE(has(drain(c), Cmd::KeepClip));
}

static void test_too_short_or_failed_recording_is_discarded() {
  StoryController c;
  c.on_button(ButtonEvent::Press, 0);
  c.on_button(ButtonEvent::LongStart, 600);
  c.on_button(ButtonEvent::LongRelease, 800);
  c.on_record_done(true, 200, 800);
  auto v = drain(c);
  TEST_ASSERT_TRUE(has(v, Cmd::DiscardClip));
  TEST_ASSERT_TRUE(has_chime(v, Chime::TooShort));
  TEST_ASSERT_FALSE(has(v, Cmd::PlayClip));
  c.on_button(ButtonEvent::Press, 5000);
  c.on_button(ButtonEvent::LongStart, 5600);
  c.on_record_done(false, 0, 6000);
  v = drain(c);
  TEST_ASSERT_TRUE(has(v, Cmd::DiscardClip));
  TEST_ASSERT_TRUE(has_chime(v, Chime::Error));
  TEST_ASSERT_EQUAL(StoryState::Idle, c.state());
}

static void test_parent_web_mode_never_opens_the_mic() {
  gs::StoryCtlConfig cfg;
  cfg.mode = gs::CaptureMode::ParentWeb;
  StoryController c(cfg);
  c.on_button(ButtonEvent::Press, 0);
  c.on_button(ButtonEvent::LongStart, 600);
  c.on_button(ButtonEvent::LongRelease, 3000);
  auto v = drain(c);
  TEST_ASSERT_FALSE(has(v, Cmd::StartRecord));
  TEST_ASSERT_EQUAL(Indicator::Off, c.indicator(700));
  cfg.mode = gs::CaptureMode::None;
  StoryController n(cfg);
  n.on_button(ButtonEvent::LongStart, 600);
  TEST_ASSERT_FALSE(has(drain(n), Cmd::StartRecord));
}

static void test_full_memory_refuses_to_record() {
  StoryController c;
  c.set_can_save(false);
  c.on_button(ButtonEvent::Press, 0);
  c.on_button(ButtonEvent::LongStart, 600);
  auto v = drain(c);
  TEST_ASSERT_FALSE(has(v, Cmd::StartRecord));
  TEST_ASSERT_TRUE(has_chime(v, Chime::Full));
  TEST_ASSERT_EQUAL(StoryState::Idle, c.state());
}

static void test_hold_during_keep_window_records_again() {
  StoryController c;
  c.on_button(ButtonEvent::Press, 0);
  c.on_button(ButtonEvent::LongStart, 600);
  c.on_button(ButtonEvent::LongRelease, 2600);
  c.on_record_done(true, 2000, 2600);
  c.on_playback_done(4600);
  drain(c);
  c.on_button(ButtonEvent::Press, 6000);
  c.on_button(ButtonEvent::LongStart, 6600);
  auto v = drain(c);
  TEST_ASSERT_EQUAL(2, v.size());
  TEST_ASSERT_EQUAL(Cmd::DiscardClip, v[0].cmd);
  TEST_ASSERT_EQUAL(Cmd::StartRecord, v[1].cmd);
  TEST_ASSERT_EQUAL(StoryState::Recording, c.state());
}

static void test_hold_during_story_stops_then_records() {
  StoryController c;
  tap(c, 0);
  drain(c);
  c.on_button(ButtonEvent::Press, 3000);
  c.on_button(ButtonEvent::LongStart, 3600);
  auto v = drain(c);
  TEST_ASSERT_EQUAL(Cmd::Stop, v[0].cmd);
  TEST_ASSERT_EQUAL(Cmd::StartRecord, v[1].cmd);
}

static void test_recorder_watchdog_closes_mic() {
  StoryController c;
  c.on_button(ButtonEvent::Press, 0);
  c.on_button(ButtonEvent::LongStart, 600);
  drain(c);
  c.tick(600 + 3000 + 750);  // the recorder never reported (clip_ms is 3 s by default, MIN-38)
  TEST_ASSERT_TRUE(has(drain(c), Cmd::StopRecord));
}

static void test_watchdog_counts_from_when_the_mic_opened() {
  // MIN-38: the spoken cue plays before the mic opens; the safety net starts when it really opens.
  StoryController c;
  c.on_button(ButtonEvent::Press, 0);
  TEST_ASSERT_EQUAL(Indicator::Listening, c.indicator(10));  // listening face at once, before the hold
  c.on_button(ButtonEvent::LongStart, 600);
  drain(c);
  c.on_record_started(2100);  // a 1.5 s cue
  c.tick(600 + 3000 + 750);
  TEST_ASSERT_FALSE(has(drain(c), Cmd::StopRecord));
  c.tick(2100 + 3000 + 750);
  TEST_ASSERT_TRUE(has(drain(c), Cmd::StopRecord));
}

static void test_tap_in_parent_web_mode_never_shows_listening() {
  gs::StoryCtlConfig cfg;
  cfg.mode = gs::CaptureMode::ParentWeb;
  StoryController c(cfg);
  c.on_button(ButtonEvent::Press, 0);
  TEST_ASSERT_NOT_EQUAL(Indicator::Listening, c.indicator(10));
}

static void test_parent_stop_discards_a_take() {
  StoryController c;
  c.on_button(ButtonEvent::Press, 0);
  c.on_button(ButtonEvent::LongStart, 600);
  drain(c);
  c.request_stop(1000);
  auto v = drain(c);
  TEST_ASSERT_TRUE(has(v, Cmd::StopRecord));
  TEST_ASSERT_TRUE(has(v, Cmd::DiscardClip));
  c.on_record_done(true, 400, 1010);  // late report is ignored
  TEST_ASSERT_FALSE(has(drain(c), Cmd::PlayClip));
}

// End to end with the real button timing: raw levels in, commands out.
static void test_button_fsm_drives_controller() {
  gs::ButtonFsm b(gs::ButtonConfig{30, 600});
  StoryController c;
  std::vector<gs::Command> all;
  auto run = [&](uint32_t t0, uint32_t t1, bool level) {
    for (uint32_t t = t0; t < t1; ++t) {
      c.on_button(b.update(t, level), t);
      c.tick(t);
      gs::Command cmd;
      while (c.pop(&cmd)) {
        all.push_back(cmd);
        if (cmd.cmd == Cmd::StopRecord) c.on_record_done(true, t - c.record_started(), t);
        if (cmd.cmd == Cmd::PlayClip) { /* clip plays for 2 s */ }
      }
    }
  };
  run(0, 100, false);
  run(100, 2700, true);   // hold 2.6 s: long press at 700, release at 2700
  run(2700, 3000, false);
  c.on_playback_done(3000);
  run(3000, 3500, false);
  run(3500, 3600, true);  // tap to keep
  run(3600, 3800, false);
  bool rec = false, play = false, keep = false;
  for (auto& x : all) {
    rec = rec || x.cmd == Cmd::StartRecord;
    play = play || x.cmd == Cmd::PlayClip;
    keep = keep || x.cmd == Cmd::KeepClip;
  }
  TEST_ASSERT_TRUE(rec);
  TEST_ASSERT_TRUE(play);
  TEST_ASSERT_TRUE(keep);
}

void register_story_ctl_tests() {
  RUN_TEST(test_tap_plays_and_tap_during_story_stops);
  RUN_TEST(test_story_end_returns_to_idle);
  RUN_TEST(test_hold_records_listens_blue_plays_back_and_tap_keeps);
  RUN_TEST(test_no_tap_within_window_discards);
  RUN_TEST(test_tap_that_interrupts_playback_does_not_keep);
  RUN_TEST(test_too_short_or_failed_recording_is_discarded);
  RUN_TEST(test_parent_web_mode_never_opens_the_mic);
  RUN_TEST(test_full_memory_refuses_to_record);
  RUN_TEST(test_hold_during_keep_window_records_again);
  RUN_TEST(test_hold_during_story_stops_then_records);
  RUN_TEST(test_recorder_watchdog_closes_mic);
  RUN_TEST(test_watchdog_counts_from_when_the_mic_opened);
  RUN_TEST(test_tap_in_parent_web_mode_never_shows_listening);
  RUN_TEST(test_parent_stop_discards_a_take);
  RUN_TEST(test_button_fsm_drives_controller);
}
