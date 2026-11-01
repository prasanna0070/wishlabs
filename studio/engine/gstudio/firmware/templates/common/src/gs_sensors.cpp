#include "gs_sensors.h"

#include <Wire.h>

#include "hardware.h"

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
  return any;
}

int sensor_count() {
  return GS_HAS_BMP280 + GS_HAS_AHT20 + GS_HAS_BH1750 + GS_HAS_DS3231 + GS_HAS_VL53L0X;
}

__attribute__((unused)) static void put(Reading* out, int cap, int* n, const char* sensor, const char* q, const char* unit, float v, bool ok) {
  if (*n >= cap) return;
  Reading& r = out[(*n)++];
  r.sensor = sensor;
  r.quantity = q;
  r.unit = unit;
  r.value = v;
  r.ok = ok && !isnan(v);
}

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
