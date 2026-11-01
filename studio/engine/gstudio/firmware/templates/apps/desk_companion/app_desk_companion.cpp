// desk_companion: a face that blinks, smiles when touched and gets sleepy when left alone.
// Tap: a smile and a chime. Hold: glow light on/off. Clap (with a microphone): a surprised face;
// a double clap toggles the light. Clap detection reads sound levels only: nothing is recorded,
// stored or sent, and a small dot in the corner of the face shows while the microphone is on.
#include <ArduinoJson.h>
#include <esp_random.h>

#include "config.h"
#include "gs_app.h"
#include "gs_audio.h"
#include "gs_device.h"
#include "gs_display.h"
#include "gs_led.h"
#include "gs_light.h"
#include "gs_templates.h"
#include "hardware.h"

#if GS_DISPLAY_KIND == GS_DISP_NONE
#error "desk_companion needs a display for its face"
#endif

class DeskCompanionApp : public GsApp {
 public:
  const char* template_name() const override { return "desk_companion"; }
  // The face shows a microphone dot whenever the mic is open (see update_face), and the glow light
  // stays the parent's choice, so the shared listening light is not used.
  bool show_listening(bool on) override {
    mic_open_ = on;
    return true;
  }

  void begin(uint32_t now) override {
    face_ = gs::FaceFsm(esp_random(), GS_CFG_SLEEP_AFTER_MS);
    gs::parse_hex_color(GS_CFG_LIGHT_COLOR, &light_color_);
    light_on_ = gs_store().mem().app[0] != 0;
    clap_on_ = GS_CFG_CLAP_DETECT && gsaudio::mic_begin();
    gsaudio::mic_monitor(clap_on_);
    clap_.set_threshold(GS_CFG_CLAP_THRESHOLD);
    face_.tick(now);
    face_.poke(now);
    gsaudio::play_chime(gs::Chime::Ready);
  }

  void loop(uint32_t now) override {
    face_.tick(now);
    int16_t peak = 0;
    while (clap_on_ && gsaudio::mic_take_block_peak(&peak)) {
      const gs::ClapEvent ev = clap_.feed(now, peak);
      if (ev == gs::ClapEvent::Single) {
        face_.clap(now);
      } else if (ev == gs::ClapEvent::Double) {
        set_light(!light_on_);
      }
    }
    draw(now);
    if (now - last_led_ >= 30) {
      last_led_ = now;
      gsled::set_all(light_on_ ? gs::scale(light_color_, GS_CFG_LIGHT_LEVEL) : gs::Rgb());
      gsled::show();
    }
  }

  void on_button(uint8_t index, gs::ButtonEvent ev, uint32_t now) override {
    (void)index;
    if (ev == gs::ButtonEvent::Tap) {
      face_.poke(now);
      if (GS_CFG_CHIME_ON_TAP) gsaudio::play_chime(gs::Chime::Saved);
    } else if (ev == gs::ButtonEvent::LongStart) {
      set_light(!light_on_);
    }
  }

  void state(JsonObject out, uint32_t now) override {
    out["face"] = gs::expression_name(face_.expression(now));
    out["light_on"] = light_on_;
    out["clap_detect"] = clap_on_;
    out["mic_note"] = clap_on_ ? "The microphone measures loudness for claps only; no sound is recorded or sent."
                               : "Clap detection is off.";
    out["volume"] = gs_store().mem().volume;
    out["volume_max"] = GS_CFG_VOLUME_MAX;
    out["volume_note"] = "software setting, not a measured sound level";
  }

  int action(const char* name, JsonObjectConst a, JsonObject out, uint32_t now) override {
    if (!strcmp(name, "poke")) {
      face_.poke(now);
      return gs_reply(out, 200, "Said hello.");
    }
    if (!strcmp(name, "chime")) {
      gsaudio::play_chime(gs::Chime::Saved);
      return gs_reply(out, 200, "Chimed.");
    }
    if (!strcmp(name, "light")) {
      set_light(a["on"] | !light_on_);
      return gs_reply(out, 200, light_on_ ? "Light on." : "Light off.");
    }
    if (!strcmp(name, "clap_detect")) {
      clap_on_ = (a["on"] | false) && gsaudio::mic_begin();
      gsaudio::mic_monitor(clap_on_);
      return gs_reply(out, 200, clap_on_ ? "Clap detection on." : "Clap detection off; the microphone is closed.");
    }
    if (!strcmp(name, "set_volume")) {
      const int v = a["volume"] | -1;
      if (v < 0 || v > GS_CFG_VOLUME_MAX) return gs_reply(out, 400, "That is above this device's volume limit.");
      gs_store().set_volume(uint8_t(v));
      gsaudio::set_volume(uint8_t(v), GS_CFG_VOLUME_MAX);
      return gs_reply(out, 200, "Volume saved.");
    }
    return gs_reply(out, 404, "Unknown action for desk_companion.");
  }

 private:
  void set_light(bool on) {
    light_on_ = on;
    gs_store().set_app_byte(0, on ? 1 : 0);
  }

  void draw(uint32_t now) {
    const gs::Expression e = face_.expression(now);
    const uint32_t frame = e == gs::Expression::Listening ? now / 250 : 0;
    const bool mic = clap_on_ || mic_open_;
    if (e == last_ && frame == last_frame_ && mic == last_mic_) return;
    last_ = e;
    last_frame_ = frame;
    last_mic_ = mic;
    const uint16_t c = gsdisp::is_color() ? gsdisp::rgb(140, 220, 255) : gsdisp::kWhite;
    gsdisp::draw_face(e, now, c);
    if (mic) {
      gsdisp::fill_circle(gsdisp::width() - 4, 3, 2, c);  // microphone-on dot
      gsdisp::flush();
    }
  }

  gs::FaceFsm face_;
  gs::ClapDetector clap_;
  gs::Rgb light_color_{127, 211, 255};
  bool light_on_ = false;
  bool clap_on_ = false;
  bool last_mic_ = false;
  volatile bool mic_open_ = false;
  gs::Expression last_ = gs::Expression::Blink;
  uint32_t last_frame_ = 1;
  uint32_t last_led_ = 0;
};

static DeskCompanionApp s_app;
GsApp* gs_make_app() { return &s_app; }
