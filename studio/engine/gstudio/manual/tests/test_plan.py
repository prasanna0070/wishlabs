"""Step, wiring and tests generation against the fixture (no rendering)."""

import re

from gstudio.manual.data import load, parse_color
from gstudio.manual.finals import build_tests
from gstudio.manual.model import classify
from gstudio.manual.steps import plan
from gstudio.manual.wiring import build_wiring


def _plan(ctx):
    data = load(ctx)
    A = classify(data)
    return data, A, plan(A)


def test_parse_color_variants():
    assert parse_color("#FF0000") == (1.0, 0.0, 0.0)
    assert parse_color([255, 0, 0]) == (1.0, 0.0, 0.0)
    assert parse_color([0.5, 0.5, 0.5, 1.0]) == (0.5, 0.5, 0.5)


def test_classification(minibot_ctx):
    data, A, _ = _plan(minibot_ctx)
    assert A.shell.name == "PRINT_Shell"
    assert [c.name for c in A.covers] == ["PRINT_RearCover"]
    assert A.open_dir == (0.0, 1.0, 0.0)
    assert A.backplane.name == "PURCHASE_Backplane" and A.core.name == "PURCHASE_CoreBoard"
    assert {c.name for c in A.cartridge_mods} == {"PURCHASE_Amp", "PURCHASE_MicroSD"}
    roles = {g.role: g for g in A.features}
    assert set(roles) == {"speaker", "display", "button", "led", "mic"}
    assert [c.name for c in roles["speaker"].prints] == ["PRINT_SpeakerRing"]
    assert [c.name for c in roles["speaker"].fasteners] == ["PURCHASE_SpeakerScrews"]
    assert [c.name for c in roles["led"].prints] == ["PRINT_LedDiffuser"]
    assert all(g.normal == (0.0, -1.0, 0.0) for g in A.features)
    assert [c.name for c in A.cover_fasteners] == ["PURCHASE_CoverScrews"]
    assert [c.name for c in A.cartridge_fasteners] == ["PURCHASE_CartridgeScrews"]


def test_steps_schema_and_flow(minibot_ctx):
    data, A, steps = _plan(minibot_ctx)
    ids = [s.id for s in steps]
    assert len(ids) == len(set(ids))
    for s in steps:
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", s.id), s.id
        assert s.title and s.expected and s.stop
        assert s.instruction.startswith("**Use:**") and "\n\n**Tools:**" in s.instruction
        assert s.parts, f"{s.id} has empty parts"
        assert s.camera and len(s.camera["position"]) == 3 and len(s.camera["target"]) == 3
    order = ["check-parts", "fit-coupon", "print-parts", "install-inserts", "assemble-cartridge", "mount-cartridge",
             "mount-speaker", "mount-eyes", "mount-story-button", "mount-night-light", "mount-mic", "plug-harnesses",
             "route-cables", "flash-and-test", "close-cover", "final-checks"]
    assert ids == order


def test_every_physical_part_is_used_in_a_step(minibot_ctx):
    data, A, steps = _plan(minibot_ctx)
    used = {p for s in steps for p in s.parts}
    for c in data.comps.values():
        if c.kind in ("PRINT", "PURCHASE"):
            assert c.name in used, c.name


def test_no_invented_numbers_in_text(minibot_ctx):
    """Every 'N mm' in step text must come from saved data (cable names, labels, CAD route lengths)."""
    data, A, steps = _plan(minibot_ctx)
    allowed = set()
    for c in data.comps.values():
        allowed |= set(re.findall(r"\d+(?:\.\d+)?", c.label + " " + data.module_name(c)))
    for sl in data.backplane.slots.values():
        allowed |= set(re.findall(r"\d+(?:\.\d+)?", sl.footprint or ""))
    for r in data.routes:
        allowed |= set(re.findall(r"\d+", r.cable or "")) | {str(round(r.length_mm))}
    for inst in data.instances:
        allowed |= set(re.findall(r"\d+", str((inst.get("harness") or {}).get("cable", ""))))
        allowed |= {str((inst.get("harness") or {}).get("length_mm"))}
    for s in steps:
        for num in re.findall(r"(\d+(?:\.\d+)?)\s*mm", s.instruction + s.expected):
            assert num in allowed, (s.id, num)


def test_wiring_section(minibot_ctx):
    data, A, _ = _plan(minibot_ctx)
    w = build_wiring(data, A, {c.name: c.bbox for c in data.comps.values() if c.bbox})
    assert w["board"] == "esp32-s3-devkitc-1"
    ids = [p["id"] for p in w["parts"]]
    assert len(ids) == len(set(ids)) and {"U1", "BP1", "DISP1", "SPK1", "AMP1", "SD1"} <= set(ids)
    n_wires = sum(len((i.get("harness") or {}).get("wires", [])) for i in data.electrical["instances"])
    cables = [c for c in w["connections"] if c["kind"] == "cable"]
    assert len(cables) == n_wires
    sda = next(c for c in cables if c["from"] == "DISP1.SDA")
    assert sda["to"] == "BP1.J_I2C1.3" and sda["gpio"] == 8 and "SDA" in sda["note"] and sda["color"] == "blue"
    assert {c["to"] for c in w["connections"] if c["kind"] == "socket"} == {"BP1.U1", "BP1.A1", "BP1.S1"}
    sids = [s["id"] for s in w["steps"]]
    assert sids[0] == "meet-backplane" and len(sids) == len(set(sids))
    assert {"wire-eyes", "wire-mic", "wire-story-button", "wire-night-light", "wire-speaker"} <= set(sids)
    for s in w["steps"]:
        for k in s["labels"]:
            assert k in data.anchors, k


def test_tests_section(minibot_ctx):
    data, A, _ = _plan(minibot_ctx)
    tests = build_tests(data, A)
    titles = [t["title"] for t in tests]
    assert len(titles) == len(set(titles))
    for t in tests:
        assert t["kind"] in ("physical", "digital") and t["status"] in ("passed", "failed", "not_run")
        if t["kind"] == "physical":
            assert t["status"] == "not_run"
