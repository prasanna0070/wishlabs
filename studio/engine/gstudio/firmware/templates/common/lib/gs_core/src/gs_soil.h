// Soil probe and float switch logic that needs no hardware (sensing pack): the calibration mapping and a
// filter for a reading that must hold before it counts. Builds for the ESP32-S3 and for the native tests.
#pragma once
#include <stdint.h>

namespace gs {

// 12-bit probe reading -> 0..100 % between the dry calibration (0 %) and the wet one (100 %), rounded and
// clamped. A capacitive probe reads lower when wetter, so raw_wet must be below raw_dry; otherwise -1.
// This is relative moisture for a dry warning, not a volumetric water measurement.
int soil_percent(int raw, int raw_dry, int raw_wet);

// Integer mean of n readings (n >= 1); 0 when n < 1.
int mean_of(const int* v, int n);

// A yes/no reading that changes only after the new value is seen `need` times in a row (water sloshes,
// contacts bounce). Starts at `initial`.
class Stable {
 public:
  explicit Stable(uint8_t need, bool initial) : need_(need ? need : 1), value_(initial) {}
  bool update(bool v);
  bool value() const { return value_; }

 private:
  uint8_t need_;
  uint8_t count_ = 0;
  bool value_;
};

}  // namespace gs
