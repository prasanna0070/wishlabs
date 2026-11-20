"""Sensing pack (CATALOG-PLAN package C): soil probe and float switch on the simple ports, shown by sensor_display, each
behind a strain-relieved cable exit. Needs the library regenerated with the pack (python3 -I library/tools/
gen_backplane_v1.py && python3 -I library/tools/merge_modules.py), as test_cat_packs.py does."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from gstudio.electrical import build_electrical
from gstudio.library import Library
from gstudio.normalize import normalize
from gstudio.spec import load_spec

STUDIO = Path(__file__).resolve().parents[2]
EXAMPLES = STUDIO / "library" / "packs" / "examples" / "sensing"
ENV = ("GS_PACKS_ENABLED", "GS_PACKS_OFF", "GS_LIBRARY")


@pytest.fixture()
def on(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GS_PACKS_ENABLED", "sensing")
    lib = Library()
    if not lib.has("SOIL_CAP_V12_ANALOG"):
        pytest.fail("SOIL_CAP_V12_ANALOG is not in modules.json: regenerate the library (gen_backplane_v1.py, merge_modules.py)")
    return lib


def _spec(slug: str) -> dict:
    return json.loads((EXAMPLES / slug / "spec.json").read_text(encoding="utf-8"))


def _norm(d: dict, lib):
    from gstudio.spec import Spec

    return normalize(Spec.model_validate(d), lib)


def _codes(rep) -> set:
    return {e.get("code") for e in rep.get("errors") or []}


@pytest.mark.parametrize("slug", ["plant-monitor", "tank-low-alarm"])
def test_examples_normalize_with_the_pack_on(slug, on):
    sp, rep = normalize(load_spec(EXAMPLES / slug / "spec.json"), on)
    assert rep["ok"], rep["errors"]
    mods = {i.module: i for i in sp.derived.instances}
    soil = mods["SOIL_CAP_V12_ANALOG"]
    assert soil.slot in ("J_IO1", "J_IO2", "J_IO3")
    h = next(h for h in sp.derived.harness if h.ref == soil.ref)
    assert h.match == "exact" and h.module.startswith("CABLE_JSTPH_3P_DUPONT1X3_") and h.module.endswith("_X321")


def test_soil_and_float_are_wired_pin_for_pin(on):
    sp, rep = normalize(load_spec(EXAMPLES / "tank-low-alarm" / "spec.json"), on)
    assert rep["ok"], rep["errors"]
    el = build_electrical(sp, on)
    nodes = {n: net["name"] for net in el["nets"] for n in net["nodes"]}
    for sid in el["slots_used"]:
        if sid == "U1":
            continue
        for p in on.slot(sid)["pins"]:
            if p.get("kind") == "nc":
                continue
            node = f"BP1.{sid}.{p['pin']}"
            assert node in nodes and nodes[node] == p["net"], node
    insts = {i["module"]: i for i in el["instances"]}
    soil, lvl = insts["SOIL_CAP_V12_ANALOG"], insts["FLOAT_SWITCH_VERTICAL_PP"]
    conns = el["connections"]
    sig = lambda ref, pin: next(c for c in conns if c["from"] == f"{ref}.{pin}")  # noqa: E731
    assert sig(soil["ref"], "AO")["net"] == f"IO{soil['slot'][-1]}_SIG"
    assert sig(soil["ref"], "VCC")["net"] == "+3V3" and sig(soil["ref"], "GND")["net"] == "GND"
    assert sig(lvl["ref"], "SIG")["net"] == f"IO{lvl['slot'][-1]}_SIG" and sig(lvl["ref"], "GND")["net"] == "GND"
    assert not any(c["from"] == f"{lvl['ref']}.3V3" for c in conns)  # dry contact: the 3V3 wire is not used
    # J_IO SIG pins are GPIO4/5/6, all ADC1 (readable with Wi-Fi on)
    assert el["gpio"][f"IO{soil['slot'][-1]}_SIG"] in (4, 5, 6)


def test_float_is_factory_prewired(on):
    m = on.get("FLOAT_SWITCH_VERTICAL_PP")
    assert (m.get("prewire") or {}).get("lead") and m.get("requires_soldering") is False
    sp, rep = normalize(load_spec(EXAMPLES / "tank-low-alarm" / "spec.json"), on)
    lvl = next(i for i in sp.derived.instances if i.module == "FLOAT_SWITCH_VERTICAL_PP")
    h = next(h for h in sp.derived.harness if h.ref == lvl.ref)
    assert h.match == "exact" and h.module_end_family == "factory_prewired"


def test_a_fourth_simple_port_part_is_refused(on):
    d = _spec("tank-low-alarm")  # buzzer + soil + float fill J_IO1-3
    d["features"].append({"id": "extra_button", "module": "BUTTON_TACTILE_KY004", "face": "left", "at": [0, 58], "label": "Extra"})
    _, rep = _norm(d, on)
    assert "slot_full" in _codes(rep)


def test_pack_off_hides_the_parts(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GS_PACKS_ENABLED", "")
    lib = Library()
    assert not lib.has("SOIL_CAP_V12_ANALOG") and not lib.has("FLOAT_SWITCH_VERTICAL_PP")
    assert "SOIL_CAP_V12_ANALOG" not in lib.summary_for_llm()
    _, rep = _norm(_spec("plant-monitor"), lib)
    assert not rep["ok"]
    monkeypatch.setenv("GS_PACKS_ENABLED", "sensing")
    monkeypatch.setenv("GS_PACKS_OFF", "sensing")  # the kill switch wins
    assert not Library().has("SOIL_CAP_V12_ANALOG")


def test_agent_sees_the_rows_and_rules_when_on(on):
    from gstudio import packs

    table = on.summary_for_llm()
    assert "SOIL_CAP_V12_ANALOG" in table and "FLOAT_SWITCH_VERTICAL_PP" in table and len(table) < 8000
    rules = dict(packs.prompt_rules()).get("sensing") or []
    text = " ".join(rules)
    assert len(rules) <= 4 and all(len(r) <= 400 for r in rules)
    for must in ("cannot water", "not a grow light", "no push messages", "cable exit"):
        assert must in text, must


def test_cable_exit_kit_is_registered_and_light():
    from gstudio.cad import kits_ext, kits_sensing

    assert "cq" not in vars(kits_sensing) and "cadquery" not in vars(kits_sensing)  # CadQuery only inside the kit fn
    assert kits_ext.classify_need("cable_exit") == "cable_exit"
    assert kits_ext.kit_fn("cable_exit") is kits_sensing.kit_cable_exit
    assert kits_ext.errors() == []
    boxes = kits_ext.footprint_boxes("cable_exit", {"id": "SOIL_CAP_V12_ANALOG"}, skin=2.4)
    for b in boxes:  # the whole kit stays inside the 20 x 12 x 15 mm keep-out the pack row declares (merge.face_mm)
        assert -10 <= b["u"][0] <= b["u"][1] <= 10 and -6 <= b["v"][0] <= b["v"][1] <= 6, b
        assert 0 <= b["depth"][0] <= b["depth"][1] <= 15, b
    assert any("lead" in b["what"] for b in boxes)


@pytest.mark.parametrize("slug,soil,level", [("plant-monitor", 1, 0), ("tank-low-alarm", 1, 1)])
def test_firmware_binds_the_sensing_pins(slug, soil, level, on, tmp_path):
    from gstudio.firmware import generate

    sp, rep = normalize(load_spec(EXAMPLES / slug / "spec.json"), on)
    assert rep["ok"], rep["errors"]
    el = build_electrical(sp, on)
    facts = generate(sp, el, tmp_path, lib=on)
    hw = (tmp_path / "firmware" / "include" / "hardware.h").read_text()
    assert f"#define GS_HAS_SOIL {soil}" in hw and f"#define GS_HAS_LEVEL {level}" in hw
    assert facts["template"] == "sensor_display" and not any("not driven" in w for w in facts["warnings"])
    cfg = (tmp_path / "firmware" / "include" / "config.h").read_text()
    assert "#define GS_CFG_SOIL_DRY_PERCENT 30" in cfg
