#include "gs_audio_math.h"

#include <math.h>

namespace gs {

static void put32(uint8_t* p, uint32_t v) {
  p[0] = uint8_t(v);
  p[1] = uint8_t(v >> 8);
  p[2] = uint8_t(v >> 16);
  p[3] = uint8_t(v >> 24);
}
static void put16(uint8_t* p, uint16_t v) {
  p[0] = uint8_t(v);
  p[1] = uint8_t(v >> 8);
}
static uint32_t get32(const uint8_t* p) {
  return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}
static uint16_t get16(const uint8_t* p) { return uint16_t(p[0] | (p[1] << 8)); }
static bool tag(const uint8_t* p, const char* t) { return p[0] == t[0] && p[1] == t[1] && p[2] == t[2] && p[3] == t[3]; }

void wav_header(uint8_t out[kWavHeaderBytes], uint32_t rate, uint16_t bits, uint16_t channels, uint32_t data_bytes) {
  const uint16_t block = uint16_t(channels * (bits / 8));
  out[0] = 'R'; out[1] = 'I'; out[2] = 'F'; out[3] = 'F';
  put32(out + 4, 36 + data_bytes);
  out[8] = 'W'; out[9] = 'A'; out[10] = 'V'; out[11] = 'E';
  out[12] = 'f'; out[13] = 'm'; out[14] = 't'; out[15] = ' ';
  put32(out + 16, 16);
  put16(out + 20, 1);  // PCM
  put16(out + 22, channels);
  put32(out + 24, rate);
  put32(out + 28, rate * block);
  put16(out + 32, block);
  put16(out + 34, bits);
  out[36] = 'd'; out[37] = 'a'; out[38] = 't'; out[39] = 'a';
  put32(out + 40, data_bytes);
}

bool wav_parse(const uint8_t* buf, size_t len, uint32_t file_bytes, WavInfo* out) {
  if (!buf || len < 12 || !tag(buf, "RIFF") || !tag(buf + 8, "WAVE")) return false;
  size_t pos = 12;
  bool have_fmt = false;
  WavInfo info;
  while (pos + 8 <= len) {
    const uint8_t* ck = buf + pos;
    const uint32_t size = get32(ck + 4);
    if (tag(ck, "fmt ")) {
      if (pos + 8 + 16 > len || size < 16) return false;
      const uint16_t format = get16(ck + 8);
      info.channels = get16(ck + 10);
      info.sample_rate = get32(ck + 12);
      info.bits = get16(ck + 22);
      // 1 = PCM; 0xFFFE = WAVE_FORMAT_EXTENSIBLE (ffmpeg uses it for some layouts).
      if (format != 1 && format != 0xFFFE) return false;
      have_fmt = true;
    } else if (tag(ck, "data")) {
      if (!have_fmt) return false;
      info.data_offset = uint32_t(pos + 8);
      info.data_bytes = size;
      if ((size == 0 || size == 0xFFFFFFFFu) && file_bytes > info.data_offset) {
        info.data_bytes = file_bytes - info.data_offset;
      }
      if (file_bytes && info.data_offset + info.data_bytes > file_bytes) {
        info.data_bytes = file_bytes > info.data_offset ? file_bytes - info.data_offset : 0;
      }
      if (info.bits != 16 || info.channels < 1 || info.channels > 2) return false;
      if (info.sample_rate < 8000 || info.sample_rate > 48000) return false;
      *out = info;
      return true;
    }
    pos += 8 + size + (size & 1u);  // chunks are word aligned
  }
  return false;
}

uint16_t volume_gain_q12(uint8_t volume, uint8_t volume_max) {
  if (volume > volume_max) volume = volume_max;
  if (volume > 100) volume = 100;
  const uint32_t v = volume;
  return uint16_t((v * v * 4096u + 5000u) / 10000u);
}

void apply_gain(int16_t* s, size_t n, uint16_t gain_q12) {
  for (size_t i = 0; i < n; ++i) {
    int32_t v = (int32_t(s[i]) * int32_t(gain_q12)) >> 12;
    if (v > 32767) v = 32767;
    if (v < -32768) v = -32768;
    s[i] = int16_t(v);
  }
}

int16_t mic_to_pcm16(int32_t raw, uint8_t shift) {
  int32_t v = raw >> shift;
  if (v > 32767) v = 32767;
  if (v < -32768) v = -32768;
  return int16_t(v);
}

void tone_fill(int16_t* out, size_t n, uint32_t rate, uint32_t freq_hz, uint32_t* phase, int16_t amplitude) {
  if (rate == 0) return;
  const uint32_t step = uint32_t((uint64_t(freq_hz) << 32) / rate);
  uint32_t ph = phase ? *phase : 0;
  for (size_t i = 0; i < n; ++i) {
    const float turn = float(ph) / 4294967296.0f;
    out[i] = int16_t(sinf(turn * 6.28318530718f) * float(amplitude));
    ph += step;
  }
  if (phase) *phase = ph;
}

float rms_dbfs(const int16_t* s, size_t n) {
  if (n == 0) return -120.0f;
  double acc = 0;
  for (size_t i = 0; i < n; ++i) acc += double(s[i]) * double(s[i]);
  const double rms = sqrt(acc / double(n));
  if (rms < 1.0) return -120.0f;
  return float(20.0 * log10(rms / 32768.0));
}

int16_t peak_abs(const int16_t* s, size_t n) {
  int32_t p = 0;
  for (size_t i = 0; i < n; ++i) {
    int32_t v = s[i] < 0 ? -int32_t(s[i]) : int32_t(s[i]);
    if (v > p) p = v;
  }
  return int16_t(p > 32767 ? 32767 : p);
}

void fade(int16_t* s, size_t n, bool fade_in) {
  if (n == 0) return;
  for (size_t i = 0; i < n; ++i) {
    const int32_t k = fade_in ? int32_t(i) : int32_t(n - 1 - i);
    s[i] = int16_t((int32_t(s[i]) * k) / int32_t(n));
  }
}

}  // namespace gs
