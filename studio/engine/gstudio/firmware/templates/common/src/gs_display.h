// One small drawing API over the library's displays:
//   SSD1306 / SH1106 OLED on I2C (U8g2, monochrome) and ST7789 TFT on SPI (LovyanGFX, colour).
// Draw into the frame buffer, then flush().
#pragma once
#include <Arduino.h>

#include "gs_templates.h"

// Display kinds (hardware.h sets GS_DISPLAY_KIND to one of these; the generator uses the same numbers).
#define GS_DISP_NONE 0
#define GS_DISP_SSD1306_128X64 1
#define GS_DISP_SSD1306_128X32 2
#define GS_DISP_SH1106_128X64 3
#define GS_DISP_ST7789 4

namespace gsdisp {

bool begin();
bool present();
int width();
int height();
bool is_color();
uint16_t rgb(uint8_t r, uint8_t g, uint8_t b);  // RGB565 (mono displays treat non-zero as on)
constexpr uint16_t kBlack = 0;
constexpr uint16_t kWhite = 0xFFFF;

void clear();
void fill_rect(int x, int y, int w, int h, uint16_t c);
void fill_round_rect(int x, int y, int w, int h, int r, uint16_t c);
void fill_circle(int x, int y, int r, uint16_t c);
void fill_triangle(int x0, int y0, int x1, int y1, int x2, int y2, uint16_t c);
void line(int x0, int y0, int x1, int y1, uint16_t c);
// size: 1 small (about 8 px), 2 medium (about 14 px), 3 large clock digits (about 24 px).
void text(int x, int y, const char* s, uint8_t size, uint16_t c);
int text_width(const char* s, uint8_t size);
void flush();
void set_brightness(uint8_t level);  // 0..255: OLED contrast or TFT backlight
void power(bool on);

// The shared face: two eyes that blink, smile, droop when sleepy and widen while listening.
void draw_face(gs::Expression e, uint32_t now, uint16_t color);

}  // namespace gsdisp
