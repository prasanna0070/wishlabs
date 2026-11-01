// Audio: MAX98357A speaker output and INMP441 microphone input over the ESP32-S3's two I2S
// peripherals (I2S0 out, I2S1 in). Playback runs in its own task so the button and the web page
// stay responsive. The microphone channel is enabled only while recording or measuring.
#pragma once
#include <Arduino.h>

#include "gs_story.h"
#include "gs_story_ctl.h"

namespace gsaudio {

// ---- speaker
bool begin();
bool ready();
void set_volume(uint8_t volume, uint8_t volume_max);  // software scale, capped
bool play_list(const gs::PlayItem* items, size_t n);
bool play_file(const char* path);
bool play_chime(gs::Chime c);
bool play_name_chime();
bool play_tone(uint32_t freq_hz, uint32_t ms, uint8_t level_percent);
void stop();             // blocks until playback has stopped (normally under 50 ms)
bool busy();
bool take_finished();    // true once after a job ends, naturally or by stop()
uint8_t last_missing();  // how many files of the last job were missing or unreadable

// ---- microphone
bool mic_begin();
bool mic_ready();
bool record_start(const char* path, uint32_t max_ms);
void record_stop();
bool recording();
bool take_record_result(bool* ok, uint32_t* recorded_ms);
// Blocking level measurement for the factory self-test (opens and closes the mic).
bool mic_measure(uint32_t ms, float* rms_dbfs, int16_t* peak, bool* stuck);
// Level monitor (clap detection): opens the mic until turned off.
void mic_monitor(bool on);
bool mic_monitoring();
bool mic_take_block_peak(int16_t* peak);  // one value per 10 ms block while monitoring

constexpr uint32_t kMicRate = 16000;

}  // namespace gsaudio
