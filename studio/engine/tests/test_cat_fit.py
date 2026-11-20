"""Fit bug (orchestrator-to-d.md, 2026-11-20): normalize passed a shell the CAD could not fit the core cartridge into.

Live case focus-lamp (bench e2e1, project p_r4yikwxy497q, at 159a94a): silhouette 116 x 100 x 165 mm with a 16 mm
front round. normalize put the board at y -37 (rear seam cap). The CAD's keep-out check sampled 1 mm above the floor,
where the rounded front-bottom edge leaves no room for the 3 mm clearance, found no position, stepped the board forward
80 times and left it at y -117: 871 mm3 of PRINT_Shell / backplane, 341 mm3 shell / core board, 9 collisions.

Fix (both sides use one rule): the keep-out is checked from its own bottom (backplane-v1 keepout_bottom_above_floor
4.9 mm; below it only the printed bosses reach the floor) against the cavity WITH the silhouette's front and rear rounds
(envelope.rounded_extrusion_sdf; normalize ShellGeometry.fit_sdf); a cartridge that cannot fit is refused by normalize
with a concrete edge_round or depth fix; the CAD never moves the board out of the shell.
"""
import json
import math
from pathlib import Path

import pytest

from gstudio import envelope

SPEC = Path(__file__).resolve().parents[1] / "gstudio" / "cad" / "tests" / "fixtures" / "focus-lamp-p_r4yikwxy497q.json"


def test_rounded_extrusion_equals_the_sharp_one_away_from_the_rounds():
    for d2, y in ((-5.0, 0.0), (-30.0, -45.0), (3.0, 10.0), (-1.0, 49.0)):
        sharp = max(d2, abs(y) - 50.0)
        r = envelope.rounded_extrusion_sdf(d2, y, 100.0, 16.0, 6.0)
        if not (d2 + (16.0 if y < 0 else 6.0) > 0 and abs(y) - 50.0 + (16.0 if y < 0 else 6.0) > 0):
            assert r == pytest.approx(sharp)


def test_rounded_extrusion_takes_the_corner_the_sharp_one_misses():
    # 1 mm above the floor and 3 mm in front of the focus-lamp keep-out (y = -40): the sharp extrusion calls it 7.3 mm
    # inside the outer surface, the 16 mm front round puts it outside the 2.4 mm wall's inner surface
    d2, y = -3.4, -40.0
    sharp = max(d2, abs(y) - 50.0)
    rounded = envelope.rounded_extrusion_sdf(d2, y, 100.0, 16.0, 6.0)
    assert sharp <= -2.4 < rounded
    assert rounded == pytest.approx(math.hypot(16 - 3.4, 16 - 10) - 16)
    # 4.9 mm above the floor (the keep-out's real bottom, z = 2.4 + 4.9) the same y is inside the cavity
    assert envelope.rounded_extrusion_sdf(-7.3, y, 100.0, 16.0, 6.0) <= -2.4


def test_silhouette_rounds_mirror_the_cad():
    assert envelope.silhouette_rounds({"depth": 100, "edge_round": 16}) == (16.0, 6.0)
    assert envelope.silhouette_rounds({"depth": 20, "edge_round": 16}) == (8.0, 6.0)
    assert envelope.silhouette_rounds({"depth": 100, "edge_round": 4}) == (4.0, 4.0)


def _normalize(d):
    from gstudio.library import Library
    from gstudio.normalize import ShellGeometry, normalize
    from gstudio.spec import load_spec

    if not hasattr(ShellGeometry, "fit_sdf"):
        pytest.skip("normalize side of the fit fix not merged yet (hand-off d-to-e.md item 7)")
    return normalize(load_spec(d), Library(), raise_on_error=False)


def test_focus_lamp_cartridge_stays_at_the_rear_seam_cap():
    d = json.loads(SPEC.read_text())
    sp, rep = _normalize(d)
    assert not [e for e in rep["errors"] if e.get("code") == "cartridge_fit"], rep["errors"]
    cart = sp.derived.cartridge
    origin = cart["board_origin"] if isinstance(cart, dict) else cart.board_origin
    assert origin[1] == pytest.approx(-37.0, abs=0.01)  # where the live build's normalize put it; the CAD now agrees


def test_a_front_round_that_reaches_the_board_is_refused_with_a_concrete_fix():
    d = json.loads(SPEC.read_text())
    d["body"]["edge_round"] = 25.0  # the spec maximum: its band reaches the keep-out even 4.9 mm above the floor
    _, rep = _normalize(d)
    errs = [e for e in rep["errors"] if e.get("code") == "cartridge_fit"]
    assert errs, rep["errors"]
    fix = errs[0].get("fix") or ""
    assert "body.edge_round to" in fix and "body.depth at least" in fix


def test_focus_lamp_top_speaker_reaching_the_rear_cover_is_refused_and_the_fix_works():
    import re

    from gstudio import normalize as N

    if not hasattr(N, "kit_seam_conflicts"):
        pytest.skip("normalize seam check not merged yet (hand-off d-to-e.md item 7b)")
    d = json.loads(SPEC.read_text())
    _, rep = _normalize(d)
    seam = [e for e in rep["errors"] if e.get("code") == "kit_seam"]
    assert len(seam) == 1 and seam[0]["msg"].startswith("sounder:"), rep["errors"]
    at1 = float(re.search(r"at\[1\] <= (-?\d+)", seam[0]["fix"]).group(1))
    k = next(i for i, f in enumerate(d["features"]) if f["id"] == "sounder")
    d["features"][k]["at"] = [d["features"][k]["at"][0], at1]
    _, rep2 = _normalize(d)
    assert not [e for e in rep2["errors"] if e.get("code") == "kit_seam"], rep2["errors"]
