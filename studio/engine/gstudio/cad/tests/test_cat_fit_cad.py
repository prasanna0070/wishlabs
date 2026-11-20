"""Fit bug regression (orchestrator-to-d.md, 2026-11-20): the live focus-lamp spec (p_r4yikwxy497q) through the CAD stage.

At 159a94a the CAD moved the core cartridge 80 mm forward out of the shell (board origin y -117) and the build failed
9 collision pairs (PRINT_Shell / backplane 871 mm3, shell / core board 341 mm3). Now the keep-out is checked from its
own bottom and the board stays at the rear seam cap (y -37), where normalize put it: no cartridge collision, cables
routed. The live spec's top speaker (at y 20) also reached the rear cover (2 more pairs, VM 14:11); normalize now refuses
that (kit_seam), so this test builds the spec with the speaker where that fix puts it (y 8). See tests/test_cat_fit.py.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
pytest.importorskip("cadquery")

SPEC = Path(__file__).resolve().parent / "fixtures" / "focus-lamp-p_r4yikwxy497q.json"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from gstudio.cad import run
    from gstudio.context import BuildContext

    out = tmp_path_factory.mktemp("fit_focus_lamp")
    d = json.loads(SPEC.read_text())
    for f in d["features"]:  # the live top speaker reached the rear cover; normalize now refuses that (kit_seam) and asks
        if f["id"] == "sounder":  # for it further forward, so the board placement is tested on a spec it accepts
            f["at"] = [f["at"][0], 8.0]
    ctx = BuildContext.prepare(d, out, options={"cad_renders": False})
    summary = run(ctx)
    checks = json.loads((out / "cad" / "digital-checks.json").read_text())
    return summary, {c["id"]: c for c in checks["checks"]}


def test_cartridge_stays_inside_the_shell(built):
    summary, checks = built
    assert summary["info"]["cartridge"]["board_origin"][1] == pytest.approx(-37.0, abs=0.5)
    assert not [w for w in summary["warnings"] if "cartridge keep-out does not fit" in w]
    assert checks["cartridge_clearance"]["status"] == "passed", checks["cartridge_clearance"]["detail"]


def test_no_collision_with_the_cartridge(built):
    _, checks = built
    pairs = [set(p["parts"]) for p in (checks["collisions"].get("data") or {}).get("intersections", [])]
    assert not [p for p in pairs if p & {"PURCHASE_Backplane", "PURCHASE_CoreBoard"}], pairs
    assert checks["cable_routes"]["status"] == "passed", checks["cable_routes"]["detail"]
