"""Writes provisional electrical.json + spec fixtures for the firmware tests (ARCHITECTURE section 7 shape).

The GPIO numbers and slot pin maps are read from library/backplane-v1.json, and module ids are real
library ids, so the fixtures match what gstudio.electrical will produce. Run: python make_fixtures.py
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
STUDIO = HERE.parents[3]
BP = json.loads((STUDIO / "library" / "backplane-v1.json").read_text())
SLOTS = {s["id"]: s for s in BP["slots"]}

DESIGNS = {
    "story_player": {
        "name": "Luna Owl", "slug": "luna-owl", "child": True,
        "settings": {"names_for_audio": ["Anya"]},
        "parts": [("BTN1", "BUTTON_ARCADE_30MM_SNAPIN", "story_button", "J_IO1", "Story button"),
                  ("LED1", "LED_WS2812B_1PX_MODULE", "night_light", "J_LED", "Night light and listening light"),
                  ("SPK1", "SPK_40MM_4R_3W", "speaker", "J_SPK", "Speaker"),
                  ("MIC1", "MIC_INMP441_I2S", "mic", "J_MIC", "Microphone hole"),
                  ("DISP1", "OLED_096_SSD1306_I2C", "eyes", "J_I2C1", "Animated eyes"),
                  ("AMP1", "AMP_MAX98357A_I2S", None, "A1", None),
                  ("SD1", "MICROSD_SPI_MODULE_5V", None, "S1", None)],
    },
    "desk_companion": {
        "name": "Desk Buddy", "slug": "desk-buddy", "child": False, "settings": {"clap_detect": True},
        "parts": [("BTN1", "TOUCH_TTP223_SMALL_15X11", "head_touch", "J_IO1", "Touch to say hello"),
                  ("DISP1", "OLED_130_SH1106_I2C", "face", "J_I2C1", "Face"),
                  ("LED1", "LED_RING_12_WS2812_50MM", "glow", "J_LED", "Glow ring"),
                  ("SPK1", "SPK_28MM_8R_2W", "speaker", "J_SPK", "Speaker"),
                  ("MIC1", "MIC_INMP441_I2S", "mic", "J_MIC", "Clap microphone"),
                  ("AMP1", "AMP_MAX98357A_I2S", None, "A1", None)],
    },
    "notifier_clock": {
        "name": "Chai Clock", "slug": "chai-clock", "child": False, "settings": {},
        "parts": [("DISP1", "LCD_200_ST7789_240X320_WAVESHARE", "screen", "J_DISP", "Clock screen"),
                  ("LED1", "LED_RING_12_WS2812_50MM", "ring", "J_LED", "Notification ring"),
                  ("BTN1", "BUTTON_TACTILE_KY004", "ack_button", "J_IO1", "Clear button"),
                  ("BZ1", "BUZZER_PASSIVE_TRANSISTOR_MODULE", "buzzer", "J_IO2", "Buzzer"),
                  ("RTC1", "RTC_DS3231_ZS042", None, "J_I2C2", None)],
    },
    "ambient_light": {
        "name": "Cloud Lamp", "slug": "cloud-lamp", "child": False, "settings": {},
        "parts": [("LED1", "LED_RING_12_WS2812_50MM", "light", "J_LED", "Light"),
                  ("BTN1", "TOUCH_TTP223_SMALL_15X11", "touch", "J_IO1", "Touch to change scene"),
                  ("PIR1", "PIR_AM312_MINI", "motion", "J_IO2", "Motion sensor"),
                  ("ALS1", "LIGHT_BH1750_GY302_I2C", "light_sensor", "J_I2C1", "Light sensor")],
    },
    "sensor_display": {
        "name": "Room Weather", "slug": "room-weather", "child": False, "settings": {},
        "parts": [("DISP1", "OLED_096_SSD1306_I2C", "screen", "J_I2C1", "Screen"),
                  ("TH1", "TEMPHUM_AHT20_ADAFRUIT_4566", "climate", "J_I2C2", "Temperature and humidity"),
                  ("BARO1", "BARO_BMP280_GY_3V3_I2C", None, "J_I2C3", None),
                  ("LED1", "LED_WS2812B_1PX_MODULE", "warn_light", "J_LED", "Warning light"),
                  ("BTN1", "BUTTON_TACTILE_KY004", "page_button", "J_IO1", "Next page")],
    },
}


def electrical(parts) -> dict:
    instances = [{"ref": "U1", "module": "CORE_ESP32S3_DEVKITC1_N8R8", "feature": None, "slot": "U1"}]
    connections = []
    slots_used = ["J_PWR", "U1"]
    for ref, mid, feat, slot, _label in parts:
        instances.append({"ref": ref, "module": mid, "feature": feat, "slot": slot,
                          "harness": {"cable": f"JST-PH {len(SLOTS[slot]['pins'])}p", "length_mm": 150}})
        slots_used.append(slot)
        for p in SLOTS[slot]["pins"]:
            if p.get("net"):
                connections.append({"from": f"{ref}.{p.get('name')}", "to": f"BP1.{slot}.{p.get('pin')}",
                                    "via": f"{slot} harness", "net": p["net"]})
    i2c_devs = [{"ref": r, "addr": None} for r, _m, _f, s, _l in parts if s.startswith("J_I2C")]
    buses = []
    for b in BP["buses"]:
        b = dict(b)
        if b["type"] == "i2c":
            b["devices"] = i2c_devs
        buses.append(b)
    return {"schema": "gadget-electrical/1", "backplane": "backplane-v1", "provisional": "firmware test fixture",
            "core": {"ref": "U1", "module": "CORE_ESP32S3_DEVKITC1_N8R8"}, "instances": instances,
            "slots_used": slots_used, "gpio": BP["gpio"], "buses": buses, "nets": [], "connections": connections,
            "power": {"rails": [{"name": "+5V", "source": "USB-C via F1"}, {"name": "+3V3", "source": "devkit regulator"}],
                      "budget": [], "ok": True}}


def spec(template: str, d: dict) -> dict:
    feats = [{"id": f, "module": m, "face": "front", "at": [0, 40 + 10 * i], "label": lab}
             for i, (_r, m, f, _s, lab) in enumerate(d["parts"]) if f]
    return {"schema": "gadget-spec/1", "name": d["name"], "slug": d["slug"], "tagline": "fixture",
            "audience": {"child_directed": d["child"], "age_years": 5 if d["child"] else None}, "region": "IN",
            "features": feats, "electronics": {"core": "CORE_ESP32S3_DEVKITC1_N8R8", "extra_modules": []},
            "behaviour": {"template": template, "summary": "fixture", "settings": d["settings"]}}


def main() -> None:
    out = HERE / "fixtures"
    out.mkdir(exist_ok=True)
    for t, d in DESIGNS.items():
        (out / f"electrical-{t}.json").write_text(json.dumps(electrical(d["parts"]), indent=1) + "\n")
        (out / f"spec-{t}.json").write_text(json.dumps(spec(t, d), indent=1) + "\n")
    print("wrote", sorted(p.name for p in out.iterdir()))


if __name__ == "__main__":
    main()
