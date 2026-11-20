"""Catalog package A: printed-only objects through the CAD stage entry point (gstudio.cad.run).

The two printed pack examples (library/packs/examples/printed/) are built from their spec.json as plain dicts (the CAD
reads specs duck-typed, cad/params.py), with the empty electrical.json the pipeline writes for printed designs. Checks: one
printed solid exported as PRINT_Shell.stl, no cartridge/cover/kits/cables/coupons, no collisions, the open top and the
drain hole of the planter, feet seats, and the not-applicable checks. Heavy (CadQuery): run on the VM.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

STUDIO = Path(__file__).resolve().parents[4]
EX = STUDIO / "library" / "packs" / "examples" / "printed"
SLUGS = ["card-qr-stand", "pen-cup-planter"]


def _ctx(slug: str, out: Path):
    from gstudio.library import Library
    from gstudio.printed import empty_electrical

    spec = json.loads((EX / slug / "spec.json").read_text())
    msgs = []
    return SimpleNamespace(spec=spec, lib=Library(), out=out, options={"cad_renders": False}, artifacts={},
                           electrical=empty_electrical(spec), warnings=[],
                           progress=lambda s, m, p=None: msgs.append((s, m, p)), warn=lambda s, m: msgs.append((s, m, None)))


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from gstudio.cad import run

    res = {}
    for slug in SLUGS:
        out = tmp_path_factory.mktemp(slug)
        summary = run(_ctx(slug, out))
        res[slug] = (out, summary)
    return res


@pytest.mark.parametrize("slug", SLUGS)
def test_printed_object_exports_one_print(built, slug):
    out, s = built[slug]
    comps = json.loads((out / "cad/components.json").read_text())
    prints = sorted(n for n, c in comps.items() if c["kind"] == "PRINT")
    assert prints == ["PRINT_Shell"], prints
    assert (out / "print/PRINT_Shell.stl").is_file()
    assert not list((out / "print").glob("TEST_*.stl")), "no fit coupon for a printed-only object"
    purchased = sorted(n for n, c in comps.items() if c["kind"] == "PURCHASE")
    assert purchased == ["PURCHASE_Feet"], purchased  # both examples stand on feet; nothing electronic
    assert not any(c["kind"] == "REFERENCE" for c in comps.values()), "no cable envelopes"
    params = json.loads((out / "cad/params.json").read_text())
    assert params["kind"] == "printed" and params["mount"] == {"kind": "desk"}


@pytest.mark.parametrize("slug", SLUGS)
def test_printed_checks_pass_and_skip_what_does_not_apply(built, slug):
    out, _ = built[slug]
    checks = {c["id"]: c for c in json.loads((out / "cad/digital-checks.json").read_text())["checks"]}
    for cid in ("collisions", "print_single_solid", "print_watertight", "bed_fit", "occ_validity", "frame_zmin", "min_wall"):
        assert checks[cid]["status"] == "passed", (cid, checks[cid]["detail"])
    for cid in ("fit_coupon", "power_entry"):
        assert checks[cid]["status"] == "not_run" and "not applicable" in checks[cid]["detail"], cid
    gate = [c for c in checks.values() if c["status"] == "failed"]
    assert not gate, [(c["id"], c["detail"][:200]) for c in gate]


def test_planter_is_open_with_a_drain_hole(built):
    out, s = built["pen-cup-planter"]
    import trimesh

    m = trimesh.load_mesh(out / "print/PRINT_Shell.stl")
    lo, hi = m.bounds
    cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
    # a vertical ray down the axis from above passes the open top and the drain hole: no hit at all
    hits, _, _ = m.ray.intersects_location([[cx, cy, hi[2] + 10]], [[0, 0, -1]])
    assert len(hits) == 0, hits
    # a ray 20 mm off the axis hits the floor (open top, closed floor around the drain hole)
    hits, _, _ = m.ray.intersects_location([[cx + 20, cy, hi[2] + 10]], [[0, 0, -1]])
    assert len(hits) >= 1


def test_card_stand_is_solid(built):
    out, _ = built["card-qr-stand"]
    import trimesh

    m = trimesh.load_mesh(out / "print/PRINT_Shell.stl")
    assert m.is_watertight
    spec = json.loads((EX / "card-qr-stand" / "spec.json").read_text())
    pts = spec["body"]["outline"]
    area = abs(sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))) / 2
    # printed solid, not a hollow shell: about outline area x depth of material (a 2.4 mm shell would be under 45 %)
    assert m.volume > 0.75 * area * spec["body"]["depth"], (m.volume, area)
