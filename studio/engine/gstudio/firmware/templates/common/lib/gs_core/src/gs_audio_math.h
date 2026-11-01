// Audio helpers with no hardware: WAV headers, volume curve, sample conversion, tones, levels.
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace gs {

struct WavInfo {
  uint32_t sample_rate = 0;
  uint16_t channels = 0;
  uint16_t bits = 0;
  uint32_t data_offset = 0;  // byte offset of the first sample in the file
  uint32_t data_bytes = 0;   // bytes of sample data
};

constexpr size_t kWavHeaderBytes = 44;

// Canonical 44-byte PCM header.
void wav_header(uint8_t out[kWavHeaderBytes], uint32_t rate, uint16_t bits, uint16_t channels, uint32_t data_bytes);

// Parses the start of a WAV file (pass at least the first 512 bytes, or the whole file if smaller).
// Accepts 16-bit PCM, mono or stereo, with extra chunks (LIST, fact) before "data". A data size of
// 0 or 0xFFFFFFFF (streamed) is replaced by file_bytes - data_offset when file_bytes is given.
bool wav_parse(const uint8_t* buf, size_t len, uint32_t file_bytes, WavInfo* out);

// Software volume (0..100, capped at volume_max) to a Q12 gain with a perceptual square curve.
// This is a multiplier on the digital signal, not a measured sound level.
uint16_t volume_gain_q12(uint8_t volume, uint8_t volume_max);
void apply_gain(int16_t* samples, size_t n, uint16_t gain_q12);

// INMP441 delivers 24-bit samples left-justified in 32-bit slots. shift picks the 16-bit window
// (larger shift = quieter); the result saturates.
int16_t mic_to_pcm16(int32_t raw, uint8_t shift);

// Fills n mono samples of a sine tone (phase is carried between calls, Q32 turn).
void tone_fill(int16_t* out, size_t n, uint32_t rate, uint32_t freq_hz, uint32_t* phase, int16_t amplitude);

// RMS level in dBFS (-120 for silence) and peak absolute sample.
float rms_dbfs(const int16_t* s, size_t n);
int16_t peak_abs(const int16_t* s, size_t n);

// Fade a block in or out linearly (avoids clicks at clip edges).
void fade(int16_t* s, size_t n, bool fade_in);

}  // namespace gs
