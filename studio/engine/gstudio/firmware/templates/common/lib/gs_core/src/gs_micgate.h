// The only way the firmware opens or closes the microphone channel. Opening shows the listening
// indicator BEFORE the driver starts the mic; closing stops the driver BEFORE the indicator clears.
// So no code path (recording, the factory self-test, clap detection) can have the mic on while the
// light and face say it is off.
#pragma once
#include <stdint.h>

namespace gs {

class MicGate {
 public:
  typedef bool (*DriverFn)(bool on, void* ctx);
  typedef void (*IndicatorFn)(bool on, void* ctx);

  void attach_driver(DriverFn fn, void* ctx) {
    driver_ = fn;
    driver_ctx_ = ctx;
  }
  void attach_indicator(IndicatorFn fn, void* ctx) {
    indicator_ = fn;
    indicator_ctx_ = ctx;
  }
  // Returns the driver's result. No change when the state is already as asked.
  bool set(bool on);
  bool is_on() const { return on_; }
  uint32_t opens() const { return opens_; }

 private:
  DriverFn driver_ = nullptr;
  void* driver_ctx_ = nullptr;
  IndicatorFn indicator_ = nullptr;
  void* indicator_ctx_ = nullptr;
  bool on_ = false;
  uint32_t opens_ = 0;
};

}  // namespace gs
