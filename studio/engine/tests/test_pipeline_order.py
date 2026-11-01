"""Order stage pieces: slicing a simple STL, G-code parsing, the parts list and the quote."""
import json
import shutil
import struct
from pathlib import Path

import pytest

from gstudio.order import bom, quote, slicer


# ---------------------------------------------------------------- helpers

def box_stl(path: Path, sx: float, sy: float, sz: float, binary: bool = True) -> Path:
    """Axis-aligned box resting on z=0, centred in x/y."""
    x0, x1, y0, y1, z0, z1 = -sx / 2, sx / 2, -sy / 2, sy / 2, 0.0, sz
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    if binary:
        data = bytearray(b"\0" * 80) + struct.pack("<I", len(faces))
        for f in faces:
            data += struct.pack("<3f", 0, 0, 0)
            for i in f:
                data += struct.pack("<3f", *v[i])
            data += b"\0\0"
        path.write_bytes(bytes(data))
    else:
        path.write_text("solid b\n" + "".join("facet normal 0 0 0\nouter loop\n" + "".join(f"vertex {v[i][0]} {v[i][1]} {v[i][2]}\n" for i in f)
                                              + "endloop\nendfacet\n" for f in faces) + "endsolid b\n")
    return path


needs_slicer = pytest.mark.skipif(slicer.find_slicer() is None, reason="prusa-slicer not installed")


# ---------------------------------------------------------------- STL + bed fit

@pytest.mark.parametrize("binary", [True, False])
def test_stl_info_box(tmp_path, binary):
    info = slicer.stl_info(box_stl(tmp_path / "b.stl", 20, 30, 10, binary))
    assert info["triangles"] == 12
    assert info["size_mm"] == [20, 30, 10]
    assert info["volume_mm3"] == pytest.approx(6000, rel=1e-6)
    assert info["bounds_min"][2] == 0


def test_bed_fit_rotates_when_needed():
    bed = (250, 210, 210)
    assert slicer.bed_fit([200, 100, 50], bed) == (True, 0)
    assert slicer.bed_fit([100, 240, 50], bed) == (True, 90)
    assert slicer.bed_fit([260, 100, 50], bed) == (False, 0)
    assert slicer.bed_fit([100, 100, 220], bed) == (False, 0)


def test_profile_matches_brief():
    p = slicer.read_profile()
    assert p["layer_height"] == "0.2" and p["perimeters"] == "3" and p["fill_density"] == "15%" and p["fill_pattern"] == "gyroid"
    assert p["support_material_auto"] == "1" and p["filament_type"] == "PETG"
    assert slicer.bed_from_profile(p) == (250, 210, 210)


# ---------------------------------------------------------------- G-code parsing

GCODE = """M83
;TYPE:External perimeter
G1 X1 Y1 E1.5
G1 X2 Y1 E0.5
G1 E-0.8
G1 E0.8
;TYPE:Support material
G1 X3 Y3 E2.0
;LAYER_CHANGE
;Z:0.2
;TYPE:Internal infill
G1 X4 Y4 E1.0 ; comment E99
;LAYER_CHANGE
;Z:0.4
; filament used [mm] = 5.00
; filament used [cm3] = 0.01
; filament used [g] = 0.02
; total filament cost = 0.04
; estimated printing time (normal mode) = 1h 2m 3s
"""


def test_parse_gcode_relative(tmp_path):
    p = tmp_path / "a.gcode"
    p.write_text(GCODE)
    s = slicer.parse_gcode(p)
    assert s["filament_g"] == 0.02 and s["filament_mm"] == 5.0
    assert s["print_h"] == pytest.approx(1 + 2 / 60 + 3 / 3600, abs=1e-3)
    assert s["by_type_mm"]["External perimeter"] == pytest.approx(2.0)
    assert s["support_mm"] == pytest.approx(2.0) and s["supports"] is True
    assert s["layers"] == 2 and s["top_z_mm"] == 0.4


def test_parse_gcode_absolute_with_resets(tmp_path):
    p = tmp_path / "b.gcode"
    p.write_text("M82\n;TYPE:Perimeter\nG1 X1 E1\nG1 X2 E3\nG92 E0\n;TYPE:Support material interface\nG1 X3 E0.5\n")
    s = slicer.parse_gcode(p)
    assert s["by_type_mm"]["Perimeter"] == pytest.approx(3.0)
    assert s["support_mm"] == pytest.approx(0.5)


def test_parse_time_formats():
    assert slicer.parse_time("45m 2s") == pytest.approx(0.751, abs=1e-3)
    assert slicer.parse_time("1d 2h 0m 0s") == 26


# ---------------------------------------------------------------- real slicing

@needs_slicer
def test_slice_simple_box(tmp_path):
    stl = box_stl(tmp_path / "PRINT_Box.stl", 40, 30, 20)
    r = slicer.slice_part(stl, tmp_path / "work")
    assert r["ok"], r
    # 24 cm3 solid box at 15% infill: grams must be well below a solid block (24 * 1.27 = 30 g) and above zero
    assert 3 < r["filament_g"] < 30
    assert r["print_h"] > 0.1
    assert r["supports"] is False
    assert r["fits_bed"] and r["on_bed"]
    assert (tmp_path / "work" / "PRINT_Box.slice.txt").exists()
    assert not (tmp_path / "work" / "PRINT_Box.gcode").exists()


@needs_slicer
def test_slice_reports_supports_for_overhang(tmp_path):
    trimesh = pytest.importorskip("trimesh")
    a = trimesh.creation.box((20, 20, 40))
    a.apply_translation((0, 0, 20))
    b = trimesh.creation.box((60, 20, 6))
    b.apply_translation((20, 0, 37))
    m = trimesh.boolean.union([a, b], engine="manifold")
    stl = tmp_path / "PRINT_Tee.stl"
    m.export(stl)
    r = slicer.slice_part(stl, tmp_path / "w")
    assert r["ok"] and r["supports"] is True and r["support_g"] > 0.5


def test_too_big_part_is_reported_not_sliced(tmp_path):
    r = slicer.slice_part(box_stl(tmp_path / "PRINT_Huge.stl", 300, 220, 50), tmp_path / "w")
    assert r["ok"] is False and "does not fit" in r["error"]


# ---------------------------------------------------------------- parts list

class FakeLib:
    MODS = {
        "esp32s3_devkitc1_n16r8": {"id": "esp32s3_devkitc1_n16r8", "name": "ESP32-S3-DevKitC-1 N16R8", "catalog_row": "01",
                                   "price_inr_planning": [650, 1300], "requires_soldering": False,
                                   "sourcing_in": [{"vendor": "Robu", "url": "https://robu.in/x", "sku": "R1", "price_inr": 999, "seen_on": "2026-11-01", "stock": "in_stock"},
                                                   {"vendor": "Robocraze", "url": "https://robocraze.com/y", "price_inr": 1049, "seen_on": "2026-11-01"}]},
        "btn_big": {"id": "btn_big", "name": "30 mm arcade button", "price_inr_planning": [40, 90],
                    "sourcing_in": [{"vendor": "Robu", "url": "https://robu.in/b"}]},
        "screws_m25": {"id": "screws_m25", "name": "M2.5 x 8 mm screws", "price_inr_planning": [60, 120],
                       "sourcing_in": [{"vendor": "Robu", "url": "https://robu.in/s", "pack": 50, "price_inr": 89, "seen_on": "2026-10-30"}]},
        "mystery": {"id": "mystery", "name": "Mystery module"},
    }

    def get(self, mid):
        if mid not in self.MODS:
            raise KeyError(mid)
        return self.MODS[mid]

    def catalog_id_for_price(self, mid):
        return {"mystery": None}.get(mid)


SPEC = {
    "name": "Test Gadget", "slug": "test-gadget", "audience": {"child_directed": False}, "colors": {"body": "#8CC9C0", "body_name": "pale teal"},
    "electronics": {"power": {"input": "usb_c_5v", "adapter_in_box": False}}, "safety": {"flags": []},
    "derived": {
        "instances": [{"ref": "U1", "module": "esp32s3_devkitc1_n16r8", "role": "core", "source": "core"},
                      {"ref": "BTN1", "module": "btn_big", "role": "button", "source": "feature", "feature": "story_button"},
                      {"ref": "BTN2", "module": "btn_big", "role": "button", "source": "feature", "feature": "volume_button"}],
        "harness": [{"id": "H1", "ref": "BTN1", "kind": "jst_ph", "cable": "JST-PH 3p 150 mm", "pins": 3, "length_mm": 150, "catalog_row": "07"},
                    {"id": "H2", "ref": "BTN2", "kind": "jst_ph", "cable": "JST-PH 3p 150 mm", "pins": 3, "length_mm": 150, "catalog_row": "07"},
                    {"id": "H3", "ref": "AMP1", "kind": "socket"}],
        "hardware": [{"id": "cover_screws", "name": "Cover screws", "qty": 4, "module": "screws_m25"},
                     {"id": "inserts", "name": "M2.5 heat-set inserts", "qty": 8, "catalog_row": "10"}],
    },
}
COMPS = {"PURCHASE_CoreBoard": {"kind": "PURCHASE", "module_id": "esp32s3_devkitc1_n16r8", "ref": "U1"},
         "PURCHASE_Button": {"kind": "PURCHASE", "ref": "BTN1"}, "PURCHASE_Button2": {"kind": "PURCHASE", "ref": "BTN2"},
         "PURCHASE_CoverScrews": {"kind": "PURCHASE"}, "PRINT_Shell": {"kind": "PRINT", "color": [0.55, 0.79, 0.75], "bbox": [-60, 60, -50, 50, 0, 150]}}


def test_purchased_rows_have_qb22_fields_and_evidence_tiers():
    rows, probs = bom.build_purchased(SPEC, FakeLib(), COMPS)
    by = {r["name"]: r for r in rows}
    core = by["ESP32-S3-DevKitC-1 N16R8"]
    assert core["quantity"] == 1 and core["vendor"] == "Robu" and core["sku"] == "R1" and core["url"]
    assert core["evidence_tier"] == "seen" and core["estimate"] is False and core["price_inr_seen"] == 999 and core["seen_on"]
    assert core["alternatives"] and core["alternatives"][0]["vendor"] == "Robocraze"
    assert core["modelPart"] == "PURCHASE_CoreBoard" and core["requiresSoldering"] is False
    btn = by["30 mm arcade button"]
    assert btn["quantity"] == 2 and btn["evidence_tier"] == "estimate" and btn["estimate"] is True
    assert btn["modelParts"] == ["PURCHASE_Button", "PURCHASE_Button2"]
    cable = by["JST-PH 3p 150 mm"]
    assert cable["quantity"] == 2 and cable["kind"] == "cable"   # socket harness is not bought
    screws = by["M2.5 x 8 mm screws"]
    assert screws["packSize"] == 50 and screws["packsToBuy"] == 1 and screws["modelPart"] == "PURCHASE_CoverScrews"
    ins = by["M2.5 heat-set inserts"]   # no library module: priced from blueprint catalog row 10
    assert ins["evidence_tier"] == "estimate" and ins["price_inr_planning"] == [20, 100] and "row 10" in ins["price_basis"]
    for r in rows:
        for k in ("name", "quantity", "packSize", "requiresSoldering", "estimate", "evidence_tier", "notes"):
            assert k in r
    assert not probs


def test_unknown_module_is_reported():
    spec = json.loads(json.dumps(SPEC))
    spec["derived"]["instances"].append({"ref": "X1", "module": "nope", "role": "x", "source": "extra"})
    _rows, probs = bom.build_purchased(spec, FakeLib(), {})
    assert any("nope" in p for p in probs)


def test_printed_rows():
    parts = {"PRINT_Shell": {"file": "PRINT_Shell.stl", "ok": True, "filament_g": 100.0, "print_h": 5.0, "supports": True, "support_g": 4.0},
             "TEST_Coupon": {"file": "TEST_Coupon.stl", "ok": True, "filament_g": 3.0, "print_h": 0.3, "supports": False}}
    rows = bom.build_printed(parts, COMPS, SPEC, "PETG")
    shell = next(r for r in rows if r["name"] == "PRINT_Shell")
    assert shell["file"] == "print/PRINT_Shell.stl" and shell["grams"] == 100.0 and shell["supports"] is True
    assert shell["color"] == "#8CC9BF" and shell["orientation_note"]
    assert next(r for r in rows if r["name"] == "TEST_Coupon")["kind"] == "test_coupon"


# ---------------------------------------------------------------- quote

PRINTS = {"PRINT_Shell": {"filament_g": 180.0, "print_h": 9.5, "model_volume_cm3": 150.0, "supports": True, "support_g": 6.0},
          "PRINT_RearCover": {"filament_g": 40.0, "print_h": 2.0, "model_volume_cm3": 32.0, "supports": False, "support_g": 0.0}}


def _rows(spec=SPEC):
    rows, _ = bom.build_purchased(spec, FakeLib(), COMPS)
    return rows


def test_quote_four_scenarios_and_headline():
    q = quote.make_quote(_rows(), PRINTS, SPEC, FakeLib(), bbox_mm=[120, 100, 150], draws=2000)
    assert [s["name"] for s in q["scenarios"]] == [s.name for s in quote.pe.SCENARIOS]
    for s in q["scenarios"]:
        assert s["p5"] < s["p50"] < s["p95"]
        assert str(s["list_price_incl_gst"]).endswith("99")
        assert s["margin_at_list_p50"] >= 0.30 - 1e-6 and s["margin_at_list_p95"] >= 0.10 - 1e-6
        assert s["status"] != "BINDING" and s["reasons"]
        assert set(s["breakdown_mean"]) >= {"parts", "print", "assembly_test", "courier", "total_excl_fee"}
    dp = q["delivered_price_inr"]
    assert dp["pilot_one_off"] == q["scenarios"][0]["list_price_incl_gst"]
    assert dp["steady_state"] == q["scenarios"][2]["list_price_incl_gst"]
    assert dp["pilot_one_off"] > dp["steady_state"]
    assert q["binding"] is False and q["status"] == "ESTIMATE" and q["complete"]
    lt = q["lead_time_days"]
    assert 0 < lt["low"] <= lt["high"] and lt["reason"] and lt["steady_state"]["high"] <= lt["high"]
    assert q["inputs"]["print"]["grams"] == 220.0
    assert q["inputs"]["logistics"]["box_cm"]["value"] == [20, 18, 23]
    # the inserts row came from blueprint catalog row 10
    assert any("row 10" in p["basis"] for p in q["inputs"]["parts"])


def test_child_directed_needs_review():
    spec = json.loads(json.dumps(SPEC))
    spec["audience"] = {"child_directed": True, "age_years": 5}
    spec["safety"] = {"flags": ["CHILD_DIRECTED", "LOCAL_VOICE_CAPTURE"]}
    q = quote.make_quote(_rows(spec), PRINTS, spec, FakeLib(), draws=500)
    assert q["status"] == "ESTIMATE_REVIEW_REQUIRED"
    assert any("child" in r for r in q["reasons"]) and any("voice capture" in r for r in q["reasons"])


def test_missing_price_marks_quote_incomplete():
    spec = json.loads(json.dumps(SPEC))
    spec["derived"]["instances"].append({"ref": "M1", "module": "mystery", "role": "x", "source": "extra"})
    rows, _ = bom.build_purchased(spec, FakeLib(), {})
    assert next(r for r in rows if r["name"] == "Mystery module")["evidence_tier"] == "not_confirmed"
    q = quote.make_quote(rows, PRINTS, spec, FakeLib(), draws=500)
    assert q["status"] == "ESTIMATE_INCOMPLETE" and not q["complete"]
    assert "Mystery module" in q["missing_prices"]
    assert all(s["status"] == "ESTIMATE_INCOMPLETE" for s in q["scenarios"])


def test_pack_rounding_one_off_vs_volume():
    rows = [r for r in _rows() if r["name"] == "M2.5 x 8 mm screws"]
    q = quote.make_quote(rows, PRINTS, SPEC, FakeLib(), draws=300)
    p = q["inputs"]["parts"][0]
    assert p["qty_one_off"] == 1 and p["qty_volume"] == pytest.approx(4 / 50)


@needs_slicer
def test_order_stage_end_to_end_on_a_simple_stl(tmp_path):
    """run(ctx) with a stub context: BOM + slicing + quote + build.json merge."""
    from gstudio import order
    from gstudio.pipeline import _FallbackContext

    out = tmp_path / "out"
    (out / "print").mkdir(parents=True)
    box_stl(out / "print" / "PRINT_Shell.stl", 100, 80, 60)
    box_stl(out / "print" / "TEST_Coupon.stl", 30, 30, 3)
    (out / "cad").mkdir()
    (out / "cad" / "components.json").write_text(json.dumps(COMPS))
    (out / "build.json").write_text(json.dumps({"schema": "gadget-build/1", "steps": [{"id": "keep-me"}]}))

    class S:
        def to_json_dict(self, include_derived=True):
            return json.loads(json.dumps(SPEC))

    ctx = _FallbackContext(S(), FakeLib(), out, {"fast": True})
    summary = order.run(ctx)
    b = json.loads((out / "build.json").read_text())
    assert b["steps"] == [{"id": "keep-me"}]          # other stages' keys survive
    assert len(b["purchased"]) == summary["purchased"] and len(b["printed"]) == 2
    assert b["order"]["list_price_inr"] == summary["delivered_price_inr"]["pilot_one_off"]
    q = json.loads((out / "order" / "quote.json").read_text())
    assert len(q["scenarios"]) == 4
    ss = json.loads((out / "print" / "slice-stats.json").read_text())
    assert ss["parts"]["PRINT_Shell"]["ok"] and ss["totals_print_parts"]["parts"] == 1
    assert (out / "print" / "slice.ini").exists() and (out / "print" / "slice" / "PRINT_Shell.slice.txt").exists()
    assert summary["checks"] and all(c["status"] in ("passed", "warning", "failed", "not_run") for c in summary["checks"])


def test_order_stage_without_prints_writes_bom_then_fails(tmp_path):
    from gstudio import order
    from gstudio.errors import StageError
    from gstudio.pipeline import _FallbackContext

    out = tmp_path / "out"
    out.mkdir()

    class S:
        def to_json_dict(self, include_derived=True):
            return json.loads(json.dumps(SPEC))

    with pytest.raises(StageError, match="no quote"):
        order.run(_FallbackContext(S(), FakeLib(), out, {}))
    b = json.loads((out / "build.json").read_text())
    assert b["purchased"] and b["order"]["status"] == "NOT_QUOTED"
    assert not (out / "order" / "quote.json").exists()


def test_real_library_shape_snippet_prices_packs_and_soldering():
    """Library rows as the library agents write them: price_inr_seen + status page_read | search_snippet, no pack field."""
    m = {"id": "SCREWS", "name": "M2.5 screws", "role": "fastener", "catalog_id": "SCREWS_M2_A", "price_inr_planning": [15, 70],
         "reference_product": {"mpn_or_model": "Stainless Steel Socket Head Screws (pack of 10), metric"},
         "module_connector": {"type": "none"},
         "sourcing_in": [{"vendor": "Snippy", "url": "https://s.in/a", "price_inr_seen": 8, "seen_on": "2026-11-01", "status": "search_snippet"},
                         {"vendor": "ThinkRobotics", "url": "https://t.in/b", "price_inr_seen": 79.99, "seen_on": "2026-11-01", "status": "page_read"}]}
    r = bom._row("M2.5 screws", 8, m, module_id="SCREWS", catalog_row=None, refs=[], model_parts=[], kind="hardware")
    assert r["vendor"] == "ThinkRobotics"                     # page-read price preferred over a snippet
    assert r["evidence_tier"] == "seen" and r["price_inr_seen"] == 79.99
    assert r["packSize"] == 10 and r["packsToBuy"] == 1
    assert r["requiresSoldering"] is False and "no electrical joint" in r["requiresSoldering_basis"]
    m2 = dict(m, sourcing_in=[m["sourcing_in"][0]], role="audio_in", module_connector={"type": "header_2.54"})
    r2 = bom._row("Mic", 1, m2, module_id="MIC", catalog_row=None, refs=[], model_parts=[], kind="module")
    assert r2["evidence_tier"] == "estimate" and r2["price_inr_seen"] is None and "search result" in r2["notes"]
    assert r2["requiresSoldering_basis"].startswith("unverified")


def test_set_priced_hardware_and_open_end_cables():
    m = {"id": "INS", "name": "Heat-set insert M2.5", "role": "inserts", "price_inr_planning": [20, 100],
         "sourcing_in": [{"vendor": "TR", "url": "https://t.in/i", "price_inr_seen": 59.99, "seen_on": "2026-11-01", "status": "page_read"}]}
    r = bom._row("Heat-set insert M2.5", 10, m, module_id="INS", catalog_row=None, refs=[], model_parts=[], kind="hardware")
    assert r["packSize"] is None and r["packsToBuy"] == 1 and "one set per build" in r["notes"]
    q = quote.make_quote([r], PRINTS, SPEC, None, draws=300)
    assert q["inputs"]["parts"][0]["qty_one_off"] == 1           # not 10 x the per-set planning price
    c = {"id": "CAB", "name": "JST-PH 4p 300 mm", "role": "harness", "price_inr_planning": [30, 100],
         "sourcing_in": [{"vendor": "Ev", "url": "https://e.in/c", "price_inr_seen": 9, "seen_on": "2026-11-01", "status": "page_read",
                          "notes": "30 cm; open other end; in stock"}]}
    rc = bom._row("JST-PH 4p 300 mm", 2, c, module_id="CAB", catalog_row=None, refs=[], model_parts=[], kind="cable")
    assert rc["requiresSoldering"] is True and "open other end" in rc["requiresSoldering_basis"]


def test_seen_price_above_planning_is_stated():
    m = {"id": "SPK", "name": "Speaker 40 mm", "role": "speaker", "price_inr_planning": [80, 250],
         "sourcing_in": [{"vendor": "DK", "url": "https://d.in/s", "price_inr_seen": 787.51, "seen_on": "2026-11-01", "status": "page_read"}]}
    r = bom._row("Speaker 40 mm", 1, m, module_id="SPK", catalog_row=None, refs=[], model_parts=[], kind="module")
    q = quote.make_quote([r], PRINTS, SPEC, None, draws=300)
    assert q["inputs"]["seen_above_planning"][0]["extra_per_unit_inr"] == pytest.approx(537.51)
    assert any("above their planning range" in x for x in q["reasons"])


def test_hardware_quantity_comes_from_the_cad_groups():
    """CAD's fastener groups are the source of truth for counts (coordinator 2026-11-01); gaskets do not join modules."""
    lib = FakeLib()
    lib.MODS = dict(FakeLib.MODS, GASKET={"id": "GASKET", "name": "Foam gasket strip", "role": "gasket", "price_inr_planning": [30, 120],
                                          "sourcing_in": []},
                    screws_m25=dict(FakeLib.MODS["screws_m25"], role="screws"))
    spec = json.loads(json.dumps(SPEC))
    spec["derived"]["hardware"] = [
        {"id": "SCREWS", "name": "M2.5 x 6", "qty": 4, "module": "screws_m25", "use": "backplane"},
        {"id": "SCREWS", "name": "M2 x 5", "qty": 6, "module": "screws_m25", "use": "modules"},
        {"id": "GASKET", "name": "speaker gasket", "qty": 1, "module": "GASKET"}]
    comps = {"PURCHASE_ModuleScrews": {"kind": "PURCHASE", "module_id": "screws_m25", "label": "M2.5 module clamp screws x9", "solids": 9},
             "PURCHASE_CoverScrews": {"kind": "PURCHASE", "module_id": "screws_m25", "label": "M2.5 cover screws", "solids": 4},
             "PURCHASE_CoreBoard": {"kind": "PURCHASE", "module_id": "esp32s3_devkitc1_n8r8", "ref": "U1"},
             "PURCHASE_Button": {"kind": "PURCHASE", "ref": "BTN1"},
             "PURCHASE_SpeakerGasket": {"kind": "PURCHASE", "ref": "BTN1", "label": "foam gasket ring", "solids": 1}}
    rows, _ = bom.build_purchased(spec, lib, comps)
    by = {r["name"]: r for r in rows}
    sc = by["M2.5 x 8 mm screws"]
    assert sc["quantity"] == 13 and sc["quantity_normalizer"] == 10 and sc["quantity_basis"].startswith("cad/components.json")
    assert "the normalizer's hardware list says 10" in sc["notes"]
    assert sc["modelParts"] == ["PURCHASE_CoverScrews", "PURCHASE_ModuleScrews"]
    assert by["30 mm arcade button"]["modelParts"] == ["PURCHASE_Button"] if "modelParts" in by["30 mm arcade button"] else \
        by["30 mm arcade button"]["modelPart"] == "PURCHASE_Button"
    g = by["Foam gasket strip"]
    assert g["modelPart"] == "PURCHASE_SpeakerGasket" and g["quantity"] == 1   # rings are cut from one strip: no CAD count


def test_cad_group_qty_rules():
    assert bom.cad_group_qty({"qty": 5, "label": "x9", "solids": 9}) == (5, "qty")
    assert bom.cad_group_qty({"label": "M2.5 heat-set inserts x17", "solids": 17}) == (17, "label")
    assert bom.cad_group_qty({"label": "M2.5 x 8", "solids": 4}) == (4, "solids")   # a length is not a count
    assert bom.cad_group_qty({"label": "feet"}) == (None, "none")
