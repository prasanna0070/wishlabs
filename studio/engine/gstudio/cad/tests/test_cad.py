"""CAD stage tests. Heavy: run on the VM (tools/vm.sh run 'cd engine && python -m pytest gstudio/cad/tests -q').

The three examples are built once per session in parallel worker processes through the real stage entry point
gstudio.cad.run(ctx); the tests then inspect the files they wrote.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

STUDIO = Path(__file__).resolve().parents[4]
EXAMPLES = ["bedtime-robot", "cloud-lamp", "desk-buddy"]
OUT_ROOT = Path(os.environ.get("GS_CAD_TEST_OUT", STUDIO / "examples"))


def _build(name: str) -> dict:
    sys.path.insert(0, str(STUDIO / "engine"))
    from gstudio.cad import run
    from gstudio.context import BuildContext

    out = OUT_ROOT / name / "_out" if OUT_ROOT == STUDIO / "examples" else OUT_ROOT / name
    t = time.time()
    ctx = BuildContext.prepare(STUDIO / "examples" / name / "spec.json", out, options={"cad_renders": False})
    s = run(ctx)
    s["_out"] = str(out)
    s["_seconds"] = round(time.time() - t, 1)
    return s


@pytest.fixture(scope="session")
def built():
    with ProcessPoolExecutor(max_workers=3) as ex:
        res = dict(zip(EXAMPLES, ex.map(_build, EXAMPLES)))
    return res


@pytest.mark.parametrize("name", EXAMPLES)
def test_example_builds(built, name):
    s = built[name]
    out = Path(s["_out"])
    for rel in ["cad/assembly.step", "cad/assembly.glb", "cad/components.json", "cad/anchors.json", "cad/cable-routes.json",
                "cad/params.json", "cad/gadget_cad.py", "cad/gadget_cad/build.py", "cad/draft.glb", "print/PRINT_Shell.stl",
                "print/TEST_FitCoupon.stl"]:
        assert (out / rel).exists(), rel
    comps = json.loads((out / "cad/components.json").read_text())
    for n in comps:
        assert (out / "cad/components" / f"{n}.brep").exists(), n
    assert s["timings"]["total"] < 300, s["timings"]  # REVIEW-1 adds fins, ear blends and checks; VM shared by 8 engineers


@pytest.mark.parametrize("name", EXAMPLES)
def test_shell_valid_and_closed(built, name):
    out = Path(built[name]["_out"])
    comps = json.loads((out / "cad/components.json").read_text())
    for n, c in comps.items():
        assert c["valid"], n
        if c["kind"] == "PRINT":
            assert c["solids"] == 1, (n, c["solids"])
    import cadquery as cq
    from OCP.BRepCheck import BRepCheck_Analyzer

    shell = cq.Shape.importBrep(str(out / "cad/components/PRINT_Shell.brep"))
    assert BRepCheck_Analyzer(shell.wrapped).IsValid()
    for sh in shell.Shells():
        assert sh.Closed(), "open shell in PRINT_Shell"


@pytest.mark.parametrize("name", EXAMPLES)
def test_print_stls_watertight(built, name):
    import trimesh

    out = Path(built[name]["_out"])
    files = sorted((out / "print").glob("*.stl"))
    assert files
    for f in files:
        m = trimesh.load_mesh(f)
        assert m.is_watertight, f.name
        assert len(m.split(only_watertight=False)) == 1, f.name
        assert abs(m.bounds[0][2]) < 1e-3, f.name
        ext = m.bounds[1] - m.bounds[0]
        assert ext[0] <= 250 and ext[1] <= 210 and ext[2] <= 210, (f.name, ext)


@pytest.mark.parametrize("name", EXAMPLES)
def test_zero_unintended_collisions(built, name):
    out = Path(built[name]["_out"])
    res = json.loads((out / "cad/digital-checks.json").read_text())
    col = next(c for c in res["checks"] if c["id"] == "collisions")
    assert col["data"]["intersections"] == [], col["data"]["intersections"][:5]
    assert col["data"]["pairs_bbox_overlap"] > 10


@pytest.mark.parametrize("name", EXAMPLES)
def test_glb_nodes_match_components(built, name):
    import pygltflib
    import trimesh

    out = Path(built[name]["_out"])
    comps = json.loads((out / "cad/components.json").read_text())
    g = pygltflib.GLTF2().load(str(out / "cad/assembly.glb"))
    roots = [g.nodes[i].name for i in g.scenes[0].nodes]
    assert sorted(roots) == sorted(comps), set(roots) ^ set(comps)
    assert len(roots) == len(set(roots))
    for n in g.nodes:
        assert n.mesh is not None
        if "#" in n.name:  # REVIEW-1 children: PRINT_Shell#decor[i] pick meshes, <display>#screen quads
            parent = next(p for p in g.nodes if p.children and g.nodes.index(n) in p.children)
            assert n.name.split("#")[0] == parent.name
    sc = trimesh.load(str(out / "cad/assembly.glb"))
    ext = sc.bounds[1] - sc.bounds[0]
    assert 0.03 < max(ext) < 0.3, ext  # metres, +Y up
    assert sc.bounds[0][1] > -1e-4  # sits on y = 0 (the table) in glTF


@pytest.mark.parametrize("name", EXAMPLES)
def test_checks_core_gates(built, name):
    out = Path(built[name]["_out"])
    res = json.loads((out / "cad/digital-checks.json").read_text())
    st = {c["id"]: c["status"] for c in res["checks"]}
    for k in ("occ_validity", "print_single_solid", "print_watertight", "brep_vs_stl_volume", "bed_fit", "frame_zmin",
              "role_prefix", "fastener_engagement", "fit_coupon"):
        assert st.get(k) == "passed", (k, st.get(k))


def test_components_have_spec_paths(built):
    out = Path(built["bedtime-robot"]["_out"])
    comps = json.loads((out / "cad/components.json").read_text())
    assert comps["PRINT_Shell"]["spec_path"] == "body"
    assert any(c["spec_path"] and c["spec_path"].startswith("features[") for c in comps.values())
    assert comps["PURCHASE_CoreBoard"]["spec_path"] == "electronics.core"
    for n, c in comps.items():
        assert c["label"], n
        if c["kind"] == "PURCHASE":
            assert c.get("module_id") or c.get("ref"), n


@pytest.mark.parametrize("name", EXAMPLES)
def test_fastener_counts_and_coupon_tests(built, name):
    out = Path(built[name]["_out"])
    comps = json.loads((out / "cad/components.json").read_text())
    groups = {n: c for n, c in comps.items() if n.startswith("PURCHASE_") and ("Screws" in n or "Inserts" in n or n == "PURCHASE_Feet")}
    assert "PURCHASE_HeatSetInserts" in groups
    for n, c in groups.items():
        assert isinstance(c.get("qty"), int) and c["qty"] > 0 and c.get("size"), n
    s = built[name]
    tests = {n: v for n, v in s["print"].items() if n.startswith("TEST_")}
    assert "TEST_FitCoupon" in tests
    for n, v in tests.items():
        assert v.get("tests"), n
    summ = json.loads((out / "cad/cad-summary.json").read_text())
    assert summ["rebuild"] == "python cad/gadget_cad.py"


def test_draft_glb_is_a_shell(built):
    import pygltflib

    g = pygltflib.GLTF2().load(str(Path(built["desk-buddy"]["_out"]) / "cad/draft.glb"))
    assert [n.name for n in g.nodes] == ["PRINT_Shell"]


def test_preview_fast(tmp_path):
    sys.path.insert(0, str(STUDIO / "engine"))
    from gstudio.cad import preview
    from gstudio.library import Library
    from gstudio.normalize import normalize
    from gstudio.spec import load_spec

    lib = Library()
    sp, _ = normalize(load_spec(STUDIO / "examples/bedtime-robot/spec.json"), lib)
    t = time.time()
    r = preview(sp, lib, tmp_path)
    dt = time.time() - t
    assert Path(r["png"]).exists() and Path(r["glb"]).exists()
    assert dt < 25, dt


def test_standalone_source_rebuilds(built, tmp_path):
    out = Path(built["cloud-lamp"]["_out"])
    r = subprocess.run([sys.executable, str(out / "cad/gadget_cad.py"), "--out", str(tmp_path), "--no-checks", "--no-cables"],
                       capture_output=True, text=True, timeout=600, cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr[-2000:]
    assert (tmp_path / "cad/assembly.step").exists()
    assert (tmp_path / "print/PRINT_Shell.stl").exists()
