"""Catalog packs in the agent prompt (CATALOG-PLAN E3).

- with every pack off the prompt has no pack text, no pack example, no pack part and no servo; it refuses motors;
- with packs on it shows each pack's prompt_rules, the motion sentence, the plant say-yes line, pack parts in the table
  and the port rule, and picks pack examples that match the wish;
- the refusal sentence always names cameras, batteries, mains power and heaters.
A temp studio (symlinks to the real engine, examples, docs and landing) holds a library merged with two test packs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import make_settings

STUDIO = Path(__file__).resolve().parents[3]
LIB = STUDIO / "library"

SERVO = {
    "id": "TEST_SERVO_ARM", "catalog_row": "P-testmotion-1", "catalog_id": None, "role": "actuator",
    "name": "Test servo arm (unit-test row)",
    "reference_product": {"maker": "test", "mpn_or_model": "test", "product_url": None, "datasheet_url": None},
    "dims_mm": {"x": 22.8, "y": 12.2, "z": 22.7}, "pcb_thickness_mm": None,
    "mount": {"type": "tabs", "holes": [], "hole_origin": "n/a", "notes": "test"},
    "pins": [{"name": "GND", "func": "ground"}, {"name": "5V", "func": "power"}, {"name": "PWM", "func": "signal"}],
    "module_connector": {"type": "header_2.54", "pin_order": ["GND", "5V", "PWM"], "location": "servo plug"},
    "backplane_harness": {"backplane_connector": "JST-XH 2.5 3p", "wires": [], "length_mm": 150},
    "aperture": {"needs": "shaft_hole", "size_mm": {"w": 8, "h": 8}},
    "electrical": {"supply_v": "5", "logic_v": "5", "current_ma_typ": 150, "current_ma_max": 700, "bus": "pwm"},
    "firmware": {"arduino_libraries": [], "driver_notes": ""}, "kicad": {"backplane_footprint": "NONE", "notes": ""},
    "sourcing_in": [], "price_inr_planning": [100, 250], "evidence": ["unit test row"], "uncertain": ["unit test row"],
    "merge": {"role": "servo", "prefix": "SRV", "placement": "feature", "designable": True,
              "port": {"port": "led", "slots": ["J_LED"], "cable": {"family": "JST-XH 2.5", "pins": 3}, "supported": True,
                       "why": "the only 5 V port"},
              "prewire": None},
}
SOIL = {
    "id": "TEST_SOIL_PROBE", "catalog_row": "P-testsense-1", "catalog_id": None, "role": "sensor",
    "name": "Test soil probe (unit-test row)",
    "reference_product": {"maker": "test", "mpn_or_model": "test", "product_url": None, "datasheet_url": None},
    "dims_mm": {"x": 98.0, "y": 23.0, "z": 4.0}, "pcb_thickness_mm": 1.6,
    "mount": {"type": "none", "holes": [], "hole_origin": "n/a", "notes": "test"},
    "pins": [{"name": "AOUT", "func": "analog"}, {"name": "VCC", "func": "power"}, {"name": "GND", "func": "ground"}],
    "module_connector": {"type": "header_2.54", "pin_order": ["AOUT", "VCC", "GND"], "location": "header"},
    "backplane_harness": {"backplane_connector": "JST-PH 2.0 3p", "wires": [], "length_mm": 150},
    "aperture": {"needs": "cable_exit", "size_mm": {"w": 6, "h": 6}},
    "electrical": {"supply_v": "3.3-5.5", "logic_v": "3.3", "current_ma_typ": 5, "current_ma_max": 5, "bus": "analog"},
    "firmware": {"arduino_libraries": [], "driver_notes": ""}, "kicad": {"backplane_footprint": "NONE", "notes": ""},
    "sourcing_in": [], "price_inr_planning": [55, 130], "evidence": ["unit test row"], "uncertain": ["unit test row"],
    "manual": {"word": "soil probe"},
    "merge": {"role": "soil_moisture", "prefix": "SOIL", "placement": "feature", "designable": True,
              "port": {"port": "io", "slots": ["J_IO1", "J_IO2", "J_IO3"], "cable": {"family": "JST-PH 2.0", "pins": 3},
                       "supported": True, "why": "analog signal on a simple port"},
              "prewire": None},
}
MOTION_RULE = "Test motion rule: a servo arm waves on a tap."
SENSE_RULE = "Test sensing rule: a soil probe watches a pot."


@pytest.fixture(scope="module")
def studio(tmp_path_factory):
    root = tmp_path_factory.mktemp("catstudio")
    for d in ("engine", "examples", "docs", "landing"):
        if (STUDIO / d).exists():
            os.symlink(STUDIO / d, root / d)
    lib = root / "library"
    (lib / "references").mkdir(parents=True)
    for f in ("modules-a.json", "modules-b.json", "modules-foundation.json", "backplane-v1.json", "sourcing-updates.json"):
        if (LIB / f).exists():
            shutil.copy2(LIB / f, lib / f)
    if (LIB / "references" / "index.json").exists():
        shutil.copy2(LIB / "references" / "index.json", lib / "references" / "index.json")
    shutil.copytree(LIB / "tools", lib / "tools", ignore=shutil.ignore_patterns("__pycache__"))
    packs = lib / "packs"
    packs.mkdir()
    (packs / "testmotion.json").write_text(json.dumps({"pack": "testmotion", "rows": [SERVO], "prompt_rules": [MOTION_RULE]}))
    (packs / "testsense.json").write_text(json.dumps({"pack": "testsense", "rows": [SOIL], "prompt_rules": [SENSE_RULE]}))
    ex = packs / "examples" / "testsense" / "tomato-pot-watcher"
    ex.mkdir(parents=True)
    spec = json.loads((STUDIO / "examples" / "desk-buddy" / "spec.json").read_text())
    spec.update(name="Tomato Pot Watcher", slug="tomato-pot-watcher", tagline="Watches the soil of a tomato pot")
    (ex / "spec.json").write_text(json.dumps(spec))
    env = {k: v for k, v in os.environ.items() if not k.startswith("GS_PACKS")}
    r = subprocess.run([sys.executable, "-I", str(lib / "tools" / "merge_modules.py")], capture_output=True, text=True,
                       env=env, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    return root


def _prompt(studio, monkeypatch, packs: str, wish: str | None = None):
    from app.agent.prompt import PromptBuilder
    from app.engine_bridge import EngineBridge

    monkeypatch.delenv("GS_PACKS_OFF", raising=False)
    monkeypatch.setenv("GS_PACKS_ENABLED", packs)
    monkeypatch.setenv("GS_LIBRARY", str(studio / "library"))
    s = make_settings(studio / "run", studio_root=studio)
    pb = PromptBuilder(EngineBridge(s), s)
    return pb, pb.system_prompt(wish)


def test_packs_off_prompt_has_no_pack_text(studio, monkeypatch):
    pb, text = _prompt(studio, monkeypatch, "")
    assert MOTION_RULE not in text and SENSE_RULE not in text and "added this week" not in text
    assert "TEST_SERVO_ARM" not in text and "TEST_SOIL_PROBE" not in text and "Tomato Pot Watcher" not in text
    assert "One small servo arm is possible" not in text and "No motors or other actuators." in text
    assert "servo_wave_deg" not in text
    assert "We cannot build: motors or other moving parts, batteries, cameras, mains power, heaters" in text
    assert "a servo arm" not in text and "soil probe" not in text.lower() and "push messages" not in text
    assert "it does not keep a timetable of its own yet" in text and '"schedule"' not in text
    ports = next(l for l in text.splitlines() if l.startswith("- Ports (the validator rejects more): "))
    assert "3 simple ports shared by " in ports and "buttons" in ports and ports.endswith("one 5 V port for an LED ring or pixel.")
    assert "SERVO_SG90_TOWERPRO" not in text  # the bare SG90 row is no longer offered (E1.6)
    assert not any("packs" in p.parts for p in pb.example_paths("tomato soil"))


def test_packs_on_prompt_shows_rules_parts_and_matching_examples(studio, monkeypatch):
    pb, text = _prompt(studio, monkeypatch, "testmotion,testsense,schedule", wish="A tomato pot that watches its soil")
    assert "# Parts and objects added this week" in text and f"- {MOTION_RULE}" in text and f"- {SENSE_RULE}" in text
    assert "TEST_SERVO_ARM |" in text and "TEST_SOIL_PROBE |" in text
    assert "One small servo arm is possible; it uses the only 5 V port" in text and "No motors or other actuators." not in text
    assert "servo_wave_deg 10-60 (35)" in text and "servo_hold_s 1-30 (3)" in text  # C-fw settings, exact bounds
    assert "We cannot build: batteries, cameras, mains power, heaters" in text and "motors or other moving parts" not in text
    assert "one 5 V port for an LED ring or pixel or a servo arm, so a design has only one of them" in text
    assert "a soil probe" in text and "there are no push messages" in text
    assert '"time": "HH:MM" (24-hour)' in text
    paths = pb.example_paths("A tomato pot that watches its soil")
    assert paths[0].parent.name == "bedtime-robot" and paths[1].parent.name == "tomato-pot-watcher"
    assert "## Tomato Pot Watcher (" in text


def test_refusal_sentence_names_the_never_list(studio, monkeypatch):
    for packs in ("", "testmotion,testsense"):
        _, text = _prompt(studio, monkeypatch, packs)
        line = next(l for l in text.splitlines() if "We cannot build:" in l)
        for word in ("cameras", "batteries", "mains power", "heaters", "medical devices", "vehicles"):
            assert word in line, (packs, word)
        assert "No mains power and no batteries." in text  # the safety rule stays as it was


def test_wish_of_takes_the_users_first_words():
    from app.agent.prompt import wish_of

    p = {"chat": [{"role": "assistant", "kind": "text", "text": "Hi"},
                  {"role": "user", "kind": "text", "text": "A plant buddy for my desk"},
                  {"role": "user", "kind": "choices", "text": "x"},
                  {"role": "user", "kind": "text", "text": "Money plant"},
                  {"role": "user", "kind": "text", "text": "ignored third"}]}
    assert wish_of(p) == "A plant buddy for my desk Money plant" and wish_of(None) == ""


def test_build_summary_says_the_kind():
    from app.summary import build_summary

    payload, _, _ = build_summary("Pen Cup", {"state": "done"}, {"printed": [{"name": "PRINT_Body"}]}, None, None, None, None,
                                  {"spec": {"kind": "printed", "name": "Pen Cup"}})
    assert payload["kind"] == "printed" and "no electronics" in payload["made"][0]
    payload, _, _ = build_summary("Owl", {"state": "done"}, {"printed": [{"name": "PRINT_Shell"}]}, None, None, None, None,
                                  {"spec": {"name": "Owl"}})
    assert payload["kind"] == "gadget" and "printed shell" in payload["made"][0]


def test_printed_size_range_only_with_the_printed_pack(studio, monkeypatch):
    _, off = _prompt(studio, monkeypatch, "")
    assert "20-260 mm" not in off and "Every axis 60-260 mm;" in off
    _, on = _prompt(studio, monkeypatch, "printed")
    assert "Every axis 60-260 mm (a printed object: 20-260 mm);" in on
    assert "depth included (a printed object with no electronics: 20-260 mm)." in on
