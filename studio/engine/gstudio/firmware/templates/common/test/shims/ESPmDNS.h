// Native-test shim: mDNS responder.
#pragma once
#include "Arduino.h"

class MDNSResponder {
 public:
  bool begin(const char*) { return true; }
  bool addService(const char*, const char*, uint16_t) { return true; }
  bool addServiceTxt(const char*, const char*, const char*, const char*) { return true; }
};
inline MDNSResponder MDNS;
