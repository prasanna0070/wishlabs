// Factory self-test over the serial port (UART0: the devkit's UART USB port, or test pads
// TXD0/RXD0). Blueprint row 49: LED, button, amplifier tone, microphone level, microSD presence,
// plus display, I2C sensors, memory and firmware identity.
//
// Send "selftest" (115200 baud, newline). Each check prints one machine-readable line:
//   GS-TEST {"id":"sd","status":"pass","detail":{...}}
// status: pass | fail | operator_check (the device drove it; a person must confirm what they
// saw or heard) | skipped (not in this design). The run ends with:
//   GS-SELFTEST {"ok":true,"passed":n,"failed":n,"operator_checks":[...],...}
// "ok" covers the automatic checks only. It is not a physical, acoustic or safety certification.
#pragma once
#include <Arduino.h>

namespace gsselftest {

void poll();  // reads serial commands; runs the self-test when asked
bool running();
// Quick, non-interactive checks used after an update (OTA keep-or-roll-back decision).
bool boot_checks(String* detail);

}  // namespace gsselftest
