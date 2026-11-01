"""MAJ-28: the concept-set rules, one test per rule, on the benchmark's own concept set and on a fixed set."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from app.concept_rules import archetype_cues, check_set, face_ok, layout_signature, subject_from_text

FX = Path(__file__).parent / "fixtures" / "e2e"
PROMPT = "I want a cute robot that tells my daughter bedtime stories and remembers her favorite characters."
NAMES = {"OLED_130_SH1106_I2C": {"name": "1.3 in 128x64 mono OLED, SH1106, I2C 4-pin", "role": "display"},
         "OLED_096_SSD1306_I2C": {"name": "0.96 in 128x64 mono OLED, SSD1306, I2C 4-pin", "role": "display"},
         "LCD_200_ST7789_240X320_WAVESHARE": {"name": "2.0 in IPS colour LCD 240x320, ST7789V", "role": "display"},
         "LED_WS2812B_1PX_MODULE": {"name": "LED", "role": "led"}, "MIC_INMP441_I2S": {"name": "mic", "role": "mic"},
         "SPK_40MM_4R_3W": {"name": "speaker", "role": "speaker"},
         "BUTTON_ARCADE_30MM_SNAPIN": {"name": "button", "role": "button"}}


def baseline():
    return json.loads((FX / "baseline-concepts.json").read_text())


def rules(fails):
    return {f["rule"] for f in fails}


def test_subject_from_the_benchmark_prompt():
    s = subject_from_text(PROMPT)
    assert s["noun"] == "robot" and s["archetype"] == "robot" and s["phrase"] == "a cute robot"
    assert subject_from_text("a night light shaped like an owl")["noun"] == "owl"
    assert subject_from_text("something to remind me to drink water") is None


def test_baseline_set_fails_subject_archetype_face_and_layout():
    fails = check_set(baseline(), subject_from_text(PROMPT), NAMES)
    r = rules(fails)
    assert {"subject_expected", "archetype", "layout"} <= r
    exp = next(f for f in fails if f["rule"] == "subject_expected")
    assert set(exp["titles"]) == {"Sleepy Bear", "Little Cloud"}
    lay = next(f for f in fails if f["rule"] == "layout")
    assert lay["titles"] == ["Little Cloud"] and "same layout as Sleepy Bear" in lay["message"]


def robot_set():
    base = json.loads((FX / "concept-spec.json").read_text())
    base["decor"] = [{"type": "face_plate", "shape": "rounded_rect", "at": [0, 120], "size": [90, 50]},
                     {"type": "mouth", "style": "smile", "at": [0, 100], "size": [18, 6]},
                     {"type": "blush", "at": [30, 104], "size": [12, 7], "side": "both"},
                     {"type": "ear", "style": "antenna", "at": [30, 160], "size": [14, 20]}]
    out = []
    moves = [{}, {"mic": [-30, 85]}, {"status_light": [0, 30], "speaker": [0, 70]}, {"story_button": "front"},
             {"mic": [-30, 85], "status_light": [0, 30], "speaker": [0, 70]}, {"speaker": [0, 100], "status_light": [0, 40]}]
    titles = [("expected", "Moon Bot"), ("expected", "Owl Bot"), ("different", "Cloud Robot"),
              ("different", "Lantern Bot"), ("wildcard", "Snail Shell"), ("wildcard", "Teapot Robot")]
    for (tier, title), mv in zip(titles, moves):
        s = copy.deepcopy(base)
        for f in s["features"]:
            if f["id"] in mv:
                if mv[f["id"]] == "front":
                    f["face"], f["at"] = "front", [0, 30]
                else:
                    f["at"] = mv[f["id"]]
        out.append({"title": title, "tier": tier, "blurb": f"A {title.lower()} with a visor face.", "spec": s})
    return out


def test_fixed_set_passes_every_rule():
    cs = robot_set()
    assert check_set(cs, subject_from_text(PROMPT), NAMES) == []
    assert archetype_cues(cs[0]["spec"], NAMES, "robot") >= {"antenna ears", "visor plate"}


def test_rule_subject_per_tier():
    cs = robot_set()
    for c in cs:
        if c["tier"] == "different":
            c["title"], c["blurb"] = "Cloud Pal", "A soft cloud."
    assert "subject_tier" in rules(check_set(cs, subject_from_text(PROMPT), NAMES))


def test_rule_archetype_needs_four_of_six():
    cs = robot_set()
    for c in cs[:3]:
        c["spec"]["decor"] = [d for d in c["spec"]["decor"] if d["type"] in ("mouth", "blush")]
    f = next(x for x in check_set(cs, subject_from_text(PROMPT), NAMES) if x["rule"] == "archetype")
    assert "Only 2 of 6" in f["message"]                    # Snail Shell is not named a robot


def test_rule_face():
    s = copy.deepcopy(robot_set()[0]["spec"])
    s["decor"] = [d for d in s["decor"] if d["type"] != "blush"]
    ok, why = face_ok(s, NAMES)
    assert not ok and "mouth" in why
    s["features"][0]["module"] = "LCD_200_ST7789_240X320_WAVESHARE"        # a 2.0 in screen face is enough
    assert face_ok(s, NAMES)[0]


def test_rule_layout():
    cs = robot_set()
    cs[1]["spec"]["features"] = copy.deepcopy(cs[0]["spec"]["features"])
    f = next(x for x in check_set(cs, subject_from_text(PROMPT), NAMES) if x["rule"] == "layout")
    assert f["titles"] == ["Owl Bot"] and "same layout as Moon Bot" in f["message"]
    assert layout_signature(cs[0]["spec"], NAMES) == layout_signature(cs[1]["spec"], NAMES)


def test_rule_borrowed_names():
    from app.concept_rules import borrowed

    cs = robot_set()
    cs[0]["title"] = "Moon Hug"
    f = next(x for x in check_set(cs, subject_from_text(PROMPT), NAMES) if x["rule"] == "borrowed_name")
    assert f["titles"] == ["Moon Hug"]
    assert borrowed("Bedtime  Story Robot") and borrowed("cloud forecast lamp") and not borrowed("Story Time Bot")
