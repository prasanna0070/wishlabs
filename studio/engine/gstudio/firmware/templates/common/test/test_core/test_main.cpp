// Native (host) unit tests of the hardware-free firmware logic. Run: pio test -e native
#include <unity.h>

void setUp(void) {}
void tearDown(void) {}

void register_button_tests();
void register_store_tests();
void register_story_tests();
void register_story_ctl_tests();
void register_light_audio_tests();
void register_template_tests();

int main(int, char**) {
  UNITY_BEGIN();
  register_button_tests();
  register_store_tests();
  register_story_tests();
  register_story_ctl_tests();
  register_light_audio_tests();
  register_template_tests();
  return UNITY_END();
}
