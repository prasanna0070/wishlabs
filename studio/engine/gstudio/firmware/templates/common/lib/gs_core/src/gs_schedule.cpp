#include "gs_schedule.h"

#include <stdio.h>

namespace gs {

void DailySchedule::set(const uint16_t* minutes, uint8_t n) {
  n_ = 0;
  primed_ = false;
  for (uint8_t i = 0; i < n && n_ < kMax; ++i) {
    if (minutes[i] >= 1440) continue;
    min_[n_] = minutes[i];
    done_[n_] = 0;
    ++n_;
  }
}

int DailySchedule::due(uint32_t day, uint16_t minute_of_day) {
  if (minute_of_day >= 1440) return -1;
  const uint32_t mark = day + 1;
  if (!primed_) {
    primed_ = true;
    for (uint8_t i = 0; i < n_; ++i)
      if (minute_of_day >= min_[i] && minute_of_day - min_[i] >= kGraceMin) done_[i] = mark;
  }
  for (uint8_t i = 0; i < n_; ++i) {
    if (done_[i] == mark) continue;
    if (minute_of_day >= min_[i] && minute_of_day - min_[i] < kGraceMin) {
      done_[i] = mark;
      return i;
    }
  }
  return -1;
}

int DailySchedule::next(uint16_t minute_of_day, uint16_t* wait_min) const {
  int best = -1;
  uint16_t best_wait = 0;
  for (uint8_t i = 0; i < n_; ++i) {
    const uint16_t w = min_[i] > minute_of_day ? uint16_t(min_[i] - minute_of_day) : uint16_t(1440 - minute_of_day + min_[i]);
    if (best < 0 || w < best_wait) {
      best = i;
      best_wait = w;
    }
  }
  if (wait_min) *wait_min = best_wait;
  return best;
}

void format_hhmm(uint16_t minute_of_day, char* out, size_t cap) {
  snprintf(out, cap, "%02u:%02u", unsigned((minute_of_day / 60) % 24), unsigned(minute_of_day % 60));
}

}  // namespace gs
