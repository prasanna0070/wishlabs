#include "gs_soil.h"

namespace gs {

int soil_percent(int raw, int raw_dry, int raw_wet) {
  if (raw_wet >= raw_dry) return -1;
  const long span = long(raw_dry) - long(raw_wet);
  long p = ((long(raw_dry) - long(raw)) * 100L + span / 2) / span;
  if (raw > raw_dry) p = 0;  // drier than the dry calibration (also avoids rounding a negative toward zero)
  if (p < 0) p = 0;
  if (p > 100) p = 100;
  return int(p);
}

int mean_of(const int* v, int n) {
  if (n < 1) return 0;
  long s = 0;
  for (int i = 0; i < n; ++i) s += v[i];
  return int((s + n / 2) / n);
}

bool Stable::update(bool v) {
  if (v == value_) {
    count_ = 0;
    return value_;
  }
  if (++count_ >= need_) {
    value_ = v;
    count_ = 0;
  }
  return value_;
}

}  // namespace gs
