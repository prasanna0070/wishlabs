#include "gs_story_ctl.h"

#include "gs_util.h"

namespace gs {

const char* story_state_name(StoryState s) {
  switch (s) {
    case StoryState::Idle: return "idle";
    case StoryState::Playing: return "playing";
    case StoryState::Recording: return "recording";
    case StoryState::Reviewing: return "reviewing";
    case StoryState::AwaitKeep: return "await_keep";
  }
  return "unknown";
}

const char* indicator_name(Indicator i) {
  switch (i) {
    case Indicator::Off: return "off";
    case Indicator::Story: return "story";
    case Indicator::Listening: return "listening";
    case Indicator::KeepPrompt: return "keep_prompt";
    case Indicator::Saved: return "saved";
    case Indicator::Discarded: return "discarded";
    case Indicator::Error: return "error";
  }
  return "unknown";
}

const char* cmd_name(Cmd c) {
  switch (c) {
    case Cmd::StartStory: return "start_story";
    case Cmd::Stop: return "stop";
    case Cmd::StartRecord: return "start_record";
    case Cmd::StopRecord: return "stop_record";
    case Cmd::PlayClip: return "play_clip";
    case Cmd::KeepClip: return "keep_clip";
    case Cmd::DiscardClip: return "discard_clip";
    case Cmd::Chime: return "chime";
    default: return "none";
  }
}

const char* chime_name(Chime c) {
  switch (c) {
    case Chime::Ready: return "ready";
    case Chime::Saved: return "saved";
    case Chime::Discarded: return "discarded";
    case Chime::Error: return "error";
    case Chime::Full: return "full";
    case Chime::TooShort: return "too_short";
    default: return "none";
  }
}

void StoryController::emit(Cmd c, Chime ch) {
  if (len_ >= kQueue) return;  // cannot happen with the transitions below (max 2 per event)
  Command& slot = q_[(head_ + len_) % kQueue];
  slot.cmd = c;
  slot.chime = ch;
  ++len_;
}

bool StoryController::pop(Command* out) {
  if (len_ == 0) return false;
  *out = q_[head_];
  head_ = (head_ + 1) % kQueue;
  --len_;
  return true;
}

void StoryController::flash(Indicator ind, uint32_t now) {
  flash_ind_ = ind;
  flash_until_ = now + cfg_.flash_ms;
  flash_on_ = true;
}

void StoryController::start_recording(uint32_t now) {
  if (!can_save_) {
    emit(Cmd::Chime, Chime::Full);
    flash(Indicator::Error, now);
    state_ = StoryState::Idle;
    return;
  }
  record_started_ = now;
  state_ = StoryState::Recording;
  emit(Cmd::StartRecord);
}

void StoryController::open_keep_window(uint32_t now) {
  state_ = StoryState::AwaitKeep;
  keep_deadline_ = now + cfg_.keep_window_ms;
}

void StoryController::on_button(ButtonEvent ev, uint32_t now) {
  if (ev == ButtonEvent::None) return;
  if (ev == ButtonEvent::Press) {
    consumed_ = false;
    if (state_ == StoryState::Playing) {
      emit(Cmd::Stop);
      state_ = StoryState::Idle;
      consumed_ = true;
    } else if (state_ == StoryState::Reviewing) {
      emit(Cmd::Stop);
      open_keep_window(now);
      consumed_ = true;
    }
    return;
  }
  if (ev == ButtonEvent::LongStart) {
    if (cfg_.mode != CaptureMode::ChildVoiceClip) return;
    if (state_ == StoryState::Idle) {
      start_recording(now);
      consumed_ = true;
    } else if (state_ == StoryState::AwaitKeep) {
      emit(Cmd::DiscardClip);
      start_recording(now);
      consumed_ = true;
    }
    return;
  }
  if (ev == ButtonEvent::LongRelease) {
    if (state_ == StoryState::Recording) emit(Cmd::StopRecord);
    consumed_ = false;
    return;
  }
  if (ev == ButtonEvent::Tap) {
    const bool was_consumed = consumed_;
    consumed_ = false;
    if (was_consumed) return;
    if (state_ == StoryState::Idle) {
      emit(Cmd::StartStory);
      state_ = StoryState::Playing;
    } else if (state_ == StoryState::AwaitKeep) {
      if (!time_reached(now, keep_deadline_)) {
        emit(Cmd::KeepClip);
        state_ = StoryState::Idle;
      }
    }
  }
}

void StoryController::on_playback_done(uint32_t now) {
  if (state_ == StoryState::Playing) {
    state_ = StoryState::Idle;
  } else if (state_ == StoryState::Reviewing) {
    open_keep_window(now);
  }
}

void StoryController::on_record_done(bool ok, uint32_t recorded_ms, uint32_t now) {
  if (state_ != StoryState::Recording) return;
  if (!ok) {
    emit(Cmd::DiscardClip);
    emit(Cmd::Chime, Chime::Error);
    flash(Indicator::Error, now);
    state_ = StoryState::Idle;
    return;
  }
  if (recorded_ms < cfg_.min_clip_ms) {
    emit(Cmd::DiscardClip);
    emit(Cmd::Chime, Chime::TooShort);
    flash(Indicator::Discarded, now);
    state_ = StoryState::Idle;
    return;
  }
  emit(Cmd::PlayClip);
  state_ = StoryState::Reviewing;
}

void StoryController::on_keep_result(bool ok, uint32_t now) {
  if (ok) {
    emit(Cmd::Chime, Chime::Saved);
    flash(Indicator::Saved, now);
  } else {
    emit(Cmd::DiscardClip);
    emit(Cmd::Chime, Chime::Error);
    flash(Indicator::Error, now);
  }
}

void StoryController::tick(uint32_t now) {
  if (state_ == StoryState::Recording && time_reached(now, record_started_ + cfg_.clip_ms + 750)) {
    // Safety net: the recorder should have reported already. Never leave the mic open.
    emit(Cmd::StopRecord);
    record_started_ = now;  // re-arm rather than flooding the queue
  }
  if (state_ == StoryState::AwaitKeep && time_reached(now, keep_deadline_)) {
    emit(Cmd::DiscardClip);
    emit(Cmd::Chime, Chime::Discarded);
    flash(Indicator::Discarded, now);
    state_ = StoryState::Idle;
  }
  if (flash_on_ && time_reached(now, flash_until_)) flash_on_ = false;
}

void StoryController::request_play(uint32_t now) {
  (void)now;
  if (state_ == StoryState::Idle) {
    emit(Cmd::StartStory);
    state_ = StoryState::Playing;
  } else if (state_ == StoryState::Playing) {
    emit(Cmd::Stop);
    emit(Cmd::StartStory);
  }
}

void StoryController::request_stop(uint32_t now) {
  if (state_ == StoryState::Playing) {
    emit(Cmd::Stop);
    state_ = StoryState::Idle;
  } else if (state_ == StoryState::Recording) {
    // A parent stop discards the take: the recorder's later report is ignored in Idle.
    emit(Cmd::StopRecord);
    emit(Cmd::DiscardClip);
    flash(Indicator::Discarded, now);
    state_ = StoryState::Idle;
  } else if (state_ == StoryState::Reviewing || state_ == StoryState::AwaitKeep) {
    emit(Cmd::Stop);
    emit(Cmd::DiscardClip);
    flash(Indicator::Discarded, now);
    state_ = StoryState::Idle;
  }
}

Indicator StoryController::indicator(uint32_t now) const {
  if (state_ == StoryState::Recording) return Indicator::Listening;
  if (flash_on_ && !time_reached(now, flash_until_)) return flash_ind_;
  if (state_ == StoryState::Reviewing || state_ == StoryState::AwaitKeep) return Indicator::KeepPrompt;
  if (state_ == StoryState::Playing) return Indicator::Story;
  return Indicator::Off;
}

}  // namespace gs
