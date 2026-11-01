// Native-test shim: SPI bus (nothing to drive on a computer).
#pragma once
#include "Arduino.h"

class SPIClass {
 public:
  void begin(int8_t = -1, int8_t = -1, int8_t = -1, int8_t = -1) {}
  void end() {}
};
inline SPIClass SPI;
