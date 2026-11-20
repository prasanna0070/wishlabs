"""CAD kit registry (CATALOG-PLAN 0.5.5, package B-hooks): packs add mount kits through gstudio/cad/kits_*.py.

- the real registry loads with no errors (every kits_*.py imports, no clashes, no built-in overrides);
- a monkeypatched kits_dummy kind is dispatched by modules.classify, footprint.kit_footprint (pure Python) and
  features.build_kit (CadQuery; skipped where CadQuery is missing);
- built-in kinds and needs win, clashes are reported;
- light-engine rule: kits_ext, cad.footprint and normalize import and run with no CadQuery and without
  gstudio.firmware/manual/order/pcb (the web image), checked in a fresh interpreter.
"""

import json
import os
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

from gstudio.cad import footprint as FP
from gstudio.cad import kits_ext
from gstudio.cad import modules as M

ENGINE = Path(__file__).resolve().parents[1]
ROOT = ENGINE.parent

CALLS: list = []


def _dummy_kit(kit, F, S, O, desc, fz, colors, spec_path):
    CALLS.append(("kit", kit.feature_id, desc["kind"], spec_path))
    kit.info["dummy"] = {"dims": desc.get("dims"), "label": kit.label}


def _dummy_footprint(desc):
    w = float((desc.get("dims") or {}).get("x") or 10.0)
    return [{"part": "PURCHASE_Dummy", "kind": "module", "what": "dummy body", "u": [-w / 2, w / 2 + 2.0], "v": [-3.0, 5.0],
             "depth": [2.4, 20.0]}]


def _dummy_footprint_ctx(desc, fz=None, skin=2.4):
    CALLS.append(("footprint_ctx", fz is not None, skin))
    return [{"part": "PRINT_Shell", "kind": "pad", "what": "dummy pad", "u": [-1, 1], "v": [-1, 1], "depth": [skin, skin + 1]}]


def _dummy_describe(d, entry):
    d["dummy_geom"] = {"from_row": entry.get("id")}


def _mod(name, **tables):
    m = types.ModuleType(f"gstudio.cad.{name}")
    for k, v in tables.items():
        setattr(m, k, v)
    return m


DUMMY = _mod("kits_dummy", KITS={"dummy": _dummy_kit}, CLASSIFY={"dummy_need": "dummy"},
             FOOTPRINTS={"dummy": _dummy_footprint}, DESCRIBE={"dummy": _dummy_describe})

ROW = {"id": "DUMMY_PART_X", "name": "Dummy part", "role": "dummy_role", "dims_mm": {"x": 12.0, "y": 8.0, "z": 20.0},
       "aperture": {"needs": "dummy_need", "size_mm": {"w": 6, "h": 6}}}


@pytest.fixture
def dummy_registry(monkeypatch):
    real = kits_ext.registry()
    reg = kits_ext.build_registry([sys.modules[f"gstudio.cad.{n}"] for n in real.modules if f"gstudio.cad.{n}" in sys.modules] + [DUMMY])
    monkeypatch.setattr(kits_ext, "_REG", reg)
    CALLS.clear()
    return reg


def test_real_registry_loads_clean():
    reg = kits_ext.build_registry()
    assert reg.errors == []
    for name in reg.modules:
        assert name.startswith("kits_") and name != "kits_ext"


def test_classify_dispatches_pack_need(dummy_registry):
    assert M.classify(ROW) == "dummy"
    # built-in needs are untouched; an unknown need still falls back to the role table
    assert M.classify({"id": "X", "role": "button", "aperture": {"needs": "button_hole"}}) == "tactile"
    assert M.classify({"id": "X", "role": "sensor", "aperture": {"needs": "no_such_need"}}) == "sensor"


def test_describe_adds_dims_and_pack_geometry(dummy_registry):
    d = M.describe(ROW)
    assert d["kind"] == "dummy"
    assert d["dims"] == {"x": 12.0, "y": 8.0, "z": 20.0}
    assert d["dummy_geom"] == {"from_row": "DUMMY_PART_X"}
    # built-in kinds get no dims key (descriptors unchanged)
    assert "dims" not in M.describe({"id": "TOUCH_TTP223_SMALL_15X11", "name": "t", "role": "touch",
                                     "dims_mm": {"x": 15, "y": 11, "z": 2}, "aperture": {"needs": "touch_zone"}})


def test_kit_footprint_dispatches_pack_footprint(dummy_registry):
    feat = {"id": "d1", "module": "DUMMY_PART_X", "face": "front", "at": [10.0, 40.0]}
    boxes = FP.kit_footprint(feat, ROW)
    assert len(boxes) == 1
    b = boxes[0]
    assert b["part"] == "PURCHASE_Dummy" and b["what"] == "dummy body" and b["feature"] == "d1"
    assert b["u"] == [4.0, 18.0] and b["v"] == [37.0, 45.0] and b["depth"] == [2.4, 20.0]
    # the rear face mirrors local u about at[0]; a 90 degree rotation turns the box
    rb = FP.kit_footprint({**feat, "face": "rear"}, ROW)[0]
    assert rb["u"] == [2.0, 16.0] and rb["v"] == [37.0, 45.0]
    r90 = FP.kit_footprint({**feat, "rotate": 90}, ROW)[0]
    assert r90["u"] == [5.0, 13.0] and r90["v"] == [34.0, 48.0]
    # two pack kits on one face are checked like built-in kits
    other = {"id": "d2", "module": "DUMMY_PART_X", "face": "front", "at": [14.0, 40.0], "desc": M.describe(ROW)}
    conf = FP.layout_conflicts([{**feat, "desc": M.describe(ROW)}, other])
    assert conf and conf[0]["a"] == "d1" and conf[0]["b"] == "d2"


def test_footprint_context_is_passed_only_when_named(monkeypatch):
    mod = _mod("kits_dummy2", CLASSIFY={"dummy2_need": "dummy2"}, FOOTPRINTS={"dummy2": _dummy_footprint_ctx})
    monkeypatch.setattr(kits_ext, "_REG", kits_ext.build_registry([mod]))
    CALLS.clear()
    row = {**ROW, "id": "DUMMY2", "aperture": {"needs": "dummy2_need"}}
    boxes = FP.kit_footprint({"id": "d", "module": "DUMMY2", "face": "front", "at": [0, 0]}, row, skin=3.0)
    assert CALLS == [("footprint_ctx", True, 3.0)]
    assert boxes[0]["part"] == "PRINT_Shell" and boxes[0]["kind"] == "pad" and boxes[0]["depth"] == [3.0, 4.0]


def test_builtins_win_and_clashes_are_reported():
    bad = _mod("kits_bad", KITS={"display": _dummy_kit, "dummy": _dummy_kit}, CLASSIFY={"grille": "dummy", "x_need": 3},
               FOOTPRINTS={"dummy": "not callable"})
    reg = kits_ext.build_registry([DUMMY, bad])
    assert "display" not in reg.kits and reg.classify.get("grille") is None and "x_need" not in reg.classify
    assert reg.kits["dummy"] is _dummy_kit and reg.owner[("KITS", "dummy")] == "kits_dummy"
    joined = "\n".join(reg.errors)
    assert "built-in kind" in joined and "built-in need" in joined and "clashes with kits_dummy" in joined
    assert "not callable" in joined and "must be a kind name" in joined


def test_build_kit_dispatches_pack_kit(dummy_registry):
    pytest.importorskip("cadquery")
    import numpy as np

    from gstudio.cad.features import build_kit

    class Probe:
        """Flat outer surface at y = -20 (the front face), normals facing -y."""

        def hits(self, origins, dirs):
            o = np.asarray(origins, float)
            d = np.broadcast_to(np.asarray(dirs, float), o.shape)
            t = (-20.0 - o[:, 1]) / d[:, 1]
            return o + d * t[:, None], np.tile([0.0, -1.0, 0.0], (len(o), 1)), t

    desc = M.describe(ROW)
    feat = {"id": "wave_arm", "module": "DUMMY_PART_X", "face": "front", "at": [10.0, 40.0], "label": "flap"}
    kit = build_kit(feat, desc, "DUM1", Probe(), Probe(), None, {}, {}, "features[0]")
    assert CALLS == [("kit", "wave_arm", "dummy", "features[0]")]
    assert kit.kind == "dummy" and kit.info["dummy"] == {"dims": {"x": 12.0, "y": 8.0, "z": 20.0}, "label": "flap"}
    assert not any("no dedicated mount kit" in w for w in kit.warnings)


def test_light_engine_imports_without_cadquery():
    """The web image has no CadQuery and no firmware/manual/order/pcb packages (web/server/Dockerfile:12-22)."""
    code = textwrap.dedent(f"""
        import json, sys
        for m in ("cadquery", "OCP", "gstudio.firmware", "gstudio.manual", "gstudio.order", "gstudio.pcb"):
            sys.modules[m] = None
        import gstudio.cad.kits_ext as K
        import gstudio.cad.footprint as FP
        import gstudio.normalize as N
        from gstudio.library import Library
        reg = K.registry()
        assert reg.errors == [], reg.errors
        lib = Library()
        spec = json.load(open({str(ROOT / "examples" / "bedtime-robot" / "spec.json")!r}))
        sp, rep = N.normalize(spec, lib)
        assert rep["ok"], rep
        boxes = FP.kit_footprint(spec["features"][0], None, lib_get=lib.get)
        assert boxes
        print("LIGHT_OK", sorted(reg.modules))
    """)
    env = {**os.environ, "PYTHONPATH": str(ENGINE)}
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ENGINE), env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-4000:]
    assert "LIGHT_OK" in r.stdout
