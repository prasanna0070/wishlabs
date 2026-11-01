// Shared native (host) unit tests of the hardware-free firmware logic in lib/gs_core, run for every
// template: button timing, the crash-safe memory record, light and audio maths, clip encryption, the
// mic gate and the PIN guard. Each template's own behaviour is tested in test/test_<template>*.
// Run: pio test -e native
#include <unity.h>

void setUp(void) {}
void tearDown(void) {}

void register_button_tests();
void register_store_tests();
void register_light_audio_tests();
void register_privacy_tests();

int main(int, char**) {
  UNITY_BEGIN();
  register_button_tests();
  register_store_tests();
  register_light_audio_tests();
  register_privacy_tests();
  return UNITY_END();
}
