// This suite runs the story_player in its default mode: names typed by a parent (parent_web), with
// the parent page on so the API the parent uses is exercised.
#pragma once
#include "config.h"
#include "hardware.h"
#undef GS_CFG_CAPTURE_MODE
#define GS_CFG_CAPTURE_MODE 2
#undef GS_CFG_NETWORK
#define GS_CFG_NETWORK 1
