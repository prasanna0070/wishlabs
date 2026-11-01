// I2C sensors from the module library. Only the drivers for sensors present in this design are
// compiled (hardware.h sets GS_HAS_<SENSOR>).
#pragma once
#include <Arduino.h>
#include <time.h>

namespace gssensors {

struct Reading {
  const char* sensor = "";    // e.g. "aht20"
  const char* quantity = "";  // temperature | humidity | pressure | light | distance
  const char* unit = "";      // C | % | hPa | lx | mm
  float value = 0;
  bool ok = false;
};

bool begin();
int sensor_count();
// Reads every quantity now. Returns the number written.
int read_all(Reading* out, int cap);
bool temperature_c(float* v);
bool humidity_pct(float* v);
bool pressure_hpa(float* v);
bool light_lux(float* v);
bool distance_mm(float* v);
bool has_rtc();
bool rtc_now(time_t* t);
bool rtc_set(time_t t);

}  // namespace gssensors
