// ambient_light: an LED ring or strip with scenes (solid, breathe, candle, rainbow).
// Tap: next scene. Hold: on/off. A light sensor dims it in a dark room; a motion sensor wakes it.
// The parent page picks scenes and brightness. Brightness never exceeds the build's LED cap.
#include <ArduinoJson.h>
#include <esp_random.h>

#include "config.h"
#include "gs_app.h"
#include "gs_board.h"
#include "gs_device.h"
#include "gs_led.h"
#include "gs_light.h"
#include "gs_sensors.h"
#include "gs_templates.h"
#include "hardware.h"

#if !GS_HAS_NEOPIXEL
#error "ambient_light needs a WS2812B LED or ring"
#endif

struct SceneDef {
  const char* name;
  uint8_t r, g, b, level;
  gs::Effect effect;
};
static const SceneDef kScenes[] = GS_CFG_SCENES;
static constexpr uint8_t kSceneCount = sizeof(kScenes) / sizeof(kScenes[0]);

// Memory-record app bytes: 0 scene, 1 on/off (0 = on, 1 = off), 2 brightness percent (0 = default 100).
class AmbientLightApp : public GsApp {
 public:
  const char* template_name() const override { return "ambient_light"; }

  void begin(uint32_t now) override {
    const gs::Memory& m = gs_store().mem();
    scene_ = m.app[0] < kSceneCount ? m.app[0] : uint8_t(GS_CFG_DEFAULT_SCENE);
    on_ = m.app[1] == 0;
    brightness_ = (m.app[2] >= 1 && m.app[2] <= 100) ? m.app[2] : 100;
    seed_ = esp_random();
    last_activity_ = now;
#if GS_HAS_MOTION
    pinMode(GS_PIN_MOTION, INPUT);
#endif
  }

  void loop(uint32_t now) override {
#if GS_HAS_MOTION
    if (digitalRead(GS_PIN_MOTION) == HIGH) {
      last_activity_ = now;
      if (!on_ && auto_off_ && GS_CFG_MOTION_WAKE) set_on(true);
    }
#endif
    if (GS_CFG_OFF_AFTER_MS && on_ && now - last_activity_ > GS_CFG_OFF_AFTER_MS) {
      set_on(false);
      auto_off_ = true;
    }
    if (now - last_sense_ >= 200) {
      last_sense_ = now;
      sense();
    }
    if (now - last_frame_ >= 20) {
      last_frame_ = now;
      render(now);
    }
  }

  void on_button(uint8_t index, gs::ButtonEvent ev, uint32_t now) override {
    (void)index;
    last_activity_ = now;
    if (ev == gs::ButtonEvent::Tap) {
      if (!on_) {
        set_on(true);
      } else {
        set_scene(uint8_t((scene_ + 1) % kSceneCount));
      }
    } else if (ev == gs::ButtonEvent::LongStart) {
      set_on(!on_);
    }
  }

  void state(JsonObject out, uint32_t now) override {
    (void)now;
    JsonArray arr = out["scenes"].to<JsonArray>();
    for (uint8_t i = 0; i < kSceneCount; ++i) {
      JsonObject o = arr.add<JsonObject>();
      o["name"] = kScenes[i].name;
      o["effect"] = gs::effect_name(kScenes[i].effect);
    }
    out["scene"] = scene_;
    out["on"] = on_;
    out["brightness"] = brightness_;
    out["led_count"] = gsled::count();
    if (have_lux_) out["lux"] = lux_;
    out["auto_dim"] = auto_dim_active();
    out["auto_dim_note"] = auto_dim_active() ? (have_lux_ ? "Dims in a dark room (light sensor reading, not a calibrated lux meter)."
                                                          : "Dims in a dark room (LDR reading, uncalibrated).")
                                             : "Auto-dim is off or there is no light sensor.";
  }

  int action(const char* name, JsonObjectConst a, JsonObject out, uint32_t now) override {
    last_activity_ = now;
    if (!strcmp(name, "scene")) {
      const int i = a["index"] | -1;
      if (i < 0 || i >= kSceneCount) return gs_reply(out, 400, "No scene with that number.");
      set_scene(uint8_t(i));
      set_on(true);
      return gs_reply(out, 200, kScenes[i].name);
    }
    if (!strcmp(name, "on")) {
      set_on(true);
      return gs_reply(out, 200, "On.");
    }
    if (!strcmp(name, "off")) {
      set_on(false);
      return gs_reply(out, 200, "Off.");
    }
    if (!strcmp(name, "brightness")) {
      const int p = a["percent"] | -1;
      if (p < 1 || p > 100) return gs_reply(out, 400, "Brightness is 1 to 100 percent.");
      brightness_ = uint8_t(p);
      gs_store().set_app_byte(2, brightness_);
      return gs_reply(out, 200, "Brightness saved.");
    }
    return gs_reply(out, 404, "Unknown action for ambient_light.");
  }

 private:
  bool auto_dim_active() const { return GS_CFG_AUTO_DIM && (GS_HAS_BH1750 || GS_HAS_LDR); }

  void set_on(bool on) {
    on_ = on;
    if (on) auto_off_ = false;
    gs_store().set_app_byte(1, on ? 0 : 1);
  }

  void set_scene(uint8_t i) {
    scene_ = i;
    gs_store().set_app_byte(0, i);
  }

  void sense() {
    float lux = 0;
    if (gssensors::light_lux(&lux)) {
      have_lux_ = true;
      lux_ = lux;
      if (GS_CFG_AUTO_DIM) dim_.update(lux);
      return;
    }
#if GS_HAS_LDR
    // LM393 LDR boards: the analog output rises as the room gets darker (typical; check yours).
    const int raw = gsboard::ldr_read();
    if (raw >= 0 && GS_CFG_AUTO_DIM) {
      const float pseudo = GS_CFG_BRIGHT_LUX * (1.0f - float(raw) / 4095.0f);
      dim_.update(pseudo);
    }
#endif
  }

  void render(uint32_t now) {
    if (!on_) {
      gsled::set_all(gs::Rgb());
      gsled::show();
      return;
    }
    const SceneDef& d = kScenes[scene_];
    gs::Scene sc;
    sc.color = gs::Rgb{d.r, d.g, d.b};
    sc.effect = d.effect;
    uint32_t level = uint32_t(d.level) * brightness_ / 100;
    if (auto_dim_active()) level = level * dim_.factor() / 255;
    sc.brightness = uint8_t(level > 255 ? 255 : level);
    const uint16_t n = gsled::count();
    for (uint16_t i = 0; i < n; ++i) gsled::set(i, gs::scene_pixel(sc, i, n, now, seed_));
    gsled::show();
  }

  gs::AutoDim dim_{GS_CFG_DARK_LUX, GS_CFG_BRIGHT_LUX, 25};
  uint8_t scene_ = 0;
  uint8_t brightness_ = 100;
  bool on_ = true;
  bool auto_off_ = false;
  bool have_lux_ = false;
  float lux_ = 0;
  uint32_t seed_ = 1;
  uint32_t last_activity_ = 0;
  uint32_t last_sense_ = 0;
  uint32_t last_frame_ = 0;
};

static AmbientLightApp s_app;
GsApp* gs_make_app() { return &s_app; }
