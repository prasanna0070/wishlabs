// gs_motion: the motion pack's single hobby servo (on J_LED, the only 5 V port), moved without blocking:
// wave on a tap, open a flap on a schedule. Every call is a no-op when the design has no servo
// (GS_HAS_SERVO 0). The 50 Hz pulses stop once the arm has settled, so the servo never keeps pushing
// against a stop (stall current on USB 5 V). Angles 0..180 degrees, speeds in degrees per second.
// App settings (servo_*) live in the app's config.h: pass them in, this file reads none.
#pragma once
#include <Arduino.h>

#include "hardware.h"

namespace gsmotion {

bool present();                       // true when the design has a servo
void begin(uint8_t rest_deg);         // go to rest once (the angle at power-up is unknown)
void move_to(uint8_t deg, uint16_t speed_deg_s);
void wave(uint8_t n, uint8_t amplitude_deg, uint8_t rest_deg, uint16_t speed_deg_s);
void pulse(uint8_t open_deg, uint8_t rest_deg, uint32_t hold_ms, uint16_t speed_deg_s);
void stop();
void tick(uint32_t now);              // call every loop
bool busy();
uint8_t angle();

}  // namespace gsmotion
