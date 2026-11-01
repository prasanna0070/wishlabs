#include "gs_display.h"

#include "config.h"
#include "hardware.h"

#if GS_DISPLAY_KIND == GS_DISP_SSD1306_128X64 || GS_DISPLAY_KIND == GS_DISP_SSD1306_128X32 || \
    GS_DISPLAY_KIND == GS_DISP_SH1106_128X64
#define GS_DISP_U8G2 1
#include <U8g2lib.h>
#include <Wire.h>
#elif GS_DISPLAY_KIND == GS_DISP_ST7789
#define GS_DISP_LGFX 1
#include <SPI.h>
#define LGFX_USE_V1
#include <LovyanGFX.hpp>
#endif

namespace gsdisp {

static bool s_ok = false;

#if defined(GS_DISP_U8G2)
#if GS_DISPLAY_KIND == GS_DISP_SSD1306_128X64
static U8G2_SSD1306_128X64_NONAME_F_HW_I2C s_u8(U8G2_R0, U8X8_PIN_NONE, GS_PIN_I2C_SCL, GS_PIN_I2C_SDA);
#elif GS_DISPLAY_KIND == GS_DISP_SSD1306_128X32
static U8G2_SSD1306_128X32_UNIVISION_F_HW_I2C s_u8(U8G2_R0, U8X8_PIN_NONE, GS_PIN_I2C_SCL, GS_PIN_I2C_SDA);
#else
static U8G2_SH1106_128X64_NONAME_F_HW_I2C s_u8(U8G2_R0, U8X8_PIN_NONE, GS_PIN_I2C_SCL, GS_PIN_I2C_SDA);
#endif

static void color(uint16_t c) { s_u8.setDrawColor(c ? 1 : 0); }

bool begin() {
  s_u8.setI2CAddress(GS_DISP_I2C_ADDR * 2);
  s_ok = s_u8.begin();
  if (s_ok) {
    s_u8.setBusClock(400000);
    s_u8.clearBuffer();
    s_u8.sendBuffer();
  }
  return s_ok;
}
int width() { return s_u8.getDisplayWidth(); }
int height() { return s_u8.getDisplayHeight(); }
bool is_color() { return false; }
void clear() { s_u8.clearBuffer(); }
void fill_rect(int x, int y, int w, int h, uint16_t c) {
  if (w <= 0 || h <= 0) return;
  color(c);
  s_u8.drawBox(x, y, w, h);
}
void fill_round_rect(int x, int y, int w, int h, int r, uint16_t c) {
  if (w <= 0 || h <= 0) return;
  color(c);
  if (r * 2 + 1 >= w) r = (w - 2) / 2;
  if (r * 2 + 1 >= h) r = (h - 2) / 2;
  if (r < 1) {
    s_u8.drawBox(x, y, w, h);
  } else {
    s_u8.drawRBox(x, y, w, h, r);
  }
}
void fill_circle(int x, int y, int r, uint16_t c) {
  color(c);
  s_u8.drawDisc(x, y, r);
}
void fill_triangle(int x0, int y0, int x1, int y1, int x2, int y2, uint16_t c) {
  color(c);
  s_u8.drawTriangle(x0, y0, x1, y1, x2, y2);
}
void line(int x0, int y0, int x1, int y1, uint16_t c) {
  color(c);
  s_u8.drawLine(x0, y0, x1, y1);
}
static void font(uint8_t size) {
  if (size >= 3) {
    s_u8.setFont(u8g2_font_logisoso24_tr);
  } else if (size == 2) {
    s_u8.setFont(u8g2_font_helvB12_tr);
  } else {
    s_u8.setFont(u8g2_font_6x10_tr);
  }
}
void text(int x, int y, const char* s, uint8_t size, uint16_t c) {
  color(c);
  font(size);
  s_u8.setFontPosTop();
  s_u8.drawStr(x, y, s);
}
int text_width(const char* s, uint8_t size) {
  font(size);
  return s_u8.getStrWidth(s);
}
void flush() { s_u8.sendBuffer(); }
void set_brightness(uint8_t level) { s_u8.setContrast(level); }
void power(bool on) { s_u8.setPowerSave(on ? 0 : 1); }

#elif defined(GS_DISP_LGFX)
class LgfxGs : public lgfx::LGFX_Device {
 public:
  LgfxGs() {
    {
      auto cfg = bus_.config();
      cfg.spi_host = SPI2_HOST;  // the backplane's shared SPI bus (also the microSD)
      cfg.spi_mode = 0;
      cfg.freq_write = 40000000;
      cfg.freq_read = 16000000;
      cfg.spi_3wire = true;
      cfg.use_lock = true;
      cfg.dma_channel = SPI_DMA_CH_AUTO;
      cfg.pin_sclk = GS_PIN_SPI_SCK;
      cfg.pin_mosi = GS_PIN_SPI_MOSI;
      cfg.pin_miso = GS_PIN_SPI_MISO;
      cfg.pin_dc = GS_PIN_TFT_DC;
      bus_.config(cfg);
      panel_.setBus(&bus_);
    }
    {
      auto cfg = panel_.config();
      cfg.pin_cs = GS_PIN_TFT_CS;
      cfg.pin_rst = GS_PIN_TFT_RST;
      cfg.pin_busy = -1;
      cfg.panel_width = GS_DISP_WIDTH;
      cfg.panel_height = GS_DISP_HEIGHT;
      cfg.memory_width = 240;
      cfg.memory_height = 320;
      cfg.offset_x = GS_TFT_OFFSET_X;
      cfg.offset_y = GS_TFT_OFFSET_Y;
      cfg.offset_rotation = 0;
      cfg.readable = false;
      cfg.invert = true;
      cfg.rgb_order = false;
      cfg.dlen_16bit = false;
      cfg.bus_shared = GS_HAS_SD ? true : false;
      panel_.config(cfg);
    }
#if GS_PIN_TFT_BL >= 0
    {
      auto cfg = light_.config();
      cfg.pin_bl = GS_PIN_TFT_BL;
      cfg.invert = false;
      cfg.freq = 12000;
      cfg.pwm_channel = 7;
      light_.config(cfg);
      panel_.setLight(&light_);
    }
#endif
    setPanel(&panel_);
  }

 private:
  lgfx::Panel_ST7789 panel_;
  lgfx::Bus_SPI bus_;
  lgfx::Light_PWM light_;
};

static LgfxGs s_tft;
static LGFX_Sprite s_canvas(&s_tft);

bool begin() {
  s_ok = s_tft.init();
  if (!s_ok) return false;
  s_tft.setRotation(GS_TFT_ROTATION);
  s_tft.setBrightness(160);
  s_canvas.setColorDepth(16);
  s_canvas.setPsram(true);
  s_ok = s_canvas.createSprite(s_tft.width(), s_tft.height()) != nullptr;
  s_canvas.fillScreen(TFT_BLACK);
  s_canvas.pushSprite(0, 0);
  return s_ok;
}
int width() { return s_tft.width(); }
int height() { return s_tft.height(); }
bool is_color() { return true; }
void clear() { s_canvas.fillScreen(TFT_BLACK); }
void fill_rect(int x, int y, int w, int h, uint16_t c) { s_canvas.fillRect(x, y, w, h, c); }
void fill_round_rect(int x, int y, int w, int h, int r, uint16_t c) { s_canvas.fillRoundRect(x, y, w, h, r, c); }
void fill_circle(int x, int y, int r, uint16_t c) { s_canvas.fillCircle(x, y, r, c); }
void fill_triangle(int x0, int y0, int x1, int y1, int x2, int y2, uint16_t c) {
  s_canvas.fillTriangle(x0, y0, x1, y1, x2, y2, c);
}
void line(int x0, int y0, int x1, int y1, uint16_t c) { s_canvas.drawLine(x0, y0, x1, y1, c); }
static void font(uint8_t size) {
  if (size >= 3) {
    s_canvas.setFont(&fonts::FreeSansBold24pt7b);
  } else if (size == 2) {
    s_canvas.setFont(&fonts::FreeSansBold12pt7b);
  } else {
    s_canvas.setFont(&fonts::FreeSans9pt7b);
  }
}
void text(int x, int y, const char* s, uint8_t size, uint16_t c) {
  font(size);
  s_canvas.setTextColor(c);
  s_canvas.setTextDatum(lgfx::top_left);
  s_canvas.drawString(s, x, y);
}
int text_width(const char* s, uint8_t size) {
  font(size);
  return s_canvas.textWidth(s);
}
void flush() { s_canvas.pushSprite(0, 0); }
void set_brightness(uint8_t level) { s_tft.setBrightness(level); }
void power(bool on) {
  if (on) {
    s_tft.wakeup();
  } else {
    s_tft.sleep();
  }
}

#else  // no display in this design
bool begin() { return false; }
int width() { return 0; }
int height() { return 0; }
bool is_color() { return false; }
void clear() {}
void fill_rect(int, int, int, int, uint16_t) {}
void fill_round_rect(int, int, int, int, int, uint16_t) {}
void fill_circle(int, int, int, uint16_t) {}
void fill_triangle(int, int, int, int, int, int, uint16_t) {}
void line(int, int, int, int, uint16_t) {}
void text(int, int, const char*, uint8_t, uint16_t) {}
int text_width(const char*, uint8_t) { return 0; }
void flush() {}
void set_brightness(uint8_t) {}
void power(bool) {}
#endif

bool present() { return s_ok; }

uint16_t rgb(uint8_t r, uint8_t g, uint8_t b) {
  return uint16_t(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3));
}

void draw_face(gs::Expression e, uint32_t now, uint16_t c) {
  if (!s_ok) return;
  const int w = width();
  const int h = height();
  const int ew = w / 5;          // eye width
  const int eh = h * 11 / 20;    // eye height
  const int gap = w / 6;
  const int cx = w / 2;
  const int cy = h / 2;
  const int lx = cx - gap / 2 - ew;
  const int rx = cx + gap / 2;
  const int top = cy - eh / 2;
  clear();
  switch (e) {
    case gs::Expression::Blink:
      fill_round_rect(lx, cy - 2, ew, 4, 2, c);
      fill_round_rect(rx, cy - 2, ew, 4, 2, c);
      break;
    case gs::Expression::Happy: {
      // Upturned crescents: a disc with its lower part masked off.
      const int r = ew / 2 + 2;
      fill_circle(lx + ew / 2, cy + r / 3, r, c);
      fill_circle(rx + ew / 2, cy + r / 3, r, c);
      fill_circle(lx + ew / 2, cy + r / 3 + r / 2, r, kBlack);
      fill_circle(rx + ew / 2, cy + r / 3 + r / 2, r, kBlack);
      break;
    }
    case gs::Expression::Sleepy:
      fill_round_rect(lx, cy, ew, eh / 3, 3, c);
      fill_round_rect(rx, cy, ew, eh / 3, 3, c);
      break;
    case gs::Expression::Surprised: {
      const int r = ew / 2 + 3;
      fill_circle(lx + ew / 2, cy, r, c);
      fill_circle(rx + ew / 2, cy, r, c);
      fill_circle(lx + ew / 2, cy, r / 3, kBlack);
      fill_circle(rx + ew / 2, cy, r / 3, kBlack);
      break;
    }
    case gs::Expression::Listening: {
      fill_round_rect(lx, top - 2, ew, eh + 4, ew / 3, c);
      fill_round_rect(rx, top - 2, ew, eh + 4, ew / 3, c);
      // Pulsing "listening" dots beside the eyes.
      const int phase = int((now / 250) % 3);
      for (int i = 0; i <= phase; ++i) {
        fill_circle(lx - 6 - i * 5, cy, 2, c);
        fill_circle(rx + ew + 6 + i * 5, cy, 2, c);
      }
      break;
    }
    case gs::Expression::Neutral:
    default:
      fill_round_rect(lx, top, ew, eh, ew / 3, c);
      fill_round_rect(rx, top, ew, eh, ew / 3, c);
      break;
  }
  flush();
}

}  // namespace gsdisp
