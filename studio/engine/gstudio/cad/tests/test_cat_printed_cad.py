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
SLUGS = ["card-qr-stand", "pen-cup-planter", "owl-figurine"]


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


def test_pipeline_end_to_end(tmp_path):
    """PX as a test: the whole pipeline on the card stand with pack `printed` on (plan 0.4, package A tests). Run as the CLI
    in its own process: the CAD tests above leave OpenCascade threads in this process, and a stage child forked from it
    can deadlock (seen on the VM); the pipeline's context does not pickle for spawn."""
    import os
    import subprocess
    import sys

    out = tmp_path / "px-card"
    env = dict(os.environ, GS_PACKS_ENABLED="printed")
    r = subprocess.run([sys.executable, "-m", "gstudio", "build", str(EX / "card-qr-stand" / "spec.json"), str(out),
                        "--price-check", "cache_only", "--no-stories-audio", "--json"],
                       cwd=str(STUDIO / "engine"), env=env, capture_output=True, text=True, timeout=1500)
    assert r.returncode == 0, r.stderr[-3000:]
    s = json.loads(r.stdout)
    st = {k: v["state"] for k, v in s["stages"].items()}
    assert st["pcb"] == "skipped" and st["firmware"] == "skipped", st
    assert all(v == "done" for k, v in st.items() if k not in ("pcb", "firmware")), st
    assert s["buildable"] is True and s["state"] == "done"
    assert not (out / "pcb").exists() and not (out / "firmware").exists() and not (out / "SOFTWARE_SETUP.md").exists()
    q = json.loads((out / "order/quote.json").read_text())
    assert q["delivered_price_inr"]["pilot_one_off"] > 0
    assert q["lead_time_days"]["breakdown"]["pcb_fabrication_days"] is None
    assert not any("adapter" in r or "USB-C" in r for r in q["reasons"]), q["reasons"]
    assert any("printed-only design" in r for r in q["reasons"])
    ops = q["inputs"]["assembly_ops"]
    assert not {"core_prep", "flash_provision", "functional_test", "cable_route", "insert_heatset"} & set(ops), ops
    md = (out / "BUILD_GUIDE.md").read_text()
    assert "## Wiring" not in md and "no electronics" in md and "not food-safe" in md and "wiring table" not in md
    assert "Built the printed object" in json.dumps(s["stages"]["cad"])
    b = json.loads((out / "build.json").read_text())
    assert [x["id"] for x in b["steps"]] == ["check-parts", "print-parts", "fit-feet", "final-checks"]
    assert b["wiring"]["steps"] == [] and b["firmware"] is None and b["pcb"] is None
    dc = json.loads((out / "checks/digital-checks.json").read_text())
    assert dc["summary"]["failed"] == 0, [c for c in dc["checks"] if c["status"] == "failed"]


def test_cuts_make_pen_holes_and_a_card_slot(tmp_path):
    """Pack printed_cuts: the desk organiser's 3 top holes (55 mm deep) and slot (35 mm) are cut; the part stays one solid."""
    import trimesh

    from gstudio.cad import run
    from gstudio.library import Library
    from gstudio.printed import empty_electrical

    spec = json.loads((STUDIO / "library" / "packs" / "examples" / "printed_cuts" / "desk-organiser" / "spec.json").read_text())
    ctx = SimpleNamespace(spec=spec, lib=Library(), out=tmp_path, options={"cad_renders": False}, artifacts={},
                          electrical=empty_electrical(spec), warnings=[], progress=lambda s, m, p=None: None, warn=lambda s, m: None)
    run(ctx)
    checks = {c["id"]: c for c in json.loads((tmp_path / "cad/digital-checks.json").read_text())["checks"]}
    for cid in ("collisions", "print_single_solid", "print_watertight", "min_wall"):
        assert checks[cid]["status"] == "passed", (cid, checks[cid]["detail"])
    info = json.loads((tmp_path / "cad/cad-summary.json").read_text())["info"]["printed"]
    assert [c["kind"] for c in info["cuts"]] == ["hole", "hole", "hole", "slot"]
    m = trimesh.load_mesh(tmp_path / "print/PRINT_Shell.stl")
    top = m.bounds[1][2]
    for x, y, depth in ((-38, -14, 55), (-12, -14, 55), (14, -14, 55), (0, 18, 35), (40, 18, 35)):
        hits, _, _ = m.ray.intersects_location([[x, y, top + 5]], [[0, 0, -1]])
        assert len(hits), (x, y)
        z = max(h[2] for h in hits)
        assert abs((top - z) - depth) < 1.0, (x, y, top - z)
    hits, _, _ = m.ray.intersects_location([[50, -14, top + 5]], [[0, 0, -1]])  # no cut there: the top surface
    assert abs(max(h[2] for h in hits) - top) < 1.0


def test_printed_standalone_source_rebuilds(built, tmp_path):
    """The delivered cad/gadget_cad.py rebuilds a printed object from params.json without the engine."""
    import subprocess
    import sys

    out, _ = built["pen-cup-planter"]
    r = subprocess.run([sys.executable, str(out / "cad/gadget_cad.py"), "--out", str(tmp_path), "--no-checks"],
                       capture_output=True, text=True, timeout=900, cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr[-2000:]
    assert (tmp_path / "print/PRINT_Shell.stl").exists() and (tmp_path / "cad/assembly.step").exists()
    assert not list((tmp_path / "print").glob("TEST_*.stl"))


def test_concept_preview_shows_the_printed_object(tmp_path):
    """cad.preview (the concept shot) uses the same printed_shape as the build: the planter preview is open at the top."""
    from gstudio.cad import preview
    from gstudio.cad.build import build_shell_preview, printed_shape
    from gstudio.cad.params import build_params
    from gstudio.library import Library

    spec = json.loads((EX / "pen-cup-planter" / "spec.json").read_text())
    params = build_params(spec, Library(), None)
    pv = build_shell_preview(params)
    S, C_open, info, warns = printed_shape(params, pv)
    assert info["opening"] == "top" and info["drain_hole"] and C_open is not None and not warns
    assert S.Volume() < 0.4 * pv["outer"].Volume()  # a pot, not a solid plug
    r = preview(spec, Library(), tmp_path)
    assert not [w for w in r["warnings"] if "printed opening/cuts skipped" in w], r["warnings"]
    assert (tmp_path / "preview.glb").is_file()


def test_weld_slivers_closes_a_sub_micron_gap_only():
    """export.weld_slivers: a box whose top corner vertex is split by 0.3 um (open edges, as OCC left at a cut/fillet seam)
    is welded closed with the same volume; a real hole is left alone."""
    import numpy as np
    import trimesh

    from gstudio.cad.export import weld_slivers

    box = trimesh.creation.box((10, 10, 10))
    v = box.vertices.copy()
    f = box.faces.copy()
    k = int(np.argmax(v.sum(axis=1)))  # the (+,+,+) corner
    v = np.vstack([v, v[k] + [3e-4, 0, 0]])
    first = np.where((f == k).any(axis=1))[0][0]
    f[first][f[first] == k] = len(v) - 1  # one face uses the split copy: open edges around the corner
    m = trimesh.Trimesh(vertices=v, faces=f, process=False)
    assert not m.is_watertight
    w = weld_slivers(m)
    assert w is not None and w.is_watertight and abs(w.volume - 1000.0) < 1e-3
    holed = trimesh.Trimesh(vertices=box.vertices, faces=box.faces[2:], process=False)  # a whole side missing
    assert weld_slivers(holed) is None  # filling a whole missing side would change the volume: refused
