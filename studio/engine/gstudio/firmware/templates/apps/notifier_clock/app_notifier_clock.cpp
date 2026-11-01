// notifier_clock: the time (from the internet time service, kept by the RTC when fitted) on the
// display, and notifications from the local JSON API shown on the screen and the LED ring.
// Tap the button to clear the top notification; hold to clear all. Quiet hours dim the ring and mute
// the chime.
#include <ArduinoJson.h>
#include <WiFi.h>
#include <sys/time.h>
#include <time.h>

#include "config.h"
#include "gs_app.h"
#include "gs_audio.h"
#include "gs_display.h"
#include "gs_led.h"
#include "gs_light.h"
#include "gs_sensors.h"
#include "gs_templates.h"
#include "hardware.h"

#if GS_DISPLAY_KIND == GS_DISP_NONE
#error "notifier_clock needs a display"
#endif

class NotifierClockApp : public GsApp {
 public:
  const char* template_name() const override { return "notifier_clock"; }

  void begin(uint32_t now) override {
    (void)now;
    setenv("TZ", GS_CFG_TZ, 1);
    tzset();
    time_t t = 0;
    if (gssensors::rtc_now(&t)) {  // the RTC keeps time across power cuts until Wi-Fi is back
      timeval tv{t, 0};
      settimeofday(&tv, nullptr);
      source_ = "rtc";
    }
    configTzTime(GS_CFG_TZ, GS_CFG_NTP_SERVER, "pool.ntp.org");
  }

  void loop(uint32_t now) override {
    queue_.expire(now);
    tm lt;
    const bool have = time_valid(&lt);
    if (have && !synced_ && WiFi.status() == WL_CONNECTED && time(nullptr) > 1700000000) {
      // First valid internet time after boot: store it in the RTC.
      if (sntp_synced()) {
        synced_ = true;
        source_ = "internet";
        gssensors::rtc_set(time(nullptr));
      }
    }
    const bool quiet = have && gs::in_quiet_hours(uint16_t(lt.tm_hour * 60 + lt.tm_min), GS_CFG_QUIET_START, GS_CFG_QUIET_END);
    quiet_ = quiet;
    if (now - last_draw_ >= 500) {
      last_draw_ = now;
      draw(have ? &lt : nullptr, now);
    }
    if (now - last_led_ >= 30) {
      last_led_ = now;
      const gs::Notice* top = queue_.top(now);
      if (top) {
        uint8_t level = uint8_t((uint16_t(gs::breathe(now, 2000)) * GS_CFG_RING_LEVEL) / 255);
        if (quiet) level /= 4;
        gsled::set_all(gs::scale(top->color, level));
      } else {
        gsled::set_all(gs::Rgb());
      }
      gsled::show();
    }
  }

  void on_button(uint8_t index, gs::ButtonEvent ev, uint32_t now) override {
    (void)index;
    if (ev == gs::ButtonEvent::Tap) {
      const gs::Notice* top = queue_.top(now);
      if (top) queue_.ack(top->id);
    } else if (ev == gs::ButtonEvent::LongStart) {
      queue_.ack_all();
    }
  }

  void state(JsonObject out, uint32_t now) override {
    tm lt;
    char buf[24];
    if (time_valid(&lt)) {
      gs::format_clock(lt.tm_hour, lt.tm_min, GS_CFG_CLOCK_24H, buf, sizeof(buf));
      out["time"] = buf;
    } else {
      out["time"] = "--:--";
    }
    out["time_note"] = String("Time source: ") + source_ + (quiet_ ? ". Quiet hours: ring dimmed, chime off." : ".");
    out["quiet_hours"] = quiet_;
    JsonArray arr = out["notices"].to<JsonArray>();
    for (size_t i = 0; i < queue_.count(); ++i) {
      const gs::Notice* n = queue_.at(i);
      JsonObject o = arr.add<JsonObject>();
      o["id"] = n->id;
      o["text"] = n->text;
      o["priority"] = n->priority;
      char hex[8];
      snprintf(hex, sizeof(hex), "#%02X%02X%02X", n->color.r, n->color.g, n->color.b);
      o["color"] = hex;
      if (n->expires) o["expires_in_s"] = (n->expires - now) / 1000;
    }
    (void)now;
  }

  int action(const char* name, JsonObjectConst a, JsonObject out, uint32_t now) override {
    if (!strcmp(name, "notify")) {
      const char* text = a["text"] | "";
      if (!text[0] || strlen(text) > gs::kNoticeTextMax) return gs_reply(out, 400, "Send text of 1 to 47 characters.");
      gs::Rgb c{48, 70, 217};
      if (a["color"].is<const char*>() && !gs::parse_hex_color(a["color"], &c))
        return gs_reply(out, 400, "Colour must look like #3046D9.");
      const int prio = a["priority"] | 1;
      const int minutes = a["minutes"] | int(GS_CFG_NOTICE_MS / 60000u);
      if (prio < 0 || prio > 9 || minutes < 0 || minutes > 1440) return gs_reply(out, 400, "priority 0..9, minutes 0..1440.");
      out["id"] = queue_.push(text, c, uint8_t(prio), uint32_t(minutes) * 60000u, now);
      if (GS_CFG_NOTICE_CHIME && !quiet_) gsaudio::play_chime(gs::Chime::Ready);
      last_draw_ = 0;
      return gs_reply(out, 200, "Notification shown.");
    }
    if (!strcmp(name, "notify_clear")) {
      const int id = a["id"] | 0;
      if (!queue_.ack(uint16_t(id))) return gs_reply(out, 404, "No notification with that id.");
      last_draw_ = 0;
      return gs_reply(out, 200, "Cleared.");
    }
    if (!strcmp(name, "notify_clear_all")) {
      queue_.ack_all();
      last_draw_ = 0;
      return gs_reply(out, 200, "All cleared.");
    }
    return gs_reply(out, 404, "Unknown action for notifier_clock.");
  }

 private:
  static bool sntp_synced() { return time(nullptr) > 1700000000; }

  bool time_valid(tm* out) const {
    const time_t t = time(nullptr);
    if (t < 1700000000) return false;  // before Nov 2023: the clock has not been set
    localtime_r(&t, out);
    return true;
  }

  void draw(const tm* lt, uint32_t now) {
    const int w = gsdisp::width();
    const int h = gsdisp::height();
    const uint16_t fg = gsdisp::kWhite;
    const uint16_t accent = gsdisp::is_color() ? gsdisp::rgb(255, 179, 107) : gsdisp::kWhite;
    gsdisp::clear();
    char buf[32];
    if (lt) {
      gs::format_clock(lt->tm_hour, lt->tm_min, GS_CFG_CLOCK_24H, buf, sizeof(buf));
    } else {
      snprintf(buf, sizeof(buf), "--:--");
    }
    gsdisp::text((w - gsdisp::text_width(buf, 3)) / 2, h / 10, buf, 3, fg);
    if (lt) {
      strftime(buf, sizeof(buf), "%a %d %b", lt);
    } else {
      snprintf(buf, sizeof(buf), WiFi.status() == WL_CONNECTED ? "getting the time" : "no Wi-Fi yet");
    }
    gsdisp::text((w - gsdisp::text_width(buf, 1)) / 2, h / 2, buf, 1, fg);
    const gs::Notice* top = queue_.top(now);
    if (top) {
      gsdisp::fill_rect(0, h * 3 / 4 - 2, w, 1, accent);
      gsdisp::text(2, h * 3 / 4 + 1, top->text, 1, accent);
      if (queue_.count() > 1) {
        snprintf(buf, sizeof(buf), "+%u", unsigned(queue_.count() - 1));
        gsdisp::text(w - gsdisp::text_width(buf, 1) - 2, h * 3 / 4 + 1, buf, 1, accent);
      }
    }
    gsdisp::flush();
  }

  gs::NoticeQueue queue_;
  const char* source_ = "not set";
  bool synced_ = false;
  bool quiet_ = false;
  uint32_t last_draw_ = 0;
  uint32_t last_led_ = 0;
};

static NotifierClockApp s_app;
GsApp* gs_make_app() { return &s_app; }
