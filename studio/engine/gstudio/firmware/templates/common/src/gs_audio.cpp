#include "gs_audio.h"

#include <FS.h>

#include "config.h"
#include "gs_audio_math.h"
#include "gs_board.h"
#include "hardware.h"

#if GS_HAS_SD
#include <SD.h>
#endif
#if GS_HAS_AMP || GS_HAS_MIC
#include <driver/i2s_std.h>
#endif

namespace gsaudio {

#if GS_HAS_AMP
// ------------------------------------------------------------------ speaker
enum class JobKind : uint8_t { List, File, Chime, NameChime, Tone };

struct Job {
  JobKind kind = JobKind::File;
  gs::Chime chime = gs::Chime::None;
  uint32_t freq = 0;
  uint32_t ms = 0;
  uint8_t level = 0;
};

static i2s_chan_handle_t s_tx = nullptr;
static QueueHandle_t s_jobs = nullptr;
static TaskHandle_t s_task = nullptr;
static volatile bool s_busy = false;
static volatile bool s_stop = false;
static volatile bool s_finished = false;
static volatile uint16_t s_gain = 0;
static volatile uint8_t s_missing = 0;
static uint32_t s_rate = 0;
static gs::PlayItem s_list[gs::kMaxPlayItems];
static size_t s_list_len = 0;
static char s_file[gs::kPathMax];
static constexpr uint32_t kSynthRate = 24000;

static void amp_enable(bool on) {
#if GS_PIN_AMP_SD >= 0
  digitalWrite(GS_PIN_AMP_SD, on ? HIGH : LOW);
  if (on) delay(10);  // MAX98357A turn-on time is a few ms
#else
  (void)on;
#endif
}

static bool set_rate(uint32_t rate) {
  if (rate == s_rate) return true;
  i2s_channel_disable(s_tx);
  i2s_std_clk_config_t clk = I2S_STD_CLK_DEFAULT_CONFIG(rate);
  const bool ok = i2s_channel_reconfig_std_clock(s_tx, &clk) == ESP_OK;
  i2s_channel_enable(s_tx);
  if (ok) s_rate = rate;
  return ok;
}

static void write_pcm(const int16_t* pcm, size_t samples) {
  size_t written = 0;
  i2s_channel_write(s_tx, pcm, samples * sizeof(int16_t), &written, 250);
}

static void write_silence(uint32_t ms) {
  static int16_t zero[256] = {0};
  uint32_t samples = (s_rate ? s_rate : kSynthRate) * ms / 1000;
  while (samples && !s_stop) {
    const size_t n = samples > 256 ? 256 : samples;
    write_pcm(zero, n);
    samples -= n;
  }
}

// A soft bell: sine with an exponential-ish decay envelope.
static void synth_note(uint32_t freq, uint32_t ms, uint8_t level_percent) {
  set_rate(kSynthRate);
  int16_t buf[240];
  uint32_t phase = 0;
  const uint32_t total = kSynthRate * ms / 1000;
  const int16_t amp = int16_t(int32_t(12000) * level_percent / 100);
  uint32_t done = 0;
  while (done < total && !s_stop) {
    const size_t n = (total - done) > 240 ? 240 : size_t(total - done);
    gs::tone_fill(buf, n, kSynthRate, freq, &phase, amp);
    for (size_t i = 0; i < n; ++i) {
      const uint32_t t = done + i;
      // envelope: 5 ms attack, then linear decay to zero at the end
      const uint32_t attack = kSynthRate / 200;
      int32_t env = t < attack ? int32_t(t * 256 / attack) : int32_t((total - t) * 256 / (total - attack + 1));
      buf[i] = int16_t((int32_t(buf[i]) * env) >> 8);
    }
    gs::apply_gain(buf, n, s_gain);
    write_pcm(buf, n);
    done += n;
  }
}

static void synth_chime(gs::Chime c) {
  switch (c) {
    case gs::Chime::Ready: synth_note(1047, 160, 70); synth_note(1319, 260, 70); break;
    case gs::Chime::Saved: synth_note(659, 140, 80); synth_note(784, 140, 80); synth_note(1047, 320, 80); break;
    case gs::Chime::Discarded: synth_note(784, 180, 60); synth_note(523, 320, 60); break;
    case gs::Chime::Error:
    case gs::Chime::Full: synth_note(440, 200, 70); write_silence(80); synth_note(440, 260, 70); break;
    case gs::Chime::TooShort: synth_note(587, 200, 60); break;
    default: break;
  }
}

static bool play_wav(const char* path) {
#if GS_HAS_SD
  if (!gsboard::sd_mounted()) return false;
  File f = SD.open(path, FILE_READ);
  if (!f) return false;
  uint8_t head[512];
  const size_t got = f.read(head, sizeof(head));
  gs::WavInfo wi;
  if (!gs::wav_parse(head, got, uint32_t(f.size()), &wi) || !set_rate(wi.sample_rate)) {
    f.close();
    return false;
  }
  f.seek(wi.data_offset);
  uint32_t left = wi.data_bytes;
  static int16_t pcm[512];
  bool first = true;
  const size_t frame = size_t(wi.channels) * 2;
  while (left >= frame && !s_stop) {
    size_t want = sizeof(pcm);
    if (want > left) want = left;
    want -= want % frame;
    const size_t n = f.read(reinterpret_cast<uint8_t*>(pcm), want);
    if (n < frame) break;
    size_t samples = n / 2;
    if (wi.channels == 2) {
      for (size_t i = 0; i < samples / 2; ++i) pcm[i] = int16_t((int32_t(pcm[2 * i]) + pcm[2 * i + 1]) / 2);
      samples /= 2;
    }
    gs::apply_gain(pcm, samples, s_gain);
    if (first) {
      gs::fade(pcm, samples < 48 ? samples : 48, true);  // no click at the start
      first = false;
    }
    write_pcm(pcm, samples);
    left -= uint32_t(n);
  }
  f.close();
  return true;
#else
  (void)path;
  return false;
#endif
}

static void audio_task(void*) {
  Job job;
  for (;;) {
    if (xQueueReceive(s_jobs, &job, portMAX_DELAY) != pdTRUE) continue;
    s_busy = true;
    s_missing = 0;
    amp_enable(true);
    switch (job.kind) {
      case JobKind::List:
        for (size_t i = 0; i < s_list_len && !s_stop; ++i) {
          const gs::PlayItem& it = s_list[i];
          if (it.kind == gs::PlayKind::Chime) {
            synth_note(880, 260, 55);
          } else if (!play_wav(it.path)) {
            s_missing = uint8_t(s_missing + 1);
            synth_note(660, 160, 40);  // a soft placeholder so a missing file is audible, not silent
          }
        }
        break;
      case JobKind::File:
        if (!play_wav(s_file)) s_missing = 1;
        break;
      case JobKind::Chime: synth_chime(job.chime); break;
      case JobKind::NameChime: synth_note(880, 260, 55); break;
      case JobKind::Tone: synth_note(job.freq, job.ms, job.level); break;
    }
    write_silence(40);  // let the DMA drain cleanly
    amp_enable(false);
    s_stop = false;
    s_busy = false;
    s_finished = true;
  }
}

bool begin() {
  if (s_tx) return true;
#if GS_PIN_AMP_SD >= 0
  pinMode(GS_PIN_AMP_SD, OUTPUT);
  digitalWrite(GS_PIN_AMP_SD, LOW);
#endif
  i2s_chan_config_t cc = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
  cc.auto_clear = true;  // underrun sends silence, not a buzz
  cc.dma_desc_num = 6;
  cc.dma_frame_num = 240;
  if (i2s_new_channel(&cc, &s_tx, nullptr) != ESP_OK) return false;
  i2s_std_config_t sc;
  memset(&sc, 0, sizeof(sc));
  sc.clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(kSynthRate);
  sc.slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO);
  sc.slot_cfg.slot_mask = I2S_STD_SLOT_BOTH;  // same sample on left and right: any SD_MODE wiring plays it
  sc.gpio_cfg.mclk = I2S_GPIO_UNUSED;
  sc.gpio_cfg.bclk = gpio_num_t(GS_PIN_I2S_SPK_BCLK);
  sc.gpio_cfg.ws = gpio_num_t(GS_PIN_I2S_SPK_LRC);
  sc.gpio_cfg.dout = gpio_num_t(GS_PIN_I2S_SPK_DIN);
  sc.gpio_cfg.din = I2S_GPIO_UNUSED;
  if (i2s_channel_init_std_mode(s_tx, &sc) != ESP_OK || i2s_channel_enable(s_tx) != ESP_OK) {
    i2s_del_channel(s_tx);
    s_tx = nullptr;
    return false;
  }
  s_rate = kSynthRate;
  s_jobs = xQueueCreate(2, sizeof(Job));
  // Core 1 (with loop()), above loop priority so audio never starves; Wi-Fi stays on core 0.
  xTaskCreatePinnedToCore(audio_task, "gs_audio", 6144, nullptr, 3, &s_task, 1);
  return s_jobs && s_task;
}

bool ready() { return s_tx != nullptr; }

void set_volume(uint8_t volume, uint8_t volume_max) { s_gain = gs::volume_gain_q12(volume, volume_max); }

void stop() {
  if (!s_busy) return;
  s_stop = true;
  const uint32_t t0 = millis();
  while (s_busy && millis() - t0 < 500) vTaskDelay(pdMS_TO_TICKS(2));
}

static bool submit(const Job& job) {
  if (!s_tx) return false;
  stop();
  xQueueReset(s_jobs);
  s_finished = false;
  s_stop = false;
  return xQueueSend(s_jobs, &job, pdMS_TO_TICKS(50)) == pdTRUE;
}

bool play_list(const gs::PlayItem* items, size_t n) {
  if (!s_tx) return false;
  stop();
  s_list_len = n > gs::kMaxPlayItems ? gs::kMaxPlayItems : n;
  for (size_t i = 0; i < s_list_len; ++i) s_list[i] = items[i];
  Job j;
  j.kind = JobKind::List;
  return submit(j);
}

bool play_file(const char* path) {
  if (!s_tx) return false;
  stop();
  gs::copy_str(s_file, sizeof(s_file), path);
  Job j;
  j.kind = JobKind::File;
  return submit(j);
}

bool play_chime(gs::Chime c) {
  Job j;
  j.kind = JobKind::Chime;
  j.chime = c;
  return submit(j);
}

bool play_name_chime() {
  Job j;
  j.kind = JobKind::NameChime;
  return submit(j);
}

bool play_tone(uint32_t freq_hz, uint32_t ms, uint8_t level_percent) {
  Job j;
  j.kind = JobKind::Tone;
  j.freq = freq_hz;
  j.ms = ms;
  j.level = level_percent > 100 ? 100 : level_percent;
  return submit(j);
}

bool busy() { return s_busy; }

bool take_finished() {
  if (!s_finished) return false;
  s_finished = false;
  return true;
}

uint8_t last_missing() { return s_missing; }

#else  // no amplifier in this design
bool begin() { return false; }
bool ready() { return false; }
void set_volume(uint8_t, uint8_t) {}
bool play_list(const gs::PlayItem*, size_t) { return false; }
bool play_file(const char*) { return false; }
bool play_chime(gs::Chime c) {
  // Designs with a passive buzzer still give audible feedback.
  switch (c) {
    case gs::Chime::Saved: gsboard::buzzer_tone(1319, 80); gsboard::buzzer_tone(1568, 120); return true;
    case gs::Chime::Error:
    case gs::Chime::Full: gsboard::buzzer_tone(440, 150); return true;
    case gs::Chime::None: return false;
    default: gsboard::buzzer_tone(1047, 80); return true;
  }
}
bool play_name_chime() { return false; }
bool play_tone(uint32_t freq_hz, uint32_t ms, uint8_t) {
  gsboard::buzzer_tone(freq_hz, ms);
  return true;
}
void stop() {}
bool busy() { return false; }
bool take_finished() { return false; }
uint8_t last_missing() { return 0; }
#endif

#if GS_HAS_MIC
// ------------------------------------------------------------------ microphone
static i2s_chan_handle_t s_rx = nullptr;
static TaskHandle_t s_mic_task = nullptr;
static volatile bool s_rec_active = false;
static volatile bool s_rec_stop = false;
static volatile bool s_rec_done = false;
static volatile bool s_rec_ok = false;
static volatile uint32_t s_rec_ms = 0;
static volatile bool s_monitor = false;
static volatile int16_t s_block_peak = 0;
static volatile bool s_block_ready = false;
static volatile bool s_rx_enabled = false;
#if GS_HAS_SD
static char s_rec_path[gs::kPathMax];
static uint32_t s_rec_max_bytes = 0;
#endif
static SemaphoreHandle_t s_rx_lock = nullptr;

static void rx_enable(bool on) {
  if (on == s_rx_enabled) return;
  if (on) {
    i2s_channel_enable(s_rx);
  } else {
    i2s_channel_disable(s_rx);
  }
  s_rx_enabled = on;
}

// Reads up to n samples, converted to 16-bit. Returns samples read.
static size_t rx_read(int16_t* out, size_t n, uint32_t timeout_ms) {
  static int32_t raw[256];
  if (n > 256) n = 256;
  size_t got = 0;
  if (i2s_channel_read(s_rx, raw, n * sizeof(int32_t), &got, timeout_ms) != ESP_OK) return 0;
  const size_t samples = got / sizeof(int32_t);
  for (size_t i = 0; i < samples; ++i) out[i] = gs::mic_to_pcm16(raw[i], GS_CFG_MIC_SHIFT);
  return samples;
}

static void discard_startup() {
  // INMP441 output is not valid for its start-up time after the clock starts; drop ~100 ms.
  int16_t tmp[256];
  uint32_t left = kMicRate / 10;
  while (left) {
    const size_t n = rx_read(tmp, left > 256 ? 256 : left, 100);
    if (!n) break;
    left -= n > left ? left : n;
  }
}

static void mic_task(void*) {
  int16_t pcm[160];  // 10 ms at 16 kHz
#if GS_HAS_SD
  File f;
  uint32_t bytes = 0;
#endif
  for (;;) {
    if (!s_rec_active && !s_monitor) {
      xSemaphoreTake(s_rx_lock, portMAX_DELAY);
      rx_enable(false);
      xSemaphoreGive(s_rx_lock);
      ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(1000));
      continue;
    }
    xSemaphoreTake(s_rx_lock, portMAX_DELAY);
    if (!s_rx_enabled) {
      rx_enable(true);
      discard_startup();
    }
#if GS_HAS_SD
    if (s_rec_active && !f) {
      bytes = 0;
      f = SD.open(s_rec_path, FILE_WRITE);
      if (f) {
        uint8_t hdr[gs::kWavHeaderBytes];
        gs::wav_header(hdr, kMicRate, 16, 1, 0);
        f.write(hdr, sizeof(hdr));
      } else {
        s_rec_ok = false;
        s_rec_ms = 0;
        s_rec_active = false;
        s_rec_done = true;
      }
    }
#endif
    const size_t n = rx_read(pcm, 160, 100);
    xSemaphoreGive(s_rx_lock);
    if (n && s_monitor) {
      s_block_peak = gs::peak_abs(pcm, n);
      s_block_ready = true;
    }
#if GS_HAS_SD
    if (s_rec_active && f) {
      if (n) {
        f.write(reinterpret_cast<const uint8_t*>(pcm), n * 2);
        bytes += uint32_t(n * 2);
      }
      if (s_rec_stop || bytes >= s_rec_max_bytes || !n) {
        uint8_t hdr[gs::kWavHeaderBytes];
        gs::wav_header(hdr, kMicRate, 16, 1, bytes);
        f.seek(0);
        f.write(hdr, sizeof(hdr));
        f.flush();
        f.close();
        s_rec_ms = bytes / 2 * 1000 / kMicRate;
        s_rec_ok = n > 0 || bytes > 0;
        s_rec_active = false;
        s_rec_stop = false;
        s_rec_done = true;
      }
    }
#endif
  }
}

bool mic_begin() {
  if (s_rx) return true;
  i2s_chan_config_t cc = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
  cc.dma_desc_num = 6;
  cc.dma_frame_num = 256;
  if (i2s_new_channel(&cc, nullptr, &s_rx) != ESP_OK) return false;
  i2s_std_config_t sc;
  memset(&sc, 0, sizeof(sc));
  sc.clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(kMicRate);
  sc.slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO);
  sc.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;  // INMP441 L/R pin tied low on the backplane
  sc.gpio_cfg.mclk = I2S_GPIO_UNUSED;
  sc.gpio_cfg.bclk = gpio_num_t(GS_PIN_I2S_MIC_SCK);
  sc.gpio_cfg.ws = gpio_num_t(GS_PIN_I2S_MIC_WS);
  sc.gpio_cfg.dout = I2S_GPIO_UNUSED;
  sc.gpio_cfg.din = gpio_num_t(GS_PIN_I2S_MIC_SD);
  if (i2s_channel_init_std_mode(s_rx, &sc) != ESP_OK) {
    i2s_del_channel(s_rx);
    s_rx = nullptr;
    return false;
  }
  s_rx_lock = xSemaphoreCreateMutex();
  xTaskCreatePinnedToCore(mic_task, "gs_mic", 4096, nullptr, 2, &s_mic_task, 1);
  return s_mic_task != nullptr;
}

bool mic_ready() { return s_rx != nullptr; }

bool record_start(const char* path, uint32_t max_ms) {
  if (!s_rx || s_rec_active) return false;
#if !GS_HAS_SD
  (void)path;
  (void)max_ms;
  return false;
#else
  if (!gsboard::sd_mounted()) return false;
  gs::copy_str(s_rec_path, sizeof(s_rec_path), path);
  s_rec_max_bytes = kMicRate * 2 * max_ms / 1000;
  s_rec_done = false;
  s_rec_ok = false;
  s_rec_stop = false;
  s_rec_active = true;
  xTaskNotifyGive(s_mic_task);
  return true;
#endif
}

void record_stop() {
  if (s_rec_active) s_rec_stop = true;
}

bool recording() { return s_rec_active; }

bool take_record_result(bool* ok, uint32_t* recorded_ms) {
  if (!s_rec_done) return false;
  s_rec_done = false;
  if (ok) *ok = s_rec_ok;
  if (recorded_ms) *recorded_ms = s_rec_ms;
  return true;
}

bool mic_measure(uint32_t ms, float* rms, int16_t* peak, bool* stuck) {
  if (!s_rx || s_rec_active) return false;
  xSemaphoreTake(s_rx_lock, portMAX_DELAY);
  const bool was_on = s_rx_enabled;
  if (!was_on) {
    rx_enable(true);
    discard_startup();
  }
  const size_t total = kMicRate * ms / 1000;
  static int16_t buf[kMicRate / 2];
  const size_t cap = total > sizeof(buf) / 2 ? sizeof(buf) / 2 : total;
  size_t have = 0;
  while (have < cap) {
    const size_t n = rx_read(buf + have, (cap - have) > 256 ? 256 : cap - have, 100);
    if (!n) break;
    have += n;
  }
  if (!was_on && !s_monitor) rx_enable(false);
  xSemaphoreGive(s_rx_lock);
  bool all_same = have > 0;
  for (size_t i = 1; i < have && all_same; ++i) all_same = buf[i] == buf[0];
  if (rms) *rms = gs::rms_dbfs(buf, have);
  if (peak) *peak = gs::peak_abs(buf, have);
  if (stuck) *stuck = all_same;
  return have == cap;
}

void mic_monitor(bool on) {
  s_monitor = on && s_rx;
  if (s_monitor) xTaskNotifyGive(s_mic_task);
}

bool mic_monitoring() { return s_monitor; }

bool mic_take_block_peak(int16_t* peak) {
  if (!s_block_ready) return false;
  s_block_ready = false;
  *peak = s_block_peak;
  return true;
}

#else  // no microphone in this design
bool mic_begin() { return false; }
bool mic_ready() { return false; }
bool record_start(const char*, uint32_t) { return false; }
void record_stop() {}
bool recording() { return false; }
bool take_record_result(bool*, uint32_t*) { return false; }
bool mic_measure(uint32_t, float*, int16_t*, bool*) { return false; }
void mic_monitor(bool) {}
bool mic_monitoring() { return false; }
bool mic_take_block_peak(int16_t*) { return false; }
#endif

}  // namespace gsaudio
