"""Fast unit tests for the CAD helpers (still need cadquery: run on the VM)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

STUDIO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(STUDIO / "engine"))


def test_shapes2d_all_styles_valid():
    from gstudio.cad import shapes2d as S

    for st in ("dot", "oval", "arc_closed", "arc_happy", "star", "heart"):
        p = S.eye(st, 12, 9)
        assert p.is_valid and p.area > 10, st
    for st in ("smile", "open", "small_o", "cat"):
        p = S.mouth(st, 18, 7)
        assert p.is_valid and p.area > 5, st
    for st in ("cat", "bear", "bunny", "owl_tuft", "antenna"):
        p = S.ear(st, 26, 30)
        assert p.is_valid and p.area > 50, st


@pytest.mark.parametrize("pattern", ["hex_holes", "slots", "concentric", "dots_ellipse"])
def test_grille_patterns_stay_inside(pattern):
    from shapely.geometry import Point

    from gstudio.cad.features import _grille_points

    holes = _grille_points({"pattern": pattern, "w": 34, "h": 30, "hole": 2.4, "pitch": 4.0}, 17.4)
    assert len(holes) >= (5 if pattern == "slots" else 8)
    disk = Point(0, 0).buffer(17.4 + 1e-6)
    for h in holes:
        assert disk.contains(h)


def test_every_library_feature_module_resolves():
    from gstudio.cad.modules import describe
    from gstudio.library import Library

    from gstudio.cad import kits_ext

    lib = Library()
    for m in lib.aperture_modules():
        d = describe(m)
        # a row of a pack that is on (library/packs/enabled.json) may resolve to a kit kind its pack registers (kits_ext)
        assert d["kind"] in {"display", "speaker", "snap_button", "tactile", "led", "led_ring", "mic", "touch", "sensor_window", "sensor", "generic"} or kits_ext.is_pack_kind(d["kind"]), (m["id"], d["kind"])
        if d["kind"] not in ("speaker", "snap_button") and not kits_ext.is_pack_kind(d["kind"]):  # pack kits: own table
            assert d["pcb"]["w"] > 0 and d["pcb"]["h"] > 0


def test_decor_stack():
    from gstudio.cad.decor import decor_stack

    d = [{"type": "face_plate", "shape": "circle", "at": [0, 100], "size": [80, 80], "depth": 1.2},
         {"type": "eye", "style": "dot", "at": [10, 100], "size": [8, 8], "depth": 0.8}]
    assert abs(decor_stack(d) - 2.0) < 1e-9


@pytest.mark.parametrize("name", ["bedtime-robot", "cloud-lamp", "desk-buddy"])
def test_params_from_examples(name):
    from gstudio.cad.params import build_params, jsonable
    from gstudio.library import Library
    from gstudio.normalize import normalize
    from gstudio.spec import load_spec

    lib = Library()
    sp, rep = normalize(load_spec(STUDIO / f"examples/{name}/spec.json"), lib)
    assert not rep.get("errors"), rep.get("errors")
    p = build_params(sp, lib)
    json.dumps(jsonable(p))
    assert p["features"] and all(f["ref"] and f["desc"]["kind"] for f in p["features"])
    assert p["backplane"]["id"] == "backplane-v1"


def test_body_families_valid():
    from gstudio.cad.body import build_body

    sil = {"family": "silhouette", "outline": [[-50, 0], [50, 0], [64, 40], [60, 100], [0, 130], [-60, 100], [-64, 40]], "depth": 100,
           "edge_round": 12, "wall": 2.4}
    rev = {"family": "revolve", "profile": [[0, 0], [60, 0], [70, 30], [60, 90], [0, 120]], "squash_y": 0.8, "edge_round": 8, "wall": 2.4}
    blob = {"family": "blob", "primitives": [{"kind": "rounded_box", "center": [0, 0, 20], "size": [150, 110, 50], "radius": 14},
                                            {"kind": "sphere", "center": [0, 0, 60], "size": [100, 100, 100], "radius": 6}],
            "edge_round": 6, "wall": 2.4}
    for b in (sil, rev, blob):
        g = build_body(b, 3.2)
        assert g.outer.isValid() and g.cavity.isValid(), b["family"]
        sh = g.outer.cut(g.cavity)
        assert sh.isValid() and len(sh.Solids()) == 1, b["family"]
    g = build_body(sil, 3.2)
    assert g.info["edge_round_front"] >= 11.9
