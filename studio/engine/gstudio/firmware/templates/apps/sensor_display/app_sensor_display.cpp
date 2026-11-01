// sensor_display: room readings (temperature, humidity, pressure, light, distance from the library's
// I2C sensors) on the display, a trend graph, and a warning light outside the set range.
// Tap: next page (readings, trend, details). All values are readings, not calibrated measurements.
#include <ArduinoJson.h>

#include "config.h"
#include "gs_app.h"
#include "gs_display.h"
#include "gs_led.h"
#include "gs_light.h"
#include "gs_sensors.h"
#include "gs_templates.h"
#include "hardware.h"

#if GS_DISPLAY_KIND == GS_DISP_NONE
#error "sensor_display needs a display"
#endif

class SensorDisplayApp : public GsApp {
 public:
  const char* template_name() const override { return "sensor_display"; }

  void begin(uint32_t now) override {
    gs::parse_hex_color(GS_CFG_WARN_COLOR, &warn_color_);
    sample(now);
  }

  void loop(uint32_t now) override {
    if (now - last_sample_ >= GS_CFG_SAMPLE_MS) sample(now);
    if (now - last_draw_ >= 1000 || dirty_) {
      last_draw_ = now;
      dirty_ = false;
      draw();
    }
    if (now - last_led_ >= 30) {
      last_led_ = now;
      const bool warn = GS_CFG_LED_WARNING && (temp_warn_.active() || hum_warn_.active());
      gsled::set_all(warn ? gs::scale(warn_color_, gs::breathe(now, 2400)) : gs::Rgb());
      gsled::show();
    }
  }

  void on_button(uint8_t index, gs::ButtonEvent ev, uint32_t now) override {
    (void)index;
    (void)now;
    if (ev == gs::ButtonEvent::Tap) {
      page_ = uint8_t((page_ + 1) % 3);
      dirty_ = true;
    }
  }

  void state(JsonObject out, uint32_t now) override {
    (void)now;
    JsonArray arr = out["readings"].to<JsonArray>();
    for (int i = 0; i < n_; ++i) {
      JsonObject o = arr.add<JsonObject>();
      o["sensor"] = r_[i].sensor;
      o["quantity"] = r_[i].quantity;
      o["unit"] = unit(r_[i]);
      o["value"] = round1(shown(r_[i]));
      o["ok"] = r_[i].ok;
    }
    float pts[48];
    const size_t m = trend_.downsample(pts, 48);
    JsonArray t = out["trend"].to<JsonArray>();
    for (size_t i = 0; i < m; ++i) t.add(round1(GS_CFG_FAHRENHEIT ? pts[i] * 9 / 5 + 32 : pts[i]));
    out["trend_label"] = String("Temperature, last ") + GS_CFG_TREND_HOURS + " h (" + (GS_CFG_FAHRENHEIT ? "F" : "C") + ")";
    if (temp_warn_.active()) out["warning"] = "Temperature outside the set range";
    else if (hum_warn_.active()) out["warning"] = "Humidity outside the set range";
  }

  int action(const char* name, JsonObjectConst a, JsonObject out, uint32_t now) override {
    if (!strcmp(name, "sample")) {
      sample(now);
      return gs_reply(out, 200, "Read the sensors.");
    }
    if (!strcmp(name, "page")) {
      const int p = a["page"] | -1;
      if (p < 0 || p > 2) return gs_reply(out, 400, "Pages are 0, 1 and 2.");
      page_ = uint8_t(p);
      dirty_ = true;
      return gs_reply(out, 200, "Page changed.");
    }
    return gs_reply(out, 404, "Unknown action for sensor_display.");
  }

 private:
  static float round1(float v) { return float(int(v * 10.0f + (v < 0 ? -0.5f : 0.5f))) / 10.0f; }

  static float shown(const gssensors::Reading& r) {
    if (GS_CFG_FAHRENHEIT && !strcmp(r.quantity, "temperature")) return r.value * 9.0f / 5.0f + 32.0f;
    return r.value;
  }

  static const char* unit(const gssensors::Reading& r) {
    if (GS_CFG_FAHRENHEIT && !strcmp(r.quantity, "temperature")) return "F";
    return r.unit;
  }

  void sample(uint32_t now) {
    last_sample_ = now;
    n_ = gssensors::read_all(r_, 12);
    float t = 0, h = 0;
    if (gssensors::temperature_c(&t)) {
      temp_warn_.update(t);
      if (now - last_trend_ >= GS_CFG_TREND_POINT_MS || trend_.size() == 0) {
        last_trend_ = now;
        trend_.push(t);
      }
    }
    if (gssensors::humidity_pct(&h)) hum_warn_.update(h);
    dirty_ = true;
  }

  void draw() {
    const int w = gsdisp::width();
    const int h = gsdisp::height();
    const uint16_t fg = gsdisp::kWhite;
    const uint16_t warn = gsdisp::is_color() ? gsdisp::rgb(warn_color_.r, warn_color_.g, warn_color_.b) : fg;
    gsdisp::clear();
    char buf[40];
    if (page_ == 0) {
      float t = 0, hu = 0;
      if (gssensors::temperature_c(&t)) {
        snprintf(buf, sizeof(buf), "%.1f%s", GS_CFG_FAHRENHEIT ? t * 9 / 5 + 32 : t, GS_CFG_FAHRENHEIT ? "F" : "C");
      } else {
        snprintf(buf, sizeof(buf), "--");
      }
      gsdisp::text((w - gsdisp::text_width(buf, 3)) / 2, h / 12, buf, 3, temp_warn_.active() ? warn : fg);
      if (gssensors::humidity_pct(&hu)) {
        snprintf(buf, sizeof(buf), "%.0f%% humidity", hu);
        gsdisp::text((w - gsdisp::text_width(buf, 1)) / 2, h * 2 / 3, buf, 1, hum_warn_.active() ? warn : fg);
      }
    } else if (page_ == 1) {
      float pts[160];
      const int cols = w - 4 > 160 ? 160 : w - 4;
      const size_t m = trend_.downsample(pts, size_t(cols));
      snprintf(buf, sizeof(buf), "Last %d h", GS_CFG_TREND_HOURS);
      gsdisp::text(2, 0, buf, 1, fg);
      if (m > 1) {
        float lo = pts[0], hi = pts[0];
        for (size_t i = 1; i < m; ++i) {
          lo = pts[i] < lo ? pts[i] : lo;
          hi = pts[i] > hi ? pts[i] : hi;
        }
        const float span = hi - lo > 0.5f ? hi - lo : 0.5f;
        const int top = h / 5, bottom = h - 2;
        for (size_t i = 1; i < m; ++i) {
          const int y0 = bottom - int((pts[i - 1] - lo) / span * float(bottom - top));
          const int y1 = bottom - int((pts[i] - lo) / span * float(bottom - top));
          gsdisp::line(2 + int(i) - 1, y0, 2 + int(i), y1, fg);
        }
        snprintf(buf, sizeof(buf), "%.1f-%.1f", lo, hi);
        gsdisp::text(w - gsdisp::text_width(buf, 1) - 2, 0, buf, 1, fg);
      } else {
        gsdisp::text(2, h / 2, "collecting...", 1, fg);
      }
    } else {
      int y = 0;
      const int step = h / 6 > 10 ? h / 6 : 10;
      for (int i = 0; i < n_ && y < h; ++i, y += step) {
        snprintf(buf, sizeof(buf), "%s %s", r_[i].quantity, r_[i].ok ? "" : "?");
        gsdisp::text(2, y, buf, 1, fg);
        if (r_[i].ok) snprintf(buf, sizeof(buf), "%.1f %s", shown(r_[i]), unit(r_[i]));
        gsdisp::text(w - gsdisp::text_width(r_[i].ok ? buf : "--", 1) - 2, y, r_[i].ok ? buf : "--", 1, fg);
      }
    }
    gsdisp::flush();
  }

  gssensors::Reading r_[12];
  int n_ = 0;
  gs::Trend trend_{144};
  gs::Threshold temp_warn_{GS_CFG_TEMP_LOW, GS_CFG_TEMP_HIGH, 0.5f};
  gs::Threshold hum_warn_{GS_CFG_HUM_LOW, GS_CFG_HUM_HIGH, 2.0f};
  gs::Rgb warn_color_{255, 90, 54};
  uint8_t page_ = 0;
  bool dirty_ = true;
  uint32_t last_sample_ = 0;
  uint32_t last_trend_ = 0;
  uint32_t last_draw_ = 0;
  uint32_t last_led_ = 0;
};

static SensorDisplayApp s_app;
GsApp* gs_make_app() { return &s_app; }
