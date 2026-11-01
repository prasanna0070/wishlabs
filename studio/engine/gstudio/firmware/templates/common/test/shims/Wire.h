// Native-test shim: I2C bus with no devices (every probe is a NACK).
#pragma once
#include "Arduino.h"

class TwoWire {
 public:
  bool begin(int = -1, int = -1, uint32_t = 0) { return true; }
  void setTimeOut(uint16_t) {}
  void beginTransmission(uint8_t) {}
  uint8_t endTransmission(bool = true) { return 2; }
};
inline TwoWire Wire;
