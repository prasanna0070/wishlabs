"""Wiring section + diagram against the real library, backplane-v1.json and electrical stage (no CAD needed).

Skipped when the foundation stages are not importable or an example spec does not validate yet.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

STUDIO = Path(__file__).resolve().parents[4]
EXAMPLES = sorted((STUDIO / "examples").glob("*/spec.json"))


def _prepared(spec_path: Path, out: Path):
    try:
        from gstudio.context import BuildContext
        from gstudio import electrical
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"foundation not importable: {exc}")
    try:
        ctx = BuildContext.prepare(spec_path, out)
        electrical.run(ctx)  # sets ctx.electrical and writes electrical.json
    except Exception as exc:
        pytest.skip(f"{spec_path.parent.name}: foundation stages failed: {exc}")
    return ctx


@pytest.mark.parametrize("spec_path", EXAMPLES, ids=[p.parent.name for p in EXAMPLES])
def test_wiring_from_real_backplane(spec_path, tmp_path):
    from gstudio.manual.data import load
    from gstudio.manual.diagram import draw_wiring
    from gstudio.manual.model import classify
    from gstudio.manual.wiring import build_wiring, harnesses

    ctx = _prepared(spec_path, tmp_path)
    data = load(ctx)
    assert data.backplane and data.backplane.source.endswith("backplane-v1.json")
    assert data.backplane.size and data.backplane.slots
    A = classify(data)
    w = build_wiring(data, A, {})
    elec = ctx.electrical
    n_conn = len(elec.get("connections") or [])
    assert len([c for c in w["connections"] if c["to"].count(".") >= 2]) >= n_conn
    for c in w["connections"]:
        assert c["kind"] in ("wire", "cable", "socket")
        assert c["from"].count(".") >= 1 and c["to"].startswith("BP1.")
    for c in [c for c in w["connections"] if c["kind"] == "cable"]:
        assert "pin" in c["note"]
    ids = [s["id"] for s in w["steps"]]
    assert len(ids) == len(set(ids)) and ids[0] == "meet-backplane"
    items = {p["item"] for p in w["parts"]}
    assert all(items)
    info = draw_wiring(data, A, tmp_path / "manual")
    assert info["positions"] and info["rows"] == sum(len(h.wires) for h in harnesses(data))
    root = ET.parse(tmp_path / "manual" / "wiring.svg").getroot()
    txt = " ".join(t.text or "" for t in root.iter("{http://www.w3.org/2000/svg}text"))
    for h in harnesses(data):
        assert h.slot in txt and h.ref in txt
    # every wire's GPIO in the table equals the electrical.json wire gpio (source-backed, QB-19)
    for inst in elec["instances"]:
        for wire in (inst.get("harness") or {}).get("wires", []):
            if wire.get("gpio") is not None:
                assert f"GPIO{wire['gpio']}" in txt


def test_parse_real_backplane():
    from gstudio.manual.data import parse_backplane, read_json

    raw = read_json(STUDIO / "library" / "backplane-v1.json")
    if not raw:
        pytest.skip("library/backplane-v1.json not present")
    bp = parse_backplane(raw, source="library/backplane-v1.json")
    assert bp.size and bp.size[0] > 0 and len(bp.holes) >= 4
    assert {"U1", "J_PWR"} <= set(bp.slots)
    assert bp.slots["J_PWR"].kind == "power" and bp.slots["U1"].kind == "socket"
    ports = [s for s in bp.slots.values() if s.kind == "jst_ph"]
    assert ports
    for s in ports:
        assert s.rect and s.pos, s.id
        assert s.pins and all(p.at for p in s.pins), s.id  # every port pad position is known
    for s in bp.slots.values():
        assert s.rect is None or (s.rect[0] < s.rect[1] and s.rect[2] < s.rect[3]), s.id
