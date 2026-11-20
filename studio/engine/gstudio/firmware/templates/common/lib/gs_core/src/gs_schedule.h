// gs_schedule: a daily list of local times that each fire once a day (notifier_clock reminders: medicine box,
// feeding and watering). Hardware-free: the app passes the local day and the minute of the day.
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace gs {

class DailySchedule {
 public:
  static constexpr uint8_t kMax = 8;
  static constexpr uint16_t kGraceMin = 10;  // an entry still fires up to 10 minutes late (clock set late, slow loop)

  // minutes after local midnight (0..1439); entries past kMax and out-of-range minutes are ignored.
  void set(const uint16_t* minutes, uint8_t n);
  // day: any number that changes at local midnight (for example year * 1000 + day of the year).
  // Returns the index of an entry that is due now, once per day, or -1. On the first call (clock just set),
  // entries more than kGraceMin minutes in the past count as done today, so a reboot never replays the day.
  int due(uint32_t day, uint16_t minute_of_day);
  // The next entry after minute_of_day (today, else the first one tomorrow); -1 when empty.
  // *wait_min = minutes until it (1..1440).
  int next(uint16_t minute_of_day, uint16_t* wait_min) const;
  uint8_t count() const { return n_; }
  uint16_t minute(uint8_t i) const { return i < n_ ? min_[i] : 0; }

 private:
  uint16_t min_[kMax] = {};
  uint32_t done_[kMax] = {};  // day + 1 of the last firing (0 = never)
  uint8_t n_ = 0;
  bool primed_ = false;
};

// "08:05" for minute 485 (always 24-hour, zero padded); out must hold 6 bytes.
void format_hhmm(uint16_t minute_of_day, char* out, size_t cap);

}  // namespace gs
