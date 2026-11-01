"""Fast unit tests for gstudio.pcb that need no KiCad install (sexpr, transforms, design model)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gstudio.pcb import sexpr
from gstudio.pcb.design import POWER_NETS, SUPPORT, build_design, load_backplane, resolve_node
from gstudio.pcb.symbols import transform

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
STUDIO = HERE.parents[3]
BACKPLANE = STUDIO / "library" / "backplane-v1.json"
META = {"name": "Unit", "slug": "unit", "revision": "A", "date": "2026-11-01", "engine_version": "test"}


def _el(name: str) -> dict:
    return json.loads((FIX / f"electrical.{name}.json").read_text())


def test_sexpr_roundtrip_keeps_quotes_and_numbers():
    src = '(kicad_sch (version 20250114) (title_block (title "A \\"q\\" b")) (at 1.27 -2.54 90) (x yes))'
    node = sexpr.parse(src)
    assert node[0] == "kicad_sch"
    title = sexpr.find(sexpr.find(node, "title_block"), "title")
    assert isinstance(title[1], sexpr.Q) and str(title[1]) == 'A "q" b'
    again = sexpr.parse(sexpr.dumps(node))
    assert again == node
    assert sexpr.num(1.27000001) == "1.27" and sexpr.num(-0.0) == "0"


@pytest.mark.parametrize("rot,expected", [(0, (0.0, -3.81)), (90, (-3.81, 0.0)), (180, (0.0, 3.81)),
                                          (270, (3.81, 0.0))])
def test_pin_transform_matches_kicad_rotation(rot, expected):
    # Device:R pin 1 sits at (0, 3.81) in library coordinates (Y up)
    x, y = transform(0.0, 3.81, rot)
    assert (round(x, 4) + 0.0, round(y, 4) + 0.0) == expected


def test_full_design_uses_backplane_positions_and_fixed_support_refs():
    bp = load_backplane(BACKPLANE)
    d = build_design(bp, _el("full"), META)
    refs = {p.ref for p in d.parts}
    # every physical slot part of every slot is placed at its backplane position
    for sid, s in bp["slots"].items():
        for r in s.get("parts") or [sid]:
            assert r in refs, r
            assert d.part(r).at == tuple(float(v) for v in bp["parts"][r]["at"])
    # support parts keep backplane-v1 refs and positions
    for r in ("R1", "R2", "F1", "D1", "C1", "C2", "U2"):
        assert r in refs and d.part(r).at == tuple(float(v) for v in bp["parts"][r]["at"])
    assert not d.omitted_slots


def test_minimal_design_omits_unused_slots_and_their_support():
    bp = load_backplane(BACKPLANE)
    d = build_design(bp, _el("minimal"), META)
    refs = {p.ref for p in d.parts}
    assert "J_A1" not in refs and "J_S1" not in refs and "J_MIC" not in refs
    assert "R7" not in refs and "C3" not in refs           # amp support only with A1
    assert {"U3", "R3", "C5", "R4", "R5"} <= refs          # LED buffer + I2C pull-ups are needed
    assert set(d.omitted_slots) == set(bp["slots"]) - set(_el("minimal")["slots_used"])
    # GPIOs whose slot is absent are left unconnected, not routed to nowhere
    u1a = d.part("J_U1A")
    assert u1a.pads["7"] == ""  # J1-7 = I2S_SPK_DIN, amp not fitted


def test_every_design_net_has_two_or_more_pads_except_rails():
    bp = load_backplane(BACKPLANE)
    for name in ("full", "minimal"):
        d = build_design(bp, _el(name), META)
        for net, nodes in d.nets().items():
            assert len(nodes) >= 2 or net in POWER_NETS, (name, net, nodes)


def test_resolve_node_accepts_slot_pin_names():
    bp = load_backplane(BACKPLANE)
    assert resolve_node("U1.J1-12", bp) == ("J_U1A", "12")
    assert resolve_node("U1.J3-19", bp) == ("J_U1B", "19")
    assert resolve_node("BP1.J_I2C1.3", bp) == ("J_I2C1", "3")
    assert resolve_node("A1.VO+", bp) == ("J_A1P", "1")
    assert resolve_node("DISP1.SDA", bp) is None  # module side, off the board


def test_support_table_only_names_parts_the_backplane_defines_when_needed():
    bp = load_backplane(BACKPLANE)
    for ref, sp in SUPPORT.items():
        if sp["when"] == "always" or set(sp["when"]) & set(bp["slots"]):
            assert ref in bp["parts"], f"{ref} missing from backplane-v1 parts"
