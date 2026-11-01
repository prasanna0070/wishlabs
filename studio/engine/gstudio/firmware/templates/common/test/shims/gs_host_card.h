// Story-card content for native tests: WAV files and a one-story pack on gs_host::sd.
#pragma once
#include <math.h>

#include <memory>
#include <string>
#include <vector>

#include "gs_audio_math.h"
#include "gs_host.h"

namespace gs_host {

inline std::vector<uint8_t> wav(uint32_t rate, size_t samples, int16_t amp = 3000) {
  std::vector<uint8_t> w(44 + samples * 2);
  gs::wav_header(w.data(), rate, 16, 1, uint32_t(samples * 2));
  for (size_t i = 0; i < samples; ++i) {
    const int16_t v = int16_t(amp * sinf(6.2831853f * 330.0f * float(i) / float(rate)));
    memcpy(&w[44 + 2 * i], &v, 2);
  }
  return w;
}

inline void put_file(const std::string& path, const std::vector<uint8_t>& bytes) {
  for (size_t p = path.find('/', 1); p != std::string::npos; p = path.find('/', p + 1)) sd.dirs.insert(path.substr(0, p));
  sd.files[path] = bytes;
}

// One story with three name slots (hero, friend, hero), its four segments, and optionally the
// spoken prompts. names: typed names the card carries (with their clips) for a network-off design.
inline void story_card(bool prompts, const std::vector<std::string>& names = {}) {
  std::string idx = "{\"language\":\"en-IN\",\"stories\":[{\"id\":\"mango_tree\",\"title\":\"The Mango Tree\",\"segments\":4,"
                    "\"slots\":[\"FAV\",\"FRIEND\",\"FAV\"]}],\"names\":[";
  for (size_t i = 0; i < names.size(); ++i) {
    std::string slug;
    for (char c : names[i]) slug += (c == ' ') ? '_' : char(tolower(c));
    idx += std::string(i ? "," : "") + "{\"name\":\"" + names[i] + "\",\"file\":\"names/" + slug + ".wav\"}";
    put_file("/names/" + slug + ".wav", wav(24000, 2400));
  }
  idx += "]}";
  put_file("/stories.json", std::vector<uint8_t>(idx.begin(), idx.end()));
  for (int s = 0; s < 4; ++s) put_file("/stories/mango_tree/s0" + std::to_string(s) + ".wav", wav(24000, 2400));
  if (prompts)
    for (const char* p : {"hello", "hold_to_tell", "tap_to_keep", "saved", "try_again"})
      put_file(std::string("/prompts/") + p + ".wav", wav(24000, 2400));
}

// The microphone: start-up noise the firmware discards, then seconds of a tone at amp (16-bit units).
inline void mic_tone(float seconds, int16_t amp, uint8_t shift = 13) {
  auto n = std::make_shared<size_t>(0);
  const size_t total = 1600 + size_t(16000 * seconds);
  i2s.mic = [n, total, amp, shift](int32_t* out) {
    if (*n >= total) return false;
    const float v = float(amp) * sinf(6.2831853f * 440.0f * float(*n) / 16000.0f);
    *out = int32_t(int32_t(v) * (1 << shift));
    ++*n;
    return true;
  };
}

inline bool was_read(const std::string& path) {
  for (const auto& r : sd.reads)
    if (r == path) return true;
  return false;
}

inline bool contains(const std::vector<uint8_t>& hay, const char* needle) {
  const size_t n = strlen(needle);
  for (size_t i = 0; i + n <= hay.size(); ++i)
    if (memcmp(hay.data() + i, needle, n) == 0) return true;
  return false;
}

}  // namespace gs_host
