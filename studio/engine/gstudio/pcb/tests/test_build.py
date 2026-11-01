"""Integration tests: build real boards with KiCad 9 + Freerouting (run on the VM / builder only).

Each fixture is built once per session (module-scoped); the checks then read the produced files.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import zipfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
STUDIO = HERE.parents[3]
BACKPLANE = STUDIO / "library" / "backplane-v1.json"
OUT = Path(os.environ.get("GSTUDIO_PCB_TEST_OUT") or HERE.parent / "_out" / "pytest")
GUIDE = FIX / "guide-bedtime-robot-e2e-v3.md"  # the benchmark build guide (REVIEW-1 baseline, _out/e2e/v3)
META = {"name": "Fixture", "slug": "fixture", "revision": "A", "date": "2026-11-01", "engine_version": "test"}

pcbnew = pytest.importorskip("pcbnew")
if shutil.which("kicad-cli") is None:  # pragma: no cover
    pytest.skip("kicad-cli not installed", allow_module_level=True)

from gstudio.pcb import library_modules, module_footprints, silk_audit  # noqa: E402
from gstudio.pcb.design import load_backplane, resolve_node  # noqa: E402
from gstudio.pcb.stage import build_pcb  # noqa: E402
from gstudio.pcb.tests.test_units import xh_backplane  # noqa: E402

LIVE = json.loads(BACKPLANE.read_text())
XH_BP = xh_backplane(LIVE)  # J_LED on B3B-XH-A (REVIEW-1 MAJ-3); identical to LIVE once foundation lands it


def _build(name: str, sub: str = "", bp=None) -> tuple[dict, Path, dict]:
    bp = LIVE if bp is None else bp  # read once: another engineer's tree sync must not change it mid-session
    el = json.loads((FIX / f"electrical.{name}.json").read_text())
    out = OUT / (name + sub) / "pcb"
    s = build_pcb(bp, el, out, dict(META, slug=f"fixture-{name}"), {"fast": True, "reuse": False}, None,
                  module_footprints(), library_modules())
    return s, out, el


@pytest.fixture(scope="module")
def full():
    return _build("full", bp=XH_BP)


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

    text = (out / "bom.csv").read_text()
    bom = list(csv.DictReader(text.splitlines()))
    refs = {r for row in bom for r in row["References"].split()}
    assert {"R1", "F1", "U2", "J_PWR", "J_U1A"} <= refs
    # REVIEW-1 MAJ-22: every line orderable; nothing "to select"
    assert "to select" not in text and "generic" not in text and "NOT PINNED" not in text
    for row in bom:
        assert row["MPN"], row
        if row["References"] != "PCB":
            assert row["LCSC part"].startswith("C") and row["Manufacturer"] and row["Source"].startswith("https://"), row
    by_ref = {r: row for row in bom for r in row["References"].split()}
    assert by_ref["R7"]["Value"] == "10k" and by_ref["R7"]["MPN"] == "0603WAF1002T5E"           # MIN-1
    assert by_ref["J_LED"]["MPN"] == "B3B-XH-A(LF)(SN)"                                             # MAJ-3
    assert by_ref["J_PWR"]["MPN"] == "USB4105-GF-A" and by_ref["J_PWR"]["Qty"] == "1"
    assert "J_SRV" not in by_ref["C4"]["Description"]
    cpl = list(csv.DictReader((out / "cpl.csv").read_text().splitlines()))
    placed = {r["Designator"] for r in cpl}
    assert {"R1", "C1", "U2", "U3", "D1", "F1", "J_PWR", "J_U1A", "J_LED"} <= placed
    assert not any(d.startswith(("FID", "TP", "H", "S1_POST")) for d in placed), placed               # no fiducials
    assert all(r["Layer"] in ("Top", "Bottom") for r in cpl)
    assert placed == refs - {"PCB"}
    pcba = json.loads((out / "pcba.json").read_text())
    assert pcba["complete"] and pcba["includes_usb_c_receptacle"] and s["pcba"]["inr_per_board"]["5"]["low"] > 0
    assert pcba["joints_per_board"]["smt"] > 30 and pcba["joints_per_board"]["manual"] > 50
    checks = {c["id"]: c for c in s["checks"]}
    assert checks["pcb.bom_orderable"]["status"] == "passed" and checks["pcb.cpl_clean"]["status"] == "passed"
    assert checks["pcb.amp_sd_boot"]["status"] == "passed"
    assert all(c.get("artifact") for c in s["checks"])


def _footprint_lib_ids(pcb_file: Path) -> dict:
    board = pcbnew.LoadBoard(str(pcb_file))
    return {fp.GetReference(): str(fp.GetFPID().GetLibItemName()) for fp in board.GetFootprints()}


def test_led_port_is_jst_xh_and_marked_5v(full):
    """REVIEW-1 MAJ-3: J_LED is a B3B-XH-A (a 3V3 PH lead cannot mate), silk 'LED 5V' with G/5V/DIN, and J_MIC has
    per-pin labels. (0 DRC / 0 ERC for this board is asserted in test_board_is_clean[full].)"""
    s, out, _ = full
    fps = _footprint_lib_ids(out / "backplane.kicad_pcb")
    assert "B3B-XH-A" in fps["J_LED"] and "PH" in fps["J_IO1"]
    texts = silk_audit.silk_texts(out / "backplane.kicad_pcb")
    assert "LED 5V" in texts and "J_LED" in texts
    for pin in ("G", "5V", "DIN", "SCK", "WS", "SD", "LR"):
        assert pin in texts, pin
    assert {c["id"]: c for c in s["checks"]}["pcb.port_keying"]["status"] == "passed"


def test_silk_has_every_designator_the_guide_names(full):
    """REVIEW-1 MAJ-16: U1, A1, S1, BP1, J_PWR and every J_ port the build guide names are printed on the top silk."""
    s, out, _ = full
    words = silk_audit.silk_words(silk_audit.silk_texts(out / "backplane.kicad_pcb"))
    for d in ("U1", "A1", "S1", "BP1", "J_MIC", "J_I2C1", "J_IO1", "J_LED", "J_SPK", "J_PWR"):
        assert d in words, d
    named = silk_audit.guide_designators(GUIDE.read_text())
    assert {"U1", "A1", "S1", "J_MIC", "J_LED"} <= named
    assert silk_audit.missing_designators(GUIDE.read_text(), words, s["slots_omitted"]) == []
    assert {c["id"]: c for c in s["checks"]}["pcb.silk_designators"]["status"] == "passed", s["silk"]


def test_unchanged_inputs_reuse_the_board(minimal):
    """REVIEW-1 MIN-36: a later version with the same electrical inputs (only name/version/date differ, as after a
    colour edit) reuses the routed board: 'reused', byte-identical Gerbers and the same silk revision."""
    s1, out1, el = minimal
    out2 = OUT / "minimal-v2" / "pcb"
    if out2.exists():
        shutil.rmtree(out2)
    t = time.time()
    s2 = build_pcb(LIVE, el, out2, dict(META, slug="fixture-minimal", name="Renamed", revision="v2",
                                             date="2026-12-31"),
                   {"fast": True, "pcb_reuse_from": str(out1.parent)}, None, module_footprints(), library_modules())
    took = time.time() - t
    assert s2["reused"] and s2["reused"]["from"] == str(out1.parent) and took < 5.0, (s2.get("reused"), took)
    assert s2["revision"] == s1["revision"] and s2["design_id"] == s1["design_id"]
    for f in sorted((out1 / "gerbers").iterdir()):
        assert (out2 / "gerbers" / f.name).read_bytes() == f.read_bytes(), f.name
    for f in ("backplane.kicad_pcb", "backplane-gerbers.zip", "bom.csv", "cpl.csv", "board-top.png"):
        assert (out2 / f).read_bytes() == (out1 / f).read_bytes(), f
    texts = silk_audit.silk_texts(out2 / "backplane.kicad_pcb")
    assert any(f"rev {s1['revision']}" in t for t in texts)
    assert not any("v2" in t.split() or "2026-12-31" in t for t in texts)
    # an electrical change is not reused
    el2 = json.loads(json.dumps(el))
    el2["slots_used"] = [x for x in el2["slots_used"] if x != "J_IO1"]
    from gstudio.pcb import reuse as R
    key2 = R.pcb_key(LIVE, el2, "fixture-minimal", {"fast": True}, module_footprints())
    assert key2 != s1["pcb_key"] and R.find(OUT / "minimal-v3", key2, {"pcb_reuse_from": str(out1.parent)}) is None


def test_same_input_gives_same_board():
    """Two builds from the same in-memory inputs give byte-identical KiCad files (KIIDs are seeded, Freerouting
    runs single-threaded). Inputs are read once so a concurrent tree sync cannot change them between builds."""
    el = json.loads((FIX / "electrical.minimal.json").read_text())
    bp = json.loads(BACKPLANE.read_text())
    fps = module_footprints()
    outs = []
    for i in range(2):
        out = OUT / f"determinism-{i}" / "pcb"
        build_pcb(bp, el, out, dict(META, slug="fixture-determinism"), {"fast": True, "reuse": False}, None, fps)
        outs.append(out)
    for f in ("backplane.kicad_sch", "backplane.kicad_pcb", "netlist.json", "bom.csv", "cpl.csv"):
        assert (outs[0] / f).read_bytes() == (outs[1] / f).read_bytes(), f
