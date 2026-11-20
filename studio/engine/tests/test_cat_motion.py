"""Motion pack (CATALOG-PLAN package B): a servo arm is a feature on the only 5 V port.

- a servo feature lands on J_LED with an XH-3 harness and its kit footprint (case behind the wall, arm sweep outside);
- the power budget uses the row's current (800 mA stall for MG90S, labelled estimate);
- a servo plus an LED ring fails `slot_full` with a fix; a child-directed design with a servo fails `actuator_child`;
- a button inside the arm's sweep is a kit conflict; the pack is invisible when it is off;
- both pack examples normalize with the pack on.

Needs the motion rows merged into library/modules.json (python3 -I library/tools/merge_modules.py); skipped otherwise.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from gstudio.cad import footprint as FP
from gstudio.electrical import build_electrical
from gstudio.library import Library
from gstudio.normalize import normalize

STUDIO = Path(__file__).resolve().parents[2]
EXAMPLES = STUDIO / "library" / "packs" / "examples" / "motion"
SERVOS = ("SERVO_SG90_ARM", "SERVO_MG90S_ARM")


@pytest.fixture
def motion_on(monkeypatch):
    monkeypatch.delenv("GS_PACKS_OFF", raising=False)
    monkeypatch.setenv("GS_PACKS_ENABLED", "motion")
    lib = Library()
    if not all(lib.has(s) for s in SERVOS):
        pytest.skip("motion pack rows are not merged into library/modules.json (run library/tools/merge_modules.py)")
    return lib


def example(slug: str, settings: bool = True) -> dict:
    d = json.loads((EXAMPLES / slug / "spec.json").read_text())
    if not settings:  # the servo settings need the firmware settings contract (hack/cat-c-fw)
        d["behaviour"]["settings"] = {}
    return d


def _norm(d, lib):
    sp, rep = normalize(copy.deepcopy(d), lib)
    return sp, rep


def _codes(rep) -> set:
    return {e.get("code") for e in rep.get("errors", [])}


def test_servo_feature_on_j_led_with_xh3_harness(motion_on):
    lib = motion_on
    sp, rep = _norm(example("waving-desk-buddy", settings=False), lib)
    assert rep["ok"], rep["errors"]
    srv = next(i for i in sp.derived.instances if i.module == "SERVO_SG90_ARM")
    assert srv.slot == "J_LED" and srv.role == "servo" and srv.ref.startswith("SRV")
    h = next(h for h in sp.derived.harness if h.ref == srv.ref)
    assert h.slot == "J_LED" and h.kind == "jst_xh" and h.pins == 3
    el = build_electrical(sp, lib)
    assert el["gpio"]["LED_DIN"] == 2 and "J_LED" in el["slots_used"]
    # the three servo wires reach the three J_LED pins (GND, 5V, DIN)
    wires = {(w.get("module_pin"), w.get("slot_pin")) for w in (h.wires or [])} if getattr(h, "wires", None) else set()
    assert not wires or wires == {("GND", "GND"), ("V+", "5V"), ("SIG", "DIN")}, wires


def test_power_budget_uses_the_row_current(motion_on):
    lib = motion_on
    d = example("pet-feeder-flap", settings=False)
    sp, rep = _norm(d, lib)
    assert rep["ok"], rep["errors"]
    p = build_electrical(sp, lib)["power"]
    row = next(r for r in p["budget"] if r["module"] == "SERVO_MG90S_ARM")
    assert row["ma_max"] == 800.0 and row["ma_typ"] == 130.0 and row["basis"].startswith("estimate")
    d["features"][2]["module"] = "SERVO_SG90_ARM"
    sp, rep = _norm(d, lib)
    row = next(r for r in build_electrical(sp, lib)["power"]["budget"] if r["module"] == "SERVO_SG90_ARM")
    assert row["ma_max"] == 700.0 and row["ma_typ"] == 150.0


def test_servo_and_led_ring_do_not_share_the_5v_port(motion_on):
    lib = motion_on
    d = example("waving-desk-buddy", settings=False)
    d["features"].append({"id": "ring", "module": "LED_RING_12_WS2812_50MM", "face": "front", "at": [0, 40], "label": "Light"})
    sp, rep = _norm(d, lib)
    assert not rep["ok"] and "slot_full" in _codes(rep)
    err = next(e for e in rep["errors"] if e.get("code") == "slot_full")
    assert err.get("fix")


def test_no_servo_in_a_child_directed_design(motion_on):
    lib = motion_on
    d = example("waving-desk-buddy", settings=False)
    d["audience"] = {"child_directed": True, "age_years": 7}
    sp, rep = _norm(d, lib)
    assert not rep["ok"] and "actuator_child" in _codes(rep)


def test_button_in_the_arm_sweep_is_a_conflict(motion_on):
    lib = motion_on
    d = example("pet-feeder-flap", settings=False)
    feats = d["features"]
    flap = next(f for f in feats if f["id"] == "food_flap")
    btn = next(f for f in feats if f["id"] == "fed_button")
    boxes = FP.kit_footprint(flap, None, lib_get=lib.get)
    sweep = next(b for b in boxes if b["kind"] == "clearance")
    assert sweep["part"].startswith("PRINT_ServoArm") and sweep["depth"][1] < 0  # outside the wall
    case = next(b for b in boxes if b["what"] == "servo case")
    assert case["u"] == [round(flap["at"][0] - 11.2, 3), round(flap["at"][0] + 11.2, 3)]  # MG90S case centred on `at`
    assert not FP.layout_conflicts(feats, margin=0.5, lib_get=lib.get)  # the example keeps the button out of the sweep
    moved = copy.deepcopy(feats)
    next(f for f in moved if f["id"] == "fed_button")["at"] = [flap["at"][0] + 30, flap["at"][1] + 10]
    conf = FP.layout_conflicts(moved, margin=0.5, lib_get=lib.get)
    assert any({c["a"], c["b"]} == {"food_flap", "fed_button"} for c in conf)
    assert btn["at"] != moved[1]["at"]


def test_pack_off_hides_the_servo_rows(monkeypatch):
    monkeypatch.setenv("GS_PACKS_ENABLED", "motion")
    monkeypatch.setenv("GS_PACKS_OFF", "motion")
    lib = Library()
    assert not lib.has("SERVO_SG90_ARM")
    sp, rep = _norm(example("waving-desk-buddy", settings=False), lib)
    assert not rep["ok"]


@pytest.mark.parametrize("slug", ["waving-desk-buddy", "pet-feeder-flap"])
def test_pack_examples_normalize(motion_on, slug):
    sp, rep = _norm(example(slug), motion_on)  # with their servo and schedule settings (C-fw settings contract)
    assert rep["ok"], rep["errors"]
    assert any(i.role == "servo" for i in sp.derived.instances)
    assert not any(i.role == "led" for i in sp.derived.instances)


def test_light_engine_normalizes_a_servo_design(motion_on):
    """The web image (no CadQuery, no firmware/manual/order/pcb) validates a servo design with the real kit keep-out."""
    spec = EXAMPLES / "waving-desk-buddy" / "spec.json"
    code = textwrap.dedent(f"""
        import json, sys
        for m in ("cadquery", "OCP", "gstudio.firmware", "gstudio.manual", "gstudio.order", "gstudio.pcb"):
            sys.modules[m] = None
        from gstudio.cad import footprint as FP
        from gstudio.library import Library
        from gstudio.normalize import normalize
        lib = Library()
        spec = json.load(open({str(spec)!r}))
        sp, rep = normalize(spec, lib)
        assert rep["ok"], rep["errors"]
        warn = " ".join(map(str, list(rep.get("warnings") or []) + list(sp.derived.warnings or [])))
        assert "fell back" not in warn, warn
        arm = next(f for f in sp.features if f.module == "SERVO_SG90_ARM")
        boxes = FP.kit_footprint(arm, None, lib_get=lib.get)
        assert any(b["what"] == "servo case" for b in boxes), boxes
        print("LIGHT_SERVO_OK")
    """)
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]), "GS_PACKS_ENABLED": "motion"}
    env.pop("GS_PACKS_OFF", None)
    r = subprocess.run([sys.executable, "-c", code], cwd=str(Path(__file__).resolve().parents[1]), env=env,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-4000:]
    assert "LIGHT_SERVO_OK" in r.stdout
