"""REVIEW-1 cad items: one focused test per fix (VM: cd engine && python -m pytest gstudio/cad/tests/test_review1.py -q).

Pure-Python tests (footprint, flat-face fit, lock pins) are quick; the geometry tests build small solids with cadquery.
The full-build acceptance (benchmark spec) is checked by test_bench_build at the end, which reads a CAD build written
by `python gstudio/cad/tests/dbg_build.py gstudio/cad/tests/fixtures/bench-v3-noring-spec.json <out>` into
GS_CAD_BENCH_OUT (skipped when that variable is not set).
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import pytest

STUDIO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(STUDIO / "engine"))
FIX = Path(__file__).resolve().parent / "fixtures"


def _bench_features():
    return json.loads((FIX / "bench-v3-spec.json").read_text())["features"]


# ------------------------------------------------------------------------------------------------ BLK-1 footprint
def test_blk1_feature_above_speaker_never_gets_a_tab_at_90():
    from gstudio.cad import footprint as FP

    spk = {"id": "speaker", "module": "SPK_40MM_4R_3W", "face": "front", "at": [0, 50]}
    led = {"id": "led", "module": "LED_WS2812B_1PX_MODULE", "face": "front", "at": [0, 78]}
    r = FP.speaker_rotation(spk, None, [spk, led])
    assert r["above"] and all(abs(((a - 90.0 + 180) % 360) - 180) > 20 for a in r["angles"]), r
    alone = FP.speaker_rotation(spk, None, [spk])
    assert alone["theta"] == FP.SPEAKER_DEFAULT_TABS  # nothing near: the old triad stays


def test_blk1_benchmark_conflict_at_90_and_clear_when_chosen():
    from gstudio.cad import footprint as FP

    feats = _bench_features()
    old = [dict(f, tab_rotation=90) if f["id"] == "speaker" else f for f in feats]
    c90 = FP.layout_conflicts(old)
    assert any({c["a"], c["b"]} == {"speaker", "status_light"} and "tab" in c["a_what"] for c in c90), c90
    assert FP.layout_conflicts(feats) == []


def test_blk1_footprint_has_every_kit_piece_and_world_boxes():
    from gstudio.cad import footprint as FP

    feats = _bench_features()
    spk = next(f for f in feats if f["id"] == "speaker")
    b = FP.kit_footprint(spk, None, others=feats)
    kinds = {x["kind"] for x in b}
    assert {"module", "retainer", "boss", "tab", "screw"} <= kinds
    assert sum(1 for x in b if x["kind"] == "tab") == 3
    w = FP.to_world(b[0], -55.0)
    assert w[0] < w[1] and w[2] < w[3] and w[4] < w[5] and w[2] >= -55.0
    disp = next(f for f in feats if f["id"] == "eyes")
    assert any(x["kind"] == "clamp" for x in FP.kit_footprint(disp, None))


def test_blk1_vol_eps():
    from gstudio.cad import checks

    assert checks.VOL_EPS == 0.01


def test_maj29_find_clear_at_moves_ring_off_the_speaker():
    from gstudio.cad import footprint as FP

    feats = [{"id": "speaker", "module": "SPK_40MM_4R_3W", "face": "front", "at": [0, 40]},
             {"id": "ring", "module": "LED_RING_12_WS2812_50MM", "face": "front", "at": [0, 60]}]
    r = FP.find_clear_at(feats[1], feats, margin=3.0)
    assert r is not None and r["moved_mm"] > 0
    moved = [feats[0], dict(feats[1], at=r["at"])]
    assert FP.layout_conflicts(moved, margin=3.0) == []
    # nothing fits when every candidate is rejected; an alternate face is tried when allowed
    assert FP.find_clear_at(feats[1], feats, margin=3.0, fits=lambda at: False) is None
    alt = FP.find_clear_at(feats[1], feats, margin=3.0, fits=lambda at: False, faces=["rear"], fits_face=lambda at, f: True,
                           face_centres={"rear": [0, 100]})
    assert alt and alt["face"] == "rear"


# ------------------------------------------------------------------------------------------------ MAJ-7 declared seats
def _fake_model(parts, kits=()):
    from gstudio.cad.build import Model

    m = Model(params={})
    m.parts = parts
    m.kits = list(kits)
    m.info = {"split": "rear_cover"}
    return m


def test_maj7_contact_with_another_kits_shell_feature_is_not_a_seat():
    import cadquery as cq

    from gstudio.cad import checks as C
    from gstudio.cad.geom import box
    from gstudio.cad.parts import Kit, Part

    led = Part("PURCHASE_Led", "PURCHASE", "led", (1, 1, 1), ref="LED1", feature_id="led", role="module")
    led.add("Pcb", box(0, 0, 0, 10, 10, 2))
    clip = Part("PRINT_LedClip", "PRINT", "clamp strap: led", (1, 1, 1), host_ref="LED1", feature_id="led", role="retainer")
    clip.add("Strap", box(0, 0, -2.0, 10, 4, 2))  # touches the board: own retainer
    shell = Part("PRINT_Shell", "PRINT", "main shell", (1, 1, 1), role="shell")
    post = box(0, 6.5, 0, 3, 3, 3)  # the speaker kit's post, touching the LED board
    shell.add("Shell", post.fuse(box(0, 0, 30, 40, 40, 2)))
    k_led, k_spk = Kit("led", "led", "LED1", None), Kit("speaker", "speaker", "SPK1", None)
    k_spk.adds = [post]
    m = _fake_model([led, clip, shell], [k_led, k_spk])
    m.shapes = {"cavity": box(0, 0, 0, 200, 200, 200)}
    mans = C._manifolds(m)
    rows = C.clearances(m, mans, C.kit_add_manifolds(m))
    by = {tuple(r["parts"]): r for r in rows}
    assert by[("PURCHASE_Led", "PRINT_LedClip")].get("seat") == "own retainer"
    bad = by[("PURCHASE_Led", "PRINT_Shell")]
    assert bad.get("not_a_seat") and bad["touches_other_kit"][0]["kit"] == "speaker"


# ------------------------------------------------------------------------------------------------ MIN-5 cables in the matrix
def test_min5_slack_loop_in_the_speaker_fails_collisions():
    from gstudio.cad import checks as C
    from gstudio.cad.geom import box, ring_z
    from gstudio.cad.parts import Part

    spk = Part("PURCHASE_Speaker", "PURCHASE", "speaker", (0, 0, 0), ref="SPK1", feature_id="speaker", role="module")
    spk.add("Frame", box(0, 0, 0, 40, 40, 10))
    own = Part("REFERENCE_CableSpk1", "REFERENCE", "cable route: speaker", (0, 0, 0), host_ref="SPK1", feature_id="speaker", role="cable")
    own.add("Cable", box(10, 0, 0, 3, 3, 30))  # plugs into its own speaker: allowed
    loop = Part("REFERENCE_SlackLoopMic1", "REFERENCE", "slack loop: mic", (0, 0, 0), host_ref="MIC1", feature_id="mic", role="cable")
    loop.add("Loop", ring_z(0, 0, -2, 2, 14, 8))  # inside the speaker volume
    m = _fake_model([spk, own, loop])
    m.cables = []
    mans = C._manifolds(m)
    summ, _ = C.collisions(m, mans, exact=False)
    hit = [tuple(sorted(r["parts"])) for r in summ["intersections"]]
    assert ("PURCHASE_Speaker", "REFERENCE_SlackLoopMic1") in hit
    assert ("PURCHASE_Speaker", "REFERENCE_CableSpk1") not in hit
    assert "REFERENCE_SlackLoopMic1" in summ["cable_components"]


# ------------------------------------------------------------------------------------------------ MIN-9 thin walls
def test_min9_thin_wall_fixture_fails_and_renders_overlay(tmp_path):
    import cadquery as cq
    import numpy as np

    from gstudio.cad import checks as C
    from gstudio.cad import export as E
    from gstudio.cad.geom import box, to_trimesh
    from gstudio.cad.parts import Part
    from gstudio.cad.render_cli import render_subprocess

    # a 60 x 60 x 40 open box with 2.4 mm walls, one of them 1.0 mm: p1 falls under the 1.2 mm rule
    outer = box(0, 0, 20, 60, 60, 40)
    inner = box(0.7, 0, 22.4, 60 - 2.4 - 1.0, 60 - 4.8, 40)
    sh = outer.cut(inner)
    w = C.wall_thickness(to_trimesh(sh, 0.1, 0.3), 1500)
    assert w["p1"] < C.MIN_WALL and w["thin_points"], w
    part = Part("PRINT_Shell", "PRINT", "shell", (0.7, 0.7, 0.9))
    part.add("Shell", sh)
    E.write_glb(tmp_path / "a.glb", [(part.name, E.part_primitives(part), 1.0)])
    r = render_subprocess(tmp_path / "a.glb", tmp_path, ("thin",), None, "rear_cover", 500, timeout=120, name="thin-walls.png",
                          points=w["thin_points"])
    assert r.get("thin") and Path(r["thin"]).stat().st_size > 5000, r


# ------------------------------------------------------------------------------------------------ MIN-10 fragments
def test_min10_fragments_are_recorded_with_volume_and_centroid():
    from gstudio.cad.geom import box, split_fragments

    import cadquery as cq

    s = cq.Compound.makeCompound([box(0, 0, 0, 20, 20, 20), box(40, 0, 0, 1.5, 1.5, 1.5)])
    big, frags = split_fragments(s)
    assert abs(big.Volume() - 8000) < 1e-6
    assert len(frags) == 1 and frags[0]["volume_mm3"] > 1.0 and abs(frags[0]["centroid"][0] - 40) < 1e-6


# ------------------------------------------------------------------------------------------------ MIN-11 coupon coverage
def test_min11_deleting_a_coupon_section_fails_coverage():
    from gstudio.cad import checks as C
    from gstudio.cad.parts import Kit

    k = Kit("eyes", "display", "DISP1", None)
    k.interfaces = [{"id": "DISP1.window", "name": "w", "fit": "f"}, {"id": "DISP1.clamp_posts", "name": "c", "fit": "f"}]
    m = _fake_model([], [k])
    m.coupons = {"TEST_FitCoupon": {"features": {"insert_holes": 1, "tie_slot": 1, "usb_c_opening": 1}, "covers": []},
                 "TEST_SeamMate": {"features": {"lip_gap_mm": 0.25}, "covers": ["lip_gap_mm"]},
                 "TEST_FitEyes": {"features": {}, "covers": ["DISP1.window", "DISP1.clamp_posts"]}}
    need, covered, miss = C.coupon_coverage(m)
    assert not miss and {"DISP1.window", "DISP1.clamp_posts"} <= need
    del m.coupons["TEST_FitEyes"]
    _, _, miss = C.coupon_coverage(m)
    assert miss == ["DISP1.clamp_posts", "DISP1.window"]


# ------------------------------------------------------------------------------------------------ MAJ-30 ears, MAJ-34 roundness
def test_maj30_ear_roundness_changes_the_outline():
    from gstudio.cad import shapes2d as S

    a, b = S.ear("cat", 26, 30, 0.1), S.ear("cat", 26, 30, 0.9)
    assert a.is_valid and b.is_valid and abs(a.area - b.area) > 5
    bear_round, bear_flat = S.ear("bear", 30, 32, 1.0), S.ear("bear", 30, 32, 0.0)
    assert bear_round.is_valid and bear_flat.is_valid and abs(bear_round.area - bear_flat.area) > 5  # disc aspect + neck change
    v = S.ear_visible("bear", 30, 32)
    assert v.bounds[1] >= -1e-6  # visible part never extends below the base line


def test_maj30_bear_ears_on_the_benchmark_body_stay_inside_the_outline():
    from gstudio.cad.body import build_body
    from gstudio.cad.decor import apply_ears
    from gstudio.cad.probe import Probe

    spec = json.loads((FIX / "bench-v3-spec.json").read_text())
    g = build_body(spec["body"], 2.7)
    ears = [{**d, "_idx": i} for i, d in enumerate(spec["decor"]) if d["type"] == "ear"]
    r = apply_ears(g.outer, ears, Probe(g.outer, tol=0.25), g.outer.BoundingBox().ylen)
    assert len(r.ears) == 2
    for e in r.ears:
        assert e["outboard_excess_mm"] <= 0.0, e
        assert abs(e["x"]) < 45.0  # moved inward from the requested 45 mm (the old stem stuck out of the side)
    assert r.outer.isValid()


# ------------------------------------------------------------------------------------------------ MAJ-31 flat face
def test_maj31_face_plate_is_scaled_onto_the_flat_face():
    from shapely.geometry import Polygon

    from gstudio.cad.decor import DecorResult, _fit_flat, footprint

    flat = Polygon([(-45, 95), (45, 95), (45, 145), (-45, 145)])
    d = {"type": "face_plate", "shape": "ellipse", "at": [0, 120], "size": [112, 58], "depth": 1.2, "_idx": 0}
    res = DecorResult(outer=None)
    d2, poly = _fit_flat(res, d, footprint(d), flat)
    assert flat.contains(poly) and d2["size"][0] < 112
    assert res.adjustments[0]["action"] == "scaled" and res.adjustments[0]["requested_mm"] == [112.0, 58.0]
    assert any("scaled" in w for w in res.warnings)


# ------------------------------------------------------------------------------------------------ MAJ-32 screen node
def test_maj32_glb_screen_child_has_an_emissive_texture(tmp_path):
    import numpy as np
    import pygltflib
    from PIL import Image

    from gstudio.cad import export as E

    png = E.idle_face_png()
    im = np.asarray(Image.open(__import__("io").BytesIO(png)).convert("L"))
    assert im.max() > 200 and im.min() == 0  # not uniformly black
    sc = {"c": [0, -50, 120], "u": [1, 0, 0], "v": [0, 0, 1], "n": [0, -1, 0], "w": 31.4, "h": 16.7, "aa_w": 29.4, "aa_h": 14.7, "kind": "oled"}
    tri = (np.array([[0, 0, 0], [1, 0, 0], [0, 0, 1]], float), np.array([[0, -1, 0]] * 3, float), np.array([[0, 1, 2]]), (0.1, 0.1, 0.1))
    E.write_glb(tmp_path / "s.glb", [("PURCHASE_Display", [("Pcb",) + tri], 1.0, [("PURCHASE_Display#screen", [E.screen_quad(sc)], 1.0)])])
    g = pygltflib.GLTF2().load(str(tmp_path / "s.glb"))
    node = next(n for n in g.nodes if n.name == "PURCHASE_Display#screen")
    mat = g.materials[g.meshes[node.mesh].primitives[0].material]
    assert mat.emissiveTexture is not None and g.images and g.textures
    parent = next(n for n in g.nodes if n.name == "PURCHASE_Display")
    assert g.nodes.index(node) in parent.children
    assert [g.nodes[i].name for i in g.scenes[0].nodes] == ["PURCHASE_Display"]


# ------------------------------------------------------------------------------------------------ MAJ-26 pins and drift
def test_maj26_lock_pins_every_import_with_hashes_and_drift_is_reported():
    from gstudio.cad import regen

    lock = (Path(regen.__file__).parent / "requirements.lock").read_text()
    pins = regen.parse_pins(lock)
    for mod, pkg in regen.IMPORTS.items():
        assert regen._norm(pkg) in pins, mod
    assert lock.count("--hash=sha256:") >= len(pins)
    changed = lock.replace(f"numpy=={pins['numpy']}", "numpy==1.26.0")
    assert regen.parse_pins(changed)["numpy"] == "1.26.0"
    drift = [k for k, v in regen.parse_pins(changed).items() if pins.get(k) != v]
    assert drift == ["numpy"]


# ------------------------------------------------------------------------------------------------ MAJ-9 overhangs
def test_maj9_flat_internal_ceiling_is_flagged_and_roof_clip_is_45_deg():
    from shapely.geometry import Polygon

    from gstudio.cad import checks as C
    from gstudio.cad.body import roof_clip
    from gstudio.cad.geom import box
    from gstudio.cad.parts import Part

    hollow = box(0, 0, 25, 80, 80, 50).cut(box(0, 0, 25, 76, 76, 46))  # closed box: its 76 x 76 inner ceiling is unsupported
    p = Part("PRINT_Box", "PRINT", "box", (1, 1, 1), print_info={"up": "z"})
    p.add("Box", hollow)
    r = C.unsupported_ceiling(p)
    assert r["counted_mm2"] > 5000
    arch = Polygon([(-50, 0), (50, 0), (50, 100), (25, 115), (0, 120), (-25, 115), (-50, 100)])
    q = roof_clip(arch)
    top = max(q.exterior.coords, key=lambda c: c[1])
    assert abs(top[0]) < 1.0 and q.area < arch.area
    for x in (-30, -15, 15, 30):  # roof never flatter than 45 deg
        from shapely.geometry import LineString

        z1 = q.intersection(LineString([(x, -1), (x, 200)])).bounds[3]
        z2 = q.intersection(LineString([(x + 2 * (1 if x > 0 else -1), -1), (x + 2 * (1 if x > 0 else -1), 200)])).bounds[3]
        assert z1 - z2 >= 2 * 0.999


# ------------------------------------------------------------------------------------------------ full benchmark build
BENCH = os.environ.get("GS_CAD_BENCH_OUT")


@pytest.mark.skipif(not BENCH, reason="set GS_CAD_BENCH_OUT to a CAD build of fixtures/bench-v3-noring-spec.json")
def test_bench_build():
    out = Path(BENCH)
    res = json.loads((out / "cad/digital-checks.json").read_text())
    st = {c["id"]: c for c in res["checks"]}
    for k in ("collisions", "feature_module_clearance", "clearances", "print_single_solid", "min_wall", "fit_coupon", "unique_labels",
              "decor_inside_silhouette", "decor_on_flat_face", "bottom_edge_round", "power_entry", "unsupported_ceiling"):
        assert st[k]["status"] == "passed", (k, st[k]["detail"])
    assert st["collisions"]["data"]["threshold_mm3"] == 0.01
    assert st["collisions"]["data"]["cable_components"]
    comps = json.loads((out / "cad/components.json").read_text())
    refs = [c["ref"] for c in comps.values() if c.get("ref")]
    assert len(refs) == len(set(refs))
    assert comps["PURCHASE_MicGasket"]["host_ref"] and comps["PURCHASE_MicGasket"]["ref"] is None
    assert any(d.get("name", "").startswith("PRINT_Shell#decor[") and d.get("spec_path") for d in comps["PRINT_Shell"]["decor"])
    summ = json.loads((out / "cad/cad-summary.json").read_text())
    tabs = summ["kits"]["speaker"]["ring_tabs_deg"]
    assert all(abs(((a - 90.0 + 180) % 360) - 180) > 20 for a in tabs), tabs
