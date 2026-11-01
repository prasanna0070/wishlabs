// Board bring-up for the Gadget Studio backplane: buttons, I2C, microSD, buzzer, servo.
// Every pin comes from include/hardware.h, which binds roles to include/pins.h (generated from
// electrical.json). No GPIO number is written by hand in this firmware.
#pragma once
#include <Arduino.h>

#include "config.h"
#include "gs_button.h"
#include "hardware.h"

namespace gsboard {

constexpr uint8_t kMaxButtons = 4;

bool begin();  // safe to call once from setup()

// Buttons (debounced, tap / long press).
uint8_t button_count();
const char* button_id(uint8_t index);
bool button_raw(uint8_t index);  // polarity-corrected raw level
gs::ButtonEvent button_update(uint8_t index, uint32_t now);
bool any_button_held_at_boot();
void set_long_press_ms(uint16_t ms);

// microSD (shared SPI bus).
bool sd_mounted();
bool sd_remount();
uint64_t sd_total_bytes();
uint64_t sd_used_bytes();
bool sd_exists(const char* path);
bool sd_remove(const char* path);
bool sd_rename(const char* from, const char* to);
bool sd_mkdirs(const char* dir);
// Overwrites the file with zeros, then deletes it (MAJ-1/MAJ-17). True when the file is gone or was
// never there. SD cards remap blocks internally, so the overwrite is best effort; clips are also
// encrypted with a key that "erase all" destroys.
bool sd_wipe(const char* path);
// Calls fn for every plain file directly inside dir (names only, no cap). False if dir is missing.
typedef void (*SdListFn)(const char* name, void* ctx);
bool sd_list(const char* dir, SdListFn fn, void* ctx);

// I2C bus.
bool i2c_ok();
bool i2c_probe(uint8_t addr);
int i2c_scan(uint8_t* found, int cap);

// Passive buzzer (LEDC) and servo (50 Hz LEDC).
void buzzer_tone(uint32_t freq_hz, uint32_t ms);
void buzzer_off();
void servo_write(uint8_t degrees);  // 0..180
void servo_detach();

// Analog light sensor (LDR divider) 0..4095, -1 when absent.
int ldr_read();

}  // namespace gsboard
