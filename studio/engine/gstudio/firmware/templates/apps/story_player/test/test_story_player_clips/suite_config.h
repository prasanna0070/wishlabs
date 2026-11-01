// This suite runs the story_player in the opt-in child_voice_clip mode (microphone on), with the
// network off, so the clip, erase, retention and listening-indicator code is exercised.
#pragma once
#include "config.h"
#include "hardware.h"
#undef GS_CFG_CAPTURE_MODE
#define GS_CFG_CAPTURE_MODE 1
#undef GS_CFG_NETWORK
#define GS_CFG_NETWORK 0
#undef GS_HAS_MIC
#define GS_HAS_MIC 1
