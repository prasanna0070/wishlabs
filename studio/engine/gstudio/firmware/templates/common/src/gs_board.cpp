#include "gs_board.h"

#include <FS.h>

#include "gs_fsfile.h"
#include <SPI.h>
#include <Wire.h>
#if GS_HAS_SD
#include <SD.h>
#endif

namespace gsboard {

#if GS_HAS_BUTTON
static const int kButtonPins[] = GS_BUTTON_PINS;
static const uint8_t kButtonActiveLow[] = GS_BUTTON_ACTIVE_LOW;
static const char* const kButtonIds[] = GS_BUTTON_IDS;
static constexpr uint8_t kButtons = sizeof(kButtonPins) / sizeof(kButtonPins[0]) > kMaxButtons
                                        ? kMaxButtons
                                        : uint8_t(sizeof(kButtonPins) / sizeof(kButtonPins[0]));
static gs::ButtonFsm s_buttons[kMaxButtons];
#else
static constexpr uint8_t kButtons = 0;
#endif

static bool s_i2c = false;
static bool s_sd = false;
static bool s_boot_held = false;

// The microSD and an SPI display share the backplane's one SPI bus through the global SPI object
// (FSPI on the ESP32-S3), so both drivers see the same bus instance.

uint8_t button_count() { return kButtons; }

const char* button_id(uint8_t index) {
#if GS_HAS_BUTTON
  return index < kButtons ? kButtonIds[index] : "";
#else
  (void)index;
  return "";
#endif
}

bool button_raw(uint8_t index) {
#if GS_HAS_BUTTON
  if (index >= kButtons) return false;
  const int level = digitalRead(kButtonPins[index]);
  return kButtonActiveLow[index] ? level == LOW : level == HIGH;
#else
  (void)index;
  return false;
#endif
}

gs::ButtonEvent button_update(uint8_t index, uint32_t now) {
#if GS_HAS_BUTTON
  if (index >= kButtons) return gs::ButtonEvent::None;
  return s_buttons[index].update(now, button_raw(index));
#else
  (void)index;
  (void)now;
  return gs::ButtonEvent::None;
#endif
}

void set_long_press_ms(uint16_t ms) {
#if GS_HAS_BUTTON
  for (uint8_t i = 0; i < kButtons; ++i) {
    gs::ButtonConfig c = s_buttons[i].config();
    c.long_press_ms = ms;
    s_buttons[i].set_config(c);
  }
#else
  (void)ms;
#endif
}

bool any_button_held_at_boot() { return s_boot_held; }

static bool mount_sd() {
#if GS_HAS_SD
  SD.end();
  // 20 MHz is conservative for 150 mm harness runs; the SD library retries at its own pace.
  s_sd = SD.begin(GS_PIN_SD_CS, SPI, 20000000, "/sd", 8, false);
  if (s_sd && SD.cardType() == CARD_NONE) {
    SD.end();
    s_sd = false;
  }
  return s_sd;
#else
  return false;
#endif
}

bool begin() {
#if GS_HAS_BUTTON
  gs::ButtonConfig cfg;
  cfg.debounce_ms = 30;
  cfg.long_press_ms = GS_CFG_LONG_PRESS_MS;
  for (uint8_t i = 0; i < kButtons; ++i) {
    pinMode(kButtonPins[i], kButtonActiveLow[i] ? INPUT_PULLUP : INPUT_PULLDOWN);
    s_buttons[i].set_config(cfg);
  }
  delay(5);
  for (uint8_t i = 0; i < kButtons; ++i) {
    s_boot_held = s_boot_held || button_raw(i);
    s_buttons[i].update(millis(), button_raw(i));
  }
#endif
#if GS_HAS_I2C
  s_i2c = Wire.begin(GS_PIN_I2C_SDA, GS_PIN_I2C_SCL, 400000);
  Wire.setTimeOut(50);
#endif
#if GS_HAS_SPI
  SPI.begin(GS_PIN_SPI_SCK, GS_PIN_SPI_MISO, GS_PIN_SPI_MOSI, -1);
#endif
#if GS_HAS_SD
  pinMode(GS_PIN_SD_CS, OUTPUT);
  digitalWrite(GS_PIN_SD_CS, HIGH);
  mount_sd();
#endif
#if GS_HAS_BUZZER
  ledcAttach(GS_PIN_BUZZER, 2000, 10);
#if GS_BUZZER_ACTIVE_LOW
  // The library's buzzer module is low-level triggered: invert so "off" idles the pin HIGH.
  ledcOutputInvert(GS_PIN_BUZZER, true);
#endif
  ledcWrite(GS_PIN_BUZZER, 0);
#endif
#if GS_HAS_LDR
  pinMode(GS_PIN_LDR, INPUT);
  analogReadResolution(12);
#endif
  return true;
}

bool sd_mounted() { return s_sd; }

bool sd_remount() { return mount_sd(); }

uint64_t sd_total_bytes() {
#if GS_HAS_SD
  return s_sd ? SD.totalBytes() : 0;
#else
  return 0;
#endif
}

uint64_t sd_used_bytes() {
#if GS_HAS_SD
  return s_sd ? SD.usedBytes() : 0;
#else
  return 0;
#endif
}

bool sd_exists(const char* path) {
#if GS_HAS_SD
  return s_sd && SD.exists(path);
#else
  (void)path;
  return false;
#endif
}

bool sd_remove(const char* path) {
#if GS_HAS_SD
  if (!s_sd) return false;
  if (!SD.exists(path)) return true;
  return SD.remove(path);
#else
  (void)path;
  return false;
#endif
}

bool sd_rename(const char* from, const char* to) {
#if GS_HAS_SD
  if (!s_sd) return false;
  if (SD.exists(to)) SD.remove(to);
  return SD.rename(from, to);
#else
  (void)from;
  (void)to;
  return false;
#endif
}

bool sd_mkdirs(const char* dir) {
#if GS_HAS_SD
  if (!s_sd) return false;
  if (SD.exists(dir)) return true;
  return SD.mkdir(dir);
#else
  (void)dir;
  return false;
#endif
}

bool sd_wipe(const char* path) {
#if GS_HAS_SD
  if (!s_sd) return false;
  if (!SD.exists(path)) return true;
  File f = SD.open(path, "r+");
  if (f) {
    GsFsFile ff(f);
    gs::zero_fill(&ff);
    f.close();
  }
  return SD.remove(path);
#else
  (void)path;
  return false;
#endif
}

bool sd_list(const char* dir, SdListFn fn, void* ctx) {
#if GS_HAS_SD
  if (!s_sd) return false;
  File d = SD.open(dir);
  if (!d || !d.isDirectory()) return false;
  for (File f = d.openNextFile(); f; f = d.openNextFile()) {
    if (!f.isDirectory()) fn(f.name(), ctx);
    f.close();
  }
  d.close();
  return true;
#else
  (void)dir;
  (void)fn;
  (void)ctx;
  return false;
#endif
}

bool i2c_ok() { return s_i2c; }

bool i2c_probe(uint8_t addr) {
#if GS_HAS_I2C
  if (!s_i2c) return false;
  Wire.beginTransmission(addr);
  return Wire.endTransmission() == 0;
#else
  (void)addr;
  return false;
#endif
}

int i2c_scan(uint8_t* found, int cap) {
  int n = 0;
  for (uint8_t a = 0x08; a < 0x78 && n < cap; ++a)
    if (i2c_probe(a)) found[n++] = a;
  return n;
}

void buzzer_tone(uint32_t freq_hz, uint32_t ms) {
#if GS_HAS_BUZZER
  ledcWriteTone(GS_PIN_BUZZER, freq_hz);
  delay(ms);
  ledcWrite(GS_PIN_BUZZER, 0);
#else
  (void)freq_hz;
  (void)ms;
#endif
}

void buzzer_off() {
#if GS_HAS_BUZZER
  ledcWrite(GS_PIN_BUZZER, 0);
#endif
}

#if GS_HAS_SERVO
static bool s_servo_attached = false;
#endif

void servo_write(uint8_t degrees) {
#if GS_HAS_SERVO
  if (!s_servo_attached) {
    ledcAttach(GS_PIN_SERVO, 50, 14);
    s_servo_attached = true;
  }
  if (degrees > 180) degrees = 180;
  // SG90 nominal pulse 0.5 ms (0 deg) to 2.4 ms (180 deg) in a 20 ms frame.
  const uint32_t us = 500 + (uint32_t(degrees) * 1900u) / 180u;
  ledcWrite(GS_PIN_SERVO, (us * 16383u) / 20000u);
#else
  (void)degrees;
#endif
}

void servo_detach() {
#if GS_HAS_SERVO
  if (s_servo_attached) {
    ledcDetach(GS_PIN_SERVO);
    s_servo_attached = false;
  }
#endif
}

int ldr_read() {
#if GS_HAS_LDR
  return analogRead(GS_PIN_LDR);
#else
  return -1;
#endif
}

}  // namespace gsboard
