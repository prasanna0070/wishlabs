"""Catalog package A: printed-only objects (kind "printed", pack `printed`) through normalize, the pipeline's electronics
stages, and the order stage's pure parts. No CadQuery here (the CAD path is in gstudio/cad/tests/test_cat_printed_cad.py)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from gstudio.library import Library
from gstudio.normalize import normalize
from gstudio.printed import REASON, empty_electrical, printed_options

STUDIO = Path(__file__).resolve().parents[2]
EX = STUDIO / "library" / "packs" / "examples" / "printed"
SLUGS = ["card-qr-stand", "pen-cup-planter", "owl-figurine"]


def _spec(slug: str) -> dict:
    return json.loads((EX / slug / "spec.json").read_text())


@pytest.fixture()
def printed_on(monkeypatch):
    monkeypatch.setenv("GS_PACKS_ENABLED", "printed")
    monkeypatch.delenv("GS_PACKS_OFF", raising=False)


@pytest.fixture(scope="module")
def lib():
    return Library()


def _cube(size=(30.0, 30.0, 25.0)) -> dict:
    """A small printed blob: one rounded box standing on the table."""
    sx, sy, sz = size
    return {"schema": "gadget-spec/1", "kind": "printed", "name": "Tiny cube", "slug": "tiny-cube",
            "body": {"family": "blob", "split": "none", "feet": 0, "edge_round": 2, "wall": 2.4,
                     "primitives": [{"kind": "rounded_box", "center": [0, 0, sz / 2], "size": [sx, sy, sz], "radius": 3}]},
            "printed": {"opening": "none"}}


@pytest.mark.parametrize("slug", SLUGS)
def test_pack_examples_normalize_with_no_electronics(printed_on, lib, slug):
    sp, rep = normalize(_spec(slug), lib)
    assert rep["ok"], rep["errors"]
    d = sp.derived
    assert d.instances == [] and d.harness == [] and d.cartridge is None
    assert [h.id for h in d.hardware] == ["Feet"]  # the only purchased part
    assert rep["instances"] == [] and rep["slots"] == {}
    assert rep["kind"] == "printed" and rep["printed"] == printed_options(sp)
    assert REASON in rep["warnings"]
    assert sp.to_json_dict().get("kind") == "printed"


def test_planter_options_survive(printed_on, lib):
    sp, rep = normalize(_spec("pen-cup-planter"), lib)
    assert rep["printed"] == {"opening": "top", "drain_hole": True}


def test_small_printed_object_passes_size_and_bed(printed_on, lib):
    sp, rep = normalize(_cube(), lib)
    assert rep["ok"], rep["errors"]
    assert all(19.0 <= v <= 31.0 for v in rep["size_mm"]), rep["size_mm"]
    assert rep["envelope"]["fits_upright"]


def test_too_small_and_too_big_are_refused_with_a_fix(printed_on, lib):
    _, rep = normalize(_cube((30.0, 30.0, 12.0)), lib)
    assert not rep["ok"] and any(e.get("code") == "size" and "minimum is 20" in e["msg"] for e in rep["errors"]), rep["errors"]
    _, rep = normalize(_cube((240.0, 240.0, 100.0)), lib)
    assert not rep["ok"] and any(e.get("code") == "bed" and e.get("fix") for e in rep["errors"]), rep["errors"]


def test_printed_is_refused_when_the_pack_is_off(monkeypatch, lib):
    from gstudio.errors import SpecError

    monkeypatch.setenv("GS_PACKS_ENABLED", "")
    with pytest.raises(SpecError) as ei:  # load_spec runs the pack gate (spec.pack_checks) before normalize dispatches
        normalize(_spec("card-qr-stand"), lib)
    assert "kind" in str(ei.value) and "not available yet" in str(ei.value)


def test_open_top_needs_a_wide_flat_top(printed_on, lib):
    s = _spec("pen-cup-planter")
    s["body"]["profile"] = [[0, 0], [30, 0], [32, 40], [10, 85], [0, 95]]  # a pointed top: nothing wide enough to open
    _, rep = normalize(s, lib)
    assert any(e.get("code") == "opening" for e in rep["errors"]), rep["errors"]


def test_empty_electrical_is_ok_with_no_power():
    el = empty_electrical()
    assert el["instances"] == [] and el["nets"] == [] and el["power"]["ok"] and el["power"]["total_ma_max"] == 0
    assert el["schema"].startswith("gadget-electrical/")


def test_pipeline_skips_pcb_and_firmware_and_writes_an_empty_electrical(printed_on, tmp_path):
    from gstudio.pipeline import build

    s = build(_spec("card-qr-stand"), tmp_path, {"only": "pcb,firmware", "price_check": "off"})
    st = s["stages"]
    assert st["normalize"]["state"] == "done" and st["electrical"]["state"] == "done", st
    for sid in ("pcb", "firmware"):
        assert st[sid]["state"] == "skipped" and st[sid]["message"] == REASON, st[sid]
    el = json.loads((tmp_path / "electrical.json").read_text())
    assert el["instances"] == [] and el["power"]["ok"]
    assert not (tmp_path / "pcb").exists() and not (tmp_path / "firmware").exists()
    assert s["state"] == "done"


def test_gadget_pipeline_is_untouched(printed_on, tmp_path):
    """A gadget example still runs the real electrical stage (instances, nets)."""
    from gstudio.pipeline import build

    spec = json.loads((STUDIO / "examples" / "bedtime-robot" / "spec.json").read_text())
    s = build(spec, tmp_path, {"only": "electrical", "price_check": "off"})
    assert s["stages"]["electrical"]["state"] == "done"
    el = json.loads((tmp_path / "electrical.json").read_text())
    assert el["instances"] and el["nets"]


def test_quote_parts_for_printed_designs():
    from gstudio.order import quote

    ga = quote._j("gstudio-assumptions.json")
    lt = quote.lead_time(5.6, ga, pcb_needed=False, parts_needed=True)
    assert lt["breakdown"]["pcb_fabrication_days"] is None and "No PCB, flashing or test" in lt["reason"]
    assert "flashing" not in lt["reason"].split("No PCB")[0]
    gad = quote.lead_time(5.6, ga)
    assert gad["low"] >= lt["low"] and gad["breakdown"]["pcb_fabrication_days"]
    spec = {"kind": "printed", "derived": {"hardware": [{"id": "Feet", "name": "adhesive bumper foot", "qty": 4}]}}
    ops = quote.assembly_ops(spec, [])
    assert ops == {"module_install": 1, "qc_photo": 1, "pack": 1}
    for op in ("core_prep", "flash_provision", "functional_test", "cable_route", "insert_heatset"):
        assert op not in ops
    nofeet = copy.deepcopy(spec)
    nofeet["derived"]["hardware"] = []
    assert quote.assembly_ops(nofeet, [])["module_install"] == 0


def test_no_adapter_for_a_printed_object():
    from gstudio.order.power import adapter_policy

    in_box, why = adapter_policy({"kind": "printed", "audience": {"child_directed": True}})
    assert in_box is False and "printed" in why
    assert adapter_policy({"audience": {"child_directed": True}})[0] is True  # gadgets keep MAJ-5


def test_printed_module_is_light(tmp_path):
    """Light-engine rule (plan 0.5.5): the web image normalizes without CadQuery, firmware, manual, order or pcb."""
    import subprocess
    import sys

    code = ("import sys\n"
            "for m in ('cadquery', 'OCP', 'gstudio.firmware', 'gstudio.manual', 'gstudio.order', 'gstudio.pcb'):\n"
            "    sys.modules[m] = None\n"
            "import gstudio.printed, gstudio.normalize\n"
            "from gstudio.printed import normalize_printed, empty_electrical\n"
            "print('ok')\n")
    r = subprocess.run([sys.executable, "-c", code], cwd=str(STUDIO / "engine"), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stderr[-2000:]


# ------------------------------------------------------------------------------------------------ cuts (pack printed_cuts)
def _organiser() -> dict:
    return json.loads((STUDIO / "library" / "packs" / "examples" / "printed_cuts" / "desk-organiser" / "spec.json").read_text())


@pytest.mark.parametrize("slug,n_cuts", [("desk-organiser", 4), ("keychain-cap", 1)])
def test_cuts_examples_normalize(monkeypatch, lib, slug, n_cuts):
    monkeypatch.setenv("GS_PACKS_ENABLED", "printed,printed_cuts")
    spec = json.loads((STUDIO / "library" / "packs" / "examples" / "printed_cuts" / slug / "spec.json").read_text())
    sp, rep = normalize(spec, lib)
    assert rep["ok"], rep["errors"]
    assert len(sp.cuts) == n_cuts and sp.derived.instances == []


def test_cuts_are_refused_when_their_pack_is_off(printed_on, lib):
    from gstudio.errors import SpecError

    with pytest.raises(SpecError) as ei:
        normalize(_organiser(), lib)
    assert "cuts" in str(ei.value) and "not available yet" in str(ei.value)


@pytest.mark.parametrize("cut,code", [
    ({"kind": "hole", "face": "top", "at": [200, 0], "size": [10, 10], "depth": 10}, "cut_off_body"),
    ({"kind": "pocket", "face": "top", "at": [55, 0], "size": [20, 20], "depth": 10}, "cut_edge"),
    ({"kind": "pocket", "face": "top", "at": [0, 0], "size": [20, 20], "depth": 69.5}, "cut_floor"),
])
def test_bad_cuts_get_a_fix(monkeypatch, lib, cut, code):
    monkeypatch.setenv("GS_PACKS_ENABLED", "printed,printed_cuts")
    s = _organiser()
    s["cuts"] = [cut]
    _, rep = normalize(s, lib)
    hit = [e for e in rep["errors"] if e.get("code") == code]
    assert hit and hit[0].get("fix"), rep["errors"]


def test_a_through_hole_is_only_a_warning(monkeypatch, lib):
    monkeypatch.setenv("GS_PACKS_ENABLED", "printed,printed_cuts")
    s = _organiser()
    s["cuts"] = [{"kind": "hole", "face": "front", "at": [0, 35], "size": [10, 10], "depth": 80}]
    _, rep = normalize(s, lib)
    assert rep["ok"], rep["errors"]
    assert any("all the way through" in w for w in rep["warnings"])


def test_open_top_rim_must_keep_its_wall(monkeypatch, lib):
    """BENCH pre-check: an agent's open-top cap used a cylinder with radius 4 on a 2.4 mm wall; the vertical mouth met the
    rounded top edge and left a 0.03 mm rim (CAD min_wall failed). normalize now refuses it with a fix."""
    monkeypatch.setenv("GS_PACKS_ENABLED", "printed,printed_cuts")
    spec = json.loads((STUDIO / "library" / "packs" / "examples" / "printed_cuts" / "keychain-cap" / "spec.json").read_text())
    spec["body"]["primitives"][0]["radius"] = 4
    _, rep = normalize(spec, lib)
    hit = [e for e in rep["errors"] if e.get("code") == "opening_rim"]
    assert hit and hit[0]["path"] == "body.primitives[0].radius" and "2.4" in hit[0]["fix"], rep["errors"]
    spec["printed"]["opening"] = "none"
    _, rep = normalize(spec, lib)
    assert not [e for e in rep["errors"] if e.get("code") == "opening_rim"]
