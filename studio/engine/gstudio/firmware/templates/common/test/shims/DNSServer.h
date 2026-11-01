// Native-test shim: captive-portal DNS (records whether it runs).
#pragma once
#include "Arduino.h"

enum class DNSReplyCode { NoError = 0, ServerFailure = 2, NonExistentDomain = 3 };
class DNSServer {
 public:
  void setErrorReplyCode(DNSReplyCode) {}
  bool start(uint16_t, const String&, const IPAddress&) {
    running = true;
    return true;
  }
  void stop() { running = false; }
  void processNextRequest() {}
  bool running = false;
};
