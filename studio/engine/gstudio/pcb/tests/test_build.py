"""Integration tests: build real boards with KiCad 9 + Freerouting (run on the VM / builder only).

Each fixture is built once per session (module-scoped); the checks then read the produced files.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
STUDIO = HERE.parents[3]
BACKPLANE = STUDIO / "library" / "backplane-v1.json"
OUT = HERE.parent / "_out" / "pytest"
META = {"name": "Fixture", "slug": "fixture", "revision": "A", "date": "2026-11-01", "engine_version": "test"}

pcbnew = pytest.importorskip("pcbnew")
if shutil.which("kicad-cli") is None:  # pragma: no cover
    pytest.skip("kicad-cli not installed", allow_module_level=True)

from gstudio.pcb import module_footprints  # noqa: E402
from gstudio.pcb.design import load_backplane, resolve_node  # noqa: E402
from gstudio.pcb.stage import build_pcb  # noqa: E402


def _build(name: str, sub: str = "") -> tuple[dict, Path, dict]:
    el = json.loads((FIX / f"electrical.{name}.json").read_text())
    out = OUT / (name + sub) / "pcb"
    s = build_pcb(BACKPLANE, el, out, dict(META, slug=f"fixture-{name}"), {"fast": True}, None, module_footprints())
    return s, out, el


@pytest.fixture(scope="module")
def full():
    return _build("full")


@pytest.fixture(scope="module")
def minimal():
    return _build("minimal")


@pytest.mark.parametrize("which", ["full", "minimal"])
def test_board_is_clean(which, request):
    s, out, _ = request.getfixturevalue(which)
    assert s["erc_errors"] == 0, json.loads((out / "erc.json").read_text())
    assert s["drc_errors"] == 0, [i for i in json.loads((out / "drc.json").read_text())["violations"]
                                  if i["severity"] == "error"]
    assert s["routed"] and s["unrouted"] == 0
    assert s["parity_issues"] == 0
    failed = [c for c in s["checks"] if c["kind"] == "digital" and c["status"] != "passed"]
    assert not failed, failed
    # the only accepted DRC warnings are the USB-C receptacle's own silk past the board edge (it overhangs by design)
    warns = json.loads((out / "drc.json").read_text())["violations"]
    other = [w for w in warns if not (w["type"] == "silk_edge_clearance" and any("J_PWR" in i["description"] for i in w["items"]))
             and w["severity"] == "warning"]
    assert not other, other


@pytest.mark.parametrize("which", ["full", "minimal"])
def test_netlist_equals_electrical_json(which, request):
    s, out, el = request.getfixturevalue(which)
    bp = load_backplane(BACKPLANE)
    nl = json.loads((out / "netlist.json").read_text())
    assert nl["comparison"]["ok"], nl["comparison"]["problems"]
    sch = {n["name"]: set(n["nodes"]) for n in nl["nets"]}
    node_net = {nd: k for k, v in sch.items() for nd in v}
    on_board = 0
    for net in el["nets"]:
        resolved = [resolve_node(n, bp) for n in net["nodes"]]
        present = [f"{r}.{p}" for r, p in (x for x in resolved if x) if f"{r}.{p}" in node_net]
        if net["name"] not in sch:
            # only allowed when the net reaches a single on-board pad (left as a no-connect by design)
            assert all(node_net[n].startswith("unconnected-") for n in present), (net["name"], present)
            continue
        for n in present:
            on_board += 1
            assert node_net.get(n) in (net["name"], *s["splits"].values()), (net["name"], n, node_net.get(n))
    assert on_board > 20


def test_minimal_board_has_only_populated_footprints(minimal, full):
    s, out, el = minimal
    board = pcbnew.LoadBoard(str(out / "backplane.kicad_pcb"))
    refs = {fp.GetReference() for fp in board.GetFootprints()}
    assert "J_A1" not in refs and "J_S1" not in refs and "J_MIC" not in refs and "J_DISP" not in refs
    assert {"J_U1A", "J_U1B", "J_PWR", "J_I2C1", "J_IO1", "J_LED", "U3"} <= refs
    assert len(refs) < len({fp.GetReference() for fp in pcbnew.LoadBoard(str(full[1] / "backplane.kicad_pcb")).GetFootprints()})
    # same outline for every design
    bb = board.GetBoardEdgesBoundingBox()
    assert abs(pcbnew.ToMM(bb.GetWidth()) - s["size_mm"][0]) < 0.2
    assert abs(pcbnew.ToMM(bb.GetHeight()) - s["size_mm"][1]) < 0.2


@pytest.mark.parametrize("which", ["full", "minimal"])
def test_gerber_zip_has_all_layers(which, request):
    s, out, _ = request.getfixturevalue(which)
    with zipfile.ZipFile(out / "backplane-gerbers.zip") as z:
        names = z.namelist()
    for suffix in ("-F_Cu.gtl", "-B_Cu.gbl", "-F_Mask.gts", "-B_Mask.gbs", "-F_Silkscreen.gto", "-B_Silkscreen.gbo",
                   "-F_Paste.gtp", "-Edge_Cuts.gm1", "-PTH.drl", "-NPTH.drl"):
        assert any(n.endswith(suffix) for n in names), (suffix, names)


@pytest.mark.parametrize("which", ["full", "minimal"])
def test_outputs_exist_and_glb_loads(which, request):
    s, out, _ = request.getfixturevalue(which)
    for f in ("backplane.kicad_pro", "backplane.kicad_sch", "backplane.kicad_pcb", "schematic.svg", "schematic.pdf",
              "board-top.png", "board-bottom.png", "board-top.svg", "board-bottom.svg", "board.glb", "bom.csv",
              "cpl.csv", "drc.json", "erc.json", "netlist.json"):
        assert (out / f).exists() and (out / f).stat().st_size > 0, f
    pygltflib = pytest.importorskip("pygltflib")
    g = pygltflib.GLTF2().load(str(out / "board.glb"))
    assert len(g.meshes) > 10 and len(g.nodes) > 10
    trimesh = pytest.importorskip("trimesh")
    scene = trimesh.load(str(out / "board.glb"))
    ext = scene.extents
    assert 55 < max(ext) * (1000 if max(ext) < 1 else 1) < 120  # board-sized (m or mm units)


def test_bom_and_cpl_are_consistent(full):
    s, out, _ = full
    import csv

    bom = list(csv.DictReader((out / "bom.csv").read_text().splitlines()))
    refs = {r for row in bom for r in row["References"].split()}
    assert {"R1", "F1", "U2", "J_PWR", "J_U1A"} <= refs
    assert all(row["LCSC part"] in ("to select (estimate)", "") for row in bom)
    cpl = list(csv.DictReader((out / "cpl.csv").read_text().splitlines()))
    smd = {r["Designator"] for r in cpl}
    assert {"R1", "C1", "U2", "U3", "D1", "F1"} <= smd
    assert all(r["Layer"] in ("Top", "Bottom") for r in cpl)


def test_same_input_gives_same_board():
    """Two builds from the same in-memory inputs give byte-identical KiCad files (KIIDs are seeded, Freerouting
    runs single-threaded). Inputs are read once so a concurrent tree sync cannot change them between builds."""
    el = json.loads((FIX / "electrical.minimal.json").read_text())
    bp = json.loads(BACKPLANE.read_text())
    fps = module_footprints()
    outs = []
    for i in range(2):
        out = OUT / f"determinism-{i}" / "pcb"
        build_pcb(bp, el, out, dict(META, slug="fixture-determinism"), {"fast": True}, None, fps)
        outs.append(out)
    for f in ("backplane.kicad_sch", "backplane.kicad_pcb", "netlist.json", "bom.csv", "cpl.csv"):
        assert (outs[0] / f).read_bytes() == (outs[1] / f).read_bytes(), f
