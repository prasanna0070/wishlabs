// Native-test shim of the ESP-IDF 5 I2S standard-mode driver subset used by src/gs_audio.cpp.
// The speaker channel appends every written sample to gs_host::i2s.spk; the microphone channel reads
// raw 32-bit INMP441 slots from gs_host::i2s.mic.
#pragma once
#include "Arduino.h"

typedef int gpio_num_t;
#define I2S_GPIO_UNUSED (-1)
typedef enum { I2S_NUM_0 = 0, I2S_NUM_1 = 1 } i2s_port_t;
typedef enum { I2S_ROLE_MASTER = 0, I2S_ROLE_SLAVE = 1 } i2s_role_t;
typedef enum { I2S_DATA_BIT_WIDTH_16BIT = 16, I2S_DATA_BIT_WIDTH_32BIT = 32 } i2s_data_bit_width_t;
typedef enum { I2S_SLOT_MODE_MONO = 1, I2S_SLOT_MODE_STEREO = 2 } i2s_slot_mode_t;
typedef enum { I2S_STD_SLOT_LEFT = 1, I2S_STD_SLOT_RIGHT = 2, I2S_STD_SLOT_BOTH = 3 } i2s_std_slot_mask_t;

struct GsHostI2sChan {
  bool rx = false;
  bool enabled = false;
};
typedef GsHostI2sChan* i2s_chan_handle_t;

typedef struct {
  i2s_port_t id;
  i2s_role_t role;
  uint32_t dma_desc_num;
  uint32_t dma_frame_num;
  bool auto_clear;
} i2s_chan_config_t;
typedef struct {
  uint32_t sample_rate_hz;
} i2s_std_clk_config_t;
typedef struct {
  i2s_data_bit_width_t data_bit_width;
  i2s_slot_mode_t slot_mode;
  i2s_std_slot_mask_t slot_mask;
} i2s_std_slot_config_t;
typedef struct {
  gpio_num_t mclk, bclk, ws, dout, din;
} i2s_std_gpio_config_t;
typedef struct {
  i2s_std_clk_config_t clk_cfg;
  i2s_std_slot_config_t slot_cfg;
  i2s_std_gpio_config_t gpio_cfg;
} i2s_std_config_t;

#define I2S_CHANNEL_DEFAULT_CONFIG(port, role) \
  i2s_chan_config_t { (port), (role), 6, 240, false }
#define I2S_STD_CLK_DEFAULT_CONFIG(rate) \
  i2s_std_clk_config_t { (rate) }
#define I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(bits, mode) \
  i2s_std_slot_config_t { (bits), (mode), I2S_STD_SLOT_BOTH }

inline esp_err_t i2s_new_channel(const i2s_chan_config_t*, i2s_chan_handle_t* tx, i2s_chan_handle_t* rx) {
  if (tx) *tx = new GsHostI2sChan{false, false};
  if (rx) *rx = new GsHostI2sChan{true, false};
  return ESP_OK;
}
inline esp_err_t i2s_channel_init_std_mode(i2s_chan_handle_t h, const i2s_std_config_t* c) {
  if (h && !h->rx) gs_host::i2s.tx_rate = c->clk_cfg.sample_rate_hz;
  return ESP_OK;
}
inline esp_err_t i2s_channel_reconfig_std_clock(i2s_chan_handle_t h, const i2s_std_clk_config_t* c) {
  if (h && !h->rx) gs_host::i2s.tx_rate = c->sample_rate_hz;
  return ESP_OK;
}
inline esp_err_t i2s_channel_enable(i2s_chan_handle_t h) {
  if (!h) return ESP_FAIL;
  h->enabled = true;
  if (h->rx) {
    gs_host::i2s.rx_enabled = true;
    ++gs_host::i2s.rx_enables;
    if (gs_host::i2s.on_rx) gs_host::i2s.on_rx(true);
  } else {
    gs_host::i2s.tx_enabled = true;
  }
  return ESP_OK;
}
inline esp_err_t i2s_channel_disable(i2s_chan_handle_t h) {
  if (!h) return ESP_FAIL;
  h->enabled = false;
  if (h->rx) {
    gs_host::i2s.rx_enabled = false;
    if (gs_host::i2s.on_rx) gs_host::i2s.on_rx(false);
  } else {
    gs_host::i2s.tx_enabled = false;
  }
  return ESP_OK;
}
inline esp_err_t i2s_del_channel(i2s_chan_handle_t h) {
  delete h;
  return ESP_OK;
}
inline esp_err_t i2s_channel_write(i2s_chan_handle_t h, const void* data, size_t bytes, size_t* written, uint32_t) {
  if (!h || h->rx || !h->enabled) return ESP_FAIL;
  const int16_t* p = static_cast<const int16_t*>(data);
  gs_host::i2s.spk.insert(gs_host::i2s.spk.end(), p, p + bytes / 2);
  if (written) *written = bytes;
  return ESP_OK;
}
inline esp_err_t i2s_channel_read(i2s_chan_handle_t h, void* buf, size_t bytes, size_t* got, uint32_t timeout_ms) {
  if (got) *got = 0;
  if (!h || !h->rx || !h->enabled) return ESP_FAIL;
  int32_t* out = static_cast<int32_t*>(buf);
  size_t n = 0;
  while (n < bytes / 4 && gs_host::i2s.mic && gs_host::i2s.mic(out + n)) ++n;
  if (got) *got = n * 4;
  gs_host::now_ms += uint32_t(n / 16);  // 16 kHz: the clock moves with the audio
  if (n == 0) {
    gs_host::now_ms += timeout_ms;
    return ESP_ERR_TIMEOUT;
  }
  return ESP_OK;
}
