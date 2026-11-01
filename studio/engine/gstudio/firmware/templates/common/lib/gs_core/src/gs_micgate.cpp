#include "gs_micgate.h"

namespace gs {

bool MicGate::set(bool on) {
  if (on == on_) return true;
  if (on) {
    if (indicator_) indicator_(true, indicator_ctx_);
    const bool ok = driver_ ? driver_(true, driver_ctx_) : false;
    if (!ok) {
      if (indicator_) indicator_(false, indicator_ctx_);
      return false;
    }
    on_ = true;
    ++opens_;
    return true;
  }
  const bool ok = driver_ ? driver_(false, driver_ctx_) : true;
  on_ = false;
  if (indicator_) indicator_(false, indicator_ctx_);
  return ok;
}

}  // namespace gs
