#include "gs_sensors.h"

#include <Wire.h>

#include "config.h"
#include "hardware.h"

#ifndef GS_HAS_SOIL
#define GS_HAS_SOIL 0
#endif
#ifndef GS_HAS_LEVEL
#define GS_HAS_LEVEL 0
#endif
#if GS_HAS_SOIL
#include "gs_soil.h"
// Only sensor_display carries the calibration settings; another app reading a probe uses the defaults.
#ifndef GS_CFG_SOIL_RAW_DRY
#define GS_CFG_SOIL_RAW_DRY 3000
#endif
#ifndef GS_CFG_SOIL_RAW_WET
#define GS_CFG_SOIL_RAW_WET 1300
#endif
#endif

#if GS_HAS_BMP280
#include <Adafruit_BMP280.h>
#endif
#if GS_HAS_AHT20
#include <Adafruit_AHTX0.h>
#endif
#if GS_HAS_VL53L0X
#include <VL53L0X.h>
#endif
#if GS_HAS_BH1750
#include <BH1750.h>
#endif
#if GS_HAS_DS3231
#include <RTClib.h>
#endif

namespace gssensors {

#if GS_HAS_BMP280
static Adafruit_BMP280 s_bmp(&Wire);
static bool s_bmp_ok = false;
#endif
#if GS_HAS_AHT20
static Adafruit_AHTX0 s_aht;
static bool s_aht_ok = false;
#endif
#if GS_HAS_VL53L0X
static VL53L0X s_tof;
static bool s_tof_ok = false;
#endif
#if GS_HAS_BH1750
static BH1750 s_bh(GS_BH1750_ADDR);
static bool s_bh_ok = false;
#endif
#if GS_HAS_DS3231
static RTC_DS3231 s_rtc;
static bool s_rtc_ok = false;
#endif

bool begin() {
  bool any = false;
#if GS_HAS_BMP280
  s_bmp_ok = s_bmp.begin(GS_BMP280_ADDR);
  any = any || s_bmp_ok;
#endif
#if GS_HAS_AHT20
  s_aht_ok = s_aht.begin(&Wire);
  any = any || s_aht_ok;
#endif
#if GS_HAS_VL53L0X
  s_tof.setBus(&Wire);
  s_tof.setTimeout(200);
  s_tof_ok = s_tof.init();
  if (s_tof_ok) s_tof.startContinuous(100);
  any = any || s_tof_ok;
#endif
#if GS_HAS_BH1750
  s_bh_ok = s_bh.begin(BH1750::CONTINUOUS_HIGH_RES_MODE, GS_BH1750_ADDR, &Wire);
  any = any || s_bh_ok;
#endif
#if GS_HAS_DS3231
  s_rtc_ok = s_rtc.begin(&Wire);
  any = any || s_rtc_ok;
#endif
#if GS_HAS_SOIL
  // Capacitive soil probe on a J_IO SIG: ADC1 (GPIO4/5/6), so it reads with Wi-Fi on. 11 dB = about 0-3.1 V.
  pinMode(GS_PIN_SOIL, INPUT);
  analogReadResolution(12);
  analogSetPinAttenuation(GS_PIN_SOIL, ADC_11db);
  any = true;
#endif
#if GS_HAS_LEVEL
  // Float switch: a dry contact between SIG and GND. Closed (LOW) = water up; open (HIGH) = tank low, which is
  // also what an unplugged lead reads, so a fault warns instead of staying quiet.
  pinMode(GS_PIN_LEVEL, INPUT_PULLUP);
  any = true;
#endif
  return any;
}

int sensor_count() {
  return GS_HAS_BMP280 + GS_HAS_AHT20 + GS_HAS_BH1750 + GS_HAS_DS3231 + GS_HAS_VL53L0X + GS_HAS_SOIL + GS_HAS_LEVEL;
}

__attribute__((unused)) static void put(Reading* out, int cap, int* n, const char* sensor, const char* q, const char* unit, float v, bool ok,
                                        int raw = -1) {
  if (*n >= cap) return;
  Reading& r = out[(*n)++];
  r.sensor = sensor;
  r.quantity = q;
  r.unit = unit;
  r.value = v;
  r.ok = ok && !isnan(v);
  r.raw = raw;
}

#if GS_HAS_SOIL
static int soil_raw() {
  int v[16];
  for (int i = 0; i < 16; ++i) v[i] = analogRead(GS_PIN_SOIL);
  return gs::mean_of(v, 16);
}
#endif

#if GS_HAS_LEVEL
// Majority of 7 reads 1 ms apart: rides over contact bounce. Sloshing is filtered by the app over samples.
static bool water_up() {
  int closed = 0;
  for (int i = 0; i < 7; ++i) {
    closed += digitalRead(GS_PIN_LEVEL) == LOW ? 1 : 0;
    if (i < 6) delay(1);
  }
  return closed >= 4;
}
#endif

int read_all(Reading* out, int cap) {
  int n = 0;
  (void)out;
  (void)cap;
#if GS_HAS_BMP280
  put(out, cap, &n, "bmp280", "temperature", "C", s_bmp_ok ? s_bmp.readTemperature() : NAN, s_bmp_ok);
  put(out, cap, &n, "bmp280", "pressure", "hPa", s_bmp_ok ? s_bmp.readPressure() / 100.0f : NAN, s_bmp_ok);
#endif
#if GS_HAS_AHT20
  {
    sensors_event_t hum, temp;
    const bool ok = s_aht_ok && s_aht.getEvent(&hum, &temp);
    put(out, cap, &n, "aht20", "temperature", "C", ok ? temp.temperature : NAN, ok);
    put(out, cap, &n, "aht20", "humidity", "%", ok ? hum.relative_humidity : NAN, ok);
  }
#endif
#if GS_HAS_VL53L0X
  {
    const uint16_t mm = s_tof_ok ? s_tof.readRangeContinuousMillimeters() : 0;
    const bool ok = s_tof_ok && !s_tof.timeoutOccurred() && mm < 8190;  // 8190+ means out of range
    put(out, cap, &n, "vl53l0x", "distance", "mm", ok ? float(mm) : NAN, ok);
  }
#endif
#if GS_HAS_BH1750
  {
    const float lx = s_bh_ok ? s_bh.readLightLevel() : NAN;
    put(out, cap, &n, "bh1750", "light", "lx", lx, s_bh_ok && lx >= 0);
  }
#endif
#if GS_HAS_SOIL
  {
    const int raw = soil_raw();
    const int pct = gs::soil_percent(raw, GS_CFG_SOIL_RAW_DRY, GS_CFG_SOIL_RAW_WET);
    put(out, cap, &n, "soil", "soil", "%", pct < 0 ? NAN : float(pct), pct >= 0, raw);
  }
#endif
#if GS_HAS_LEVEL
  put(out, cap, &n, "float", "water", "", water_up() ? 1.0f : 0.0f, true);
#endif
  return n;
}

static bool first(const char* quantity, float* v) {
  Reading r[12];
  const int n = read_all(r, 12);
  for (int i = 0; i < n; ++i) {
    if (r[i].ok && strcmp(r[i].quantity, quantity) == 0) {
      *v = r[i].value;
      return true;
    }
  }
  return false;
}

bool temperature_c(float* v) { return first("temperature", v); }
bool humidity_pct(float* v) { return first("humidity", v); }
bool pressure_hpa(float* v) { return first("pressure", v); }
bool light_lux(float* v) { return first("light", v); }
bool distance_mm(float* v) { return first("distance", v); }

bool has_rtc() {
#if GS_HAS_DS3231
  return s_rtc_ok;
#else
  return false;
#endif
}

bool rtc_now(time_t* t) {
#if GS_HAS_DS3231
  if (!s_rtc_ok || s_rtc.lostPower()) return false;
  *t = time_t(s_rtc.now().unixtime());
  return true;
#else
  (void)t;
  return false;
#endif
}

bool rtc_set(time_t t) {
#if GS_HAS_DS3231
  if (!s_rtc_ok) return false;
  s_rtc.adjust(DateTime(uint32_t(t)));
  return true;
#else
  (void)t;
  return false;
#endif
}

}  // namespace gssensors
