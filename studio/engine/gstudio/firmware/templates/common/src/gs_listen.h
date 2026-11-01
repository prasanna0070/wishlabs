// The listening indicator. src/gs_audio.cpp's mic gate calls gs_listening_indicator(true) before the
// microphone channel starts and (false) after it stops, on every path: recording, the factory
// self-test's level check and clap detection (MIN-16). On the UI task (setup/loop, which also runs
// the self-test) the light and face change at once; from the microphone task the change is applied
// by the next loop pass (gslisten::apply()), where the story_player already shows listening.
#pragma once
#include <stdint.h>

void gs_listening_indicator(bool on);

namespace gslisten {

void set_ui_task(void* task);
// A template that shows the microphone its own way (the desk companion's face dot) returns true.
void set_override(bool (*fn)(bool on));
bool active();
void apply(uint32_t now);  // call every loop pass

}  // namespace gslisten
