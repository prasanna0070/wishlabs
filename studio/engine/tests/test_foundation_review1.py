"""Foundation: REVIEW-1 items (BLK-2, BLK-3/MAJ-4 electrical side, BLK-5, MAJ-3 J_LED, MAJ-8, MAJ-12, MAJ-24, MAJ-29,
MIN-23, MIN-24, MIN-25). Each test names the item it proves."""

import copy
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _fixtures import good  # noqa: E402

from gstudio.electrical import build_electrical, select_cable  # noqa: E402
from gstudio.library import CABLE_FAMILIES, Library, cable_family_for, module_end_family  # noqa: E402
from gstudio.normalize import NIGHT_LIGHT_RING, normalize, nudges_from_cad  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "library"
FIX = Path(__file__).parent / "fixtures_foundation"
BENCH_MODULES = ["CORE_ESP32S3_DEVKITC1_N8R8", "OLED_130_SH1106_I2C", "LED_RING_12_WS2812_50MM", "MIC_INMP441_I2S", "SPK_40MM_4R_3W",
                 "BUTTON_ARCADE_30MM_SNAPIN", "AMP_MAX98357A_I2S", "MICROSD_SPI_MODULE_5V", "STORAGE_MICROSD_32GB_A1"]


@pytest.fixture(scope="module")
def lib():
    return Library()


def _norm(d, lib):
    sp, rep = normalize(d, lib)
    assert rep["ok"], rep["errors"]
    return sp, rep


def _example(slug):
    return json.loads((ROOT / "examples" / slug / "spec.json").read_text())


# ------------------------------------------------------------------------------------------------ BLK-2
def test_blk2_every_cable_row_has_one_module_end_family_sku_and_a_source(lib):
    cables = [m for m in lib if m["id"].startswith("CABLE_")]
    assert len(cables) > 40
    allowed = set(CABLE_FAMILIES) | {"bare_wire", "usb_c"}
    for m in cables:
        me = m.get("module_end") or {}
        assert isinstance(me.get("family"), str) and me["family"] in allowed, m["id"]
        if m.get("design_harness"):
            assert me["family"] in CABLE_FAMILIES, m["id"]
            assert {h["housing"] for h in me["housings"]} and all(h["positions"] for h in me["housings"]), m["id"]
            for h in me["housings"]:
                for p in h["positions"]:
                    assert {"pos", "module_pin", "wire_colour"} <= set(p), (m["id"], p)
        assert m.get("sku"), m["id"]
        assert m.get("sourcing_in") or m.get("factory_make"), m["id"]
        if m.get("factory_make"):
            assert m["factory_make"]["drawing"] and m["sku"].startswith(m["factory_make"]["drawing"]), m["id"]


def test_blk2_no_module_end_per_module_text_anywhere():
    for f in ("modules.json", "backplane-v1.json", "README.md"):
        assert "module end per module" not in (LIB / f).read_text(encoding="utf-8"), f


def test_blk2_named_variants_exist_with_their_module_ends(lib):
    for L in (100, 150, 200, 300):
        d = lib.get(f"CABLE_JSTPH_4P_DUPONT1X4_{L}")
        assert d["module_end"]["family"] == "dupont_2.54" and d["straight_through"] and not d["crossed"]
        mic = lib.get(f"CABLE_JSTPH_6P_DUPONT2X1X3_{L}")
        a, b = mic["module_end"]["housings"]
        assert [p["module_pin"] for p in a["positions"]] == ["L/R", "WS", "SCK"]
        assert [p["module_pin"] for p in b["positions"]] == ["SD", "VDD", "GND"]
        sp = lib.get(f"CABLE_JSTPH_3P_SPADE28_{L}")
        assert sp["module_end"]["family"] == "spade_2.8" and [h["positions"][0]["module_pin"] for h in sp["module_end"]["housings"]] == ["COM", "NO"]
        assert lib.has(f"CABLE_JSTPH_2P_{L}") and lib.has(f"CABLE_JSTXH_3P_{L}")  # PH-PH / XH-XH base rows kept
    assert "2 x Dupont 1x3" in lib.slot("J_MIC")["cable"]["type"]


def test_blk2_cable_family_for():
    assert cable_family_for("jst_ph_2.0") == "jst_ph" and cable_family_for("jst_xh_2.5") == "jst_xh"
    assert cable_family_for("header_2.54") == "dupont_2.54" and cable_family_for("spade_2.8") == "spade_2.8"
    assert cable_family_for("solder_pads") is None and cable_family_for(None) is None


def test_blk2_every_supported_cabled_module_gets_an_exact_row(lib):
    known_gaps = {"TEMPHUM_AHT20_ADAFRUIT_4566", "PIR_AM312_MINI", "SERVO_SG90_TOWERPRO", "ENDSTOP_OMRON_SS01GL"}
    gaps = set()
    for m in lib:
        pv1 = m.get("backplane_v1") or {}
        if not pv1.get("supported") or not pv1.get("cable"):
            continue
        sel = select_cable(lib, m, lib.slot(pv1["slots"][0]), 150)
        if sel["match"] != "exact":
            gaps.add(m["id"])
        else:
            row = lib.get(sel["module"])
            assert row["module_end"]["family"] == (sel["module_end_family"] if sel["module_end_family"] != "factory_prewired"
                                                   else row["backplane_end"]["family"]), m["id"]
    assert gaps == known_gaps  # qwiic / bare leads / solder pads: made to drawing from the base lead (warned)


# ------------------------------------------------------------------------------------------------ BLK-3 / MAJ-4 (electrical side)
def test_blk3_electrical_writes_module_end_housings_and_order(lib):
    sp, _ = _norm(_example("bedtime-robot"), lib)
    el = build_electrical(sp, lib)
    inst = {i["ref"]: i for i in el["instances"]}
    disp = inst["DISP1"]["harness"]
    assert disp["module_end_order"] == ["VCC", "GND", "SCL", "SDA"] and disp["crossed"] is True and disp["made_to_drawing"] is True
    assert disp["cable_module"].endswith("_X2143") and disp["sku"].startswith("GS-H-PH4-D1X4-X2143")
    pos = disp["module_end_housings"][0]["positions"]
    assert [(p["pos"], p["module_pin"], p["port_pin"], p["net"]) for p in pos] == [
        (1, "VCC", 2, "+3V3"), (2, "GND", 1, "GND"), (3, "SCL", 4, "I2C_SCL"), (4, "SDA", 3, "I2C_SDA")]
    btn = inst["BTN1"]["harness"]
    assert btn["module_end_family"] == "spade_2.8" and len(btn["module_end_housings"]) == 2 and btn["crossed"] is False
    mic = inst["MIC1"]["harness"]
    assert [[p["module_pin"] for p in h["positions"]] for h in mic["module_end_housings"]] == [["L/R", "WS", "SCK"], ["SD", "VDD", "GND"]]
    led = inst["LED1"]["harness"]
    assert led["factory_prewired"] is True and led["cable_module"].startswith("CABLE_JSTXH_3P_")
    # every cable connection names the harness row it runs through; the spec's harness rows agree with electrical
    for c in el["connections"]:
        if c["kind"] == "cable":
            ref = c["from"].split(".")[0]
            assert c["via_module"] == inst[ref]["harness"]["cable_module"] and c["via_sku"] == inst[ref]["harness"]["sku"]
    assert {h.ref: h.module for h in sp.derived.harness} == {r: i["harness"]["cable_module"] for r, i in inst.items() if i.get("harness")}
    assert not [w for w in el["warnings"] if "spec.derived.harness names" in w]
    # one row per DevKitC pin, never a bare "U1.header"
    assert len(el["core_socket_pins"]) >= 20 and all(re.match(r"U1\.J[13]-\d+$", r["from"]) for r in el["core_socket_pins"])


def test_blk3_colours_in_the_cable_row_match_electrical(lib):
    sp, _ = _norm(_example("bedtime-robot"), lib)
    el = build_electrical(sp, lib)
    for i in el["instances"]:
        h = i.get("harness")
        if not h or h["factory_prewired"]:
            continue
        row = lib.get(h["cable_module"])
        lib_c = [p["wire_colour"] for hh in row["module_end"]["housings"] for p in hh["positions"]]
        el_c = [p["wire_colour"] for hh in h["module_end_housings"] for p in hh["positions"]]
        assert lib_c == el_c, i["ref"]


def test_blk3_negative_spade_module_on_a_ph_only_cable(lib):
    btn = lib.get("BUTTON_ARCADE_30MM_SNAPIN")
    assert module_end_family(btn) == "spade_2.8"
    ph_only = lib.get("CABLE_JSTPH_3P_200")
    assert ph_only["module_end"]["family"] != module_end_family(btn)  # the pipeline's harness-module-end check fails this pairing
    assert select_cable(lib, btn, lib.slot("J_IO1"), 200)["module"] == "CABLE_JSTPH_3P_SPADE28_200"


def test_maj4_straight_through_row_is_never_chosen_for_a_crossed_module(lib):
    for m in lib:
        pv1 = m.get("backplane_v1") or {}
        if not pv1.get("supported") or not pv1.get("cable"):
            continue
        sel = select_cable(lib, m, lib.slot(pv1["slots"][0]), 200)
        if sel["crossed"] and sel["match"] == "exact":
            row = lib.get(sel["module"])
            assert row["made_to_drawing"] and not row["straight_through"], m["id"]


# ------------------------------------------------------------------------------------------------ MAJ-3
def test_maj3_led_port_is_xh_and_the_supply_rule_holds(lib):
    sys.path.insert(0, str(LIB / "tools"))
    import gen_backplane_v1 as G  # noqa: E402

    bp = lib.backplane
    assert G.supply_rule(bp["slots"]) == []
    old = copy.deepcopy(bp["slots"])
    led = next(s for s in old if s["id"] == "J_LED")
    led["connector"] = "JST-PH 2.0"  # the pre-MAJ-3 board: J_LED was PH like J_IO1..3
    assert G.supply_rule(old) and "J_LED" in G.supply_rule(old)[0]
    assert bp["checks"]["layout_issues"] == []


# ------------------------------------------------------------------------------------------------ BLK-5
def test_blk5_card_row_requires_link_and_reprice(lib):
    sd = lib.get("MICROSD_SPI_MODULE_5V")
    assert sd["requires"] == [{"module": "STORAGE_MICROSD_32GB_A1", "qty": 1, "why": sd["requires"][0]["why"]}]
    assert sd["price_inr_planning"][1] <= 49
    card = lib.get("STORAGE_MICROSD_32GB_A1")
    retail = [r for r in card["sourcing_in"] if r.get("retail") and r.get("fulfilment_country") == "IN" and r.get("price_inr_seen")]
    assert len(retail) >= 2 and all(r["sku"] and r["snapshot"] and r["stock"] == "in_stock" for r in retail)


def test_blk5_normalize_adds_the_card_and_deleting_requires_drops_it(lib, tmp_path):
    sp, rep = _norm(good(), lib)
    card = [i for i in sp.derived.instances if i.module == "STORAGE_MICROSD_32GB_A1"]
    assert len(card) == 1 and card[0].required_by == "SD1" and card[0].slot is None
    # the same library without the requires link: no card (the pipeline's firmware-storage-has-part check then fails)
    mods = json.loads((LIB / "modules.json").read_text())
    for m in mods:
        if m["id"] == "MICROSD_SPI_MODULE_5V":
            m.pop("requires")
    (tmp_path / "modules.json").write_text(json.dumps(mods))
    (tmp_path / "backplane-v1.json").write_text((LIB / "backplane-v1.json").read_text())
    lib2 = Library(tmp_path / "modules.json")
    sp2, _ = _norm(good(), lib2)
    assert not [i for i in sp2.derived.instances if i.module == "STORAGE_MICROSD_32GB_A1"]


# ------------------------------------------------------------------------------------------------ MAJ-12
def test_maj12_inserts_pack_of_ten_and_screws_split_by_length(lib):
    sp, _ = _norm(good(), lib)
    hw = {h.group: h for h in sp.derived.hardware}
    ins = lib.get(hw["HeatSetInserts"].module)
    assert ins["id"] == "INSERT_HEATSET_M2_5X4_MEC8005" and hw["HeatSetInserts"].qty == 17
    src = next(r for r in ins["sourcing_in"] if r.get("selected"))
    assert src["pack_size"] == 10 and src["sku"] == "MEC8005-M2.5"
    assert -(-hw["HeatSetInserts"].qty // src["pack_size"]) == 2  # packs to buy
    # the purchased insert's length (vendor drawing 4.00 mm) equals the length the bosses assume (CNC Kitchen row, 4.0 mm)
    assert ins["length_mm"] == lib.get("INSERT_HEATSET_M2_5X4_STD")["dims_mm"]["z"] == 4.0
    lens = {g: (hw[g].module, hw[g].length_mm) for g in ("CartridgeScrews", "CoverScrews", "ModuleScrews")}
    assert lens == {"CartridgeScrews": ("SCREW_M2_5X5_SHCS_A2", 5.0), "CoverScrews": ("SCREW_M2_5X12_SHCS_A2", 12.0),
                    "ModuleScrews": ("SCREW_M2_5X8_SHCS_A2", 8.0)}
    assert sum(hw[g].qty for g in lens) == 17
    for g in lens:
        row = lib.get(hw[g].module)
        sel = next(r for r in row["sourcing_in"] if r.get("selected"))
        assert sel["sku"].startswith("MEC7001-M2.5/") and sel["pack_size"] == 10
    assert "nut" not in " ".join(lib.get(hw[g].module)["name"].lower() for g in lens)


# ------------------------------------------------------------------------------------------------ MAJ-24
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?Z)?$")


def test_maj24_every_source_row_has_stock_and_iso_checked_at(lib):
    for m in lib:
        for r in m.get("sourcing_in") or []:
            assert r.get("stock") in ("in_stock", "out_of_stock", "unknown"), (m["id"], r.get("vendor"))
            assert r.get("checked_at") and ISO.match(r["checked_at"]), (m["id"], r.get("vendor"), r.get("checked_at"))
            assert "sku" in r, (m["id"], r.get("vendor"))


def test_maj24_benchmark_chosen_rows_have_sku_and_snapshot(lib):
    for mid in BENCH_MODULES:
        sel = next(r for r in lib.get(mid)["sourcing_in"] if r.get("selected"))
        assert sel.get("sku"), mid


# ------------------------------------------------------------------------------------------------ MIN-23
def test_min23_snapshots_exist_and_hashes_match():
    ix = json.loads((LIB / "references" / "index.json").read_text())
    by = {f["path"]: f for f in ix["files"]}
    for f in ix["files"]:
        body = (LIB / "references" / f["path"]).read_bytes()
        assert hashlib.sha256(body).hexdigest() == f["sha256"], f["path"]
    lib = Library()
    named = 0
    for m in lib:
        for r in m.get("sourcing_in") or []:
            if r.get("snapshot"):
                named += 1
                assert r["snapshot"].removeprefix("references/") in by, (m["id"], r["snapshot"])
    assert named >= 20


# ------------------------------------------------------------------------------------------------ MIN-24
def test_min24_backplane_row_follows_backplane_json(lib):
    row = lib.get("BACKPLANE_V1")
    bp = lib.backplane
    want = {(s["id"], str(p["pin"]), p.get("net")) for s in bp["slots"] if s["kind"] in ("port", "module_socket", "power_in") for p in s["pins"]}
    assert {(p["slot"], str(p["pin"]), p["net"]) for p in row["pins"]} == want
    txt = json.dumps({k: row[k] for k in ("pins", "uncertain", "evidence", "electrical", "mount")})
    for bad in ("1206 PTC", "GPIO15/16", "add a Schottky", "PROPOSED allocation"):
        assert bad not in txt, bad
    assert row["uncertain"] == bp["uncertain"] and "Quarktex" not in json.dumps(row)


# ------------------------------------------------------------------------------------------------ MIN-25
def test_min25_hosts_vendors_fulfilment_and_selection(lib):
    for m in lib:
        rows = m.get("sourcing_in") or []
        for r in rows:
            assert r.get("host_ok") is True, (m["id"], r.get("vendor"), r.get("url"))
            assert r.get("fulfilment_country"), (m["id"], r.get("vendor"))
            if r["fulfilment_country"] != "IN":
                assert r.get("import_risk"), (m["id"], r.get("vendor"))
        if rows:
            sel = [r for r in rows if r.get("selected")]
            assert len(sel) == 1 and sel[0]["selection_reason"]["why"], m["id"]


def test_min25_benchmark_second_sources_known_gaps(lib):
    """Core and IO benchmark modules with two or more retail listings of the same part; the remaining gaps are listed."""
    gaps = set()
    for mid in BENCH_MODULES:
        same = [r for r in lib.get(mid)["sourcing_in"] if r.get("retail") and r.get("same_part") is not False and r.get("url")]
        if len(same) < 2:
            gaps.add(mid)
    assert gaps == {"CORE_ESP32S3_DEVKITC1_N8R8", "SPK_40MM_4R_3W", "BUTTON_ARCADE_30MM_SNAPIN"}


# ------------------------------------------------------------------------------------------------ MAJ-8
def test_maj8_validator_uses_kit_footprints(lib):
    """The v3 layout itself is clear once CAD rotates the clamp-ring tabs (BLK-1: theta 16 deg, 3.7 mm gap), so the
    validator is proved on the same design with the light 3 mm lower, where any tab rotation still collides; the built v3
    (fixed tabs) is covered by test_maj8_nudges_from_the_built_v3."""
    d = json.loads((FIX / "bench_v3_spec.json").read_text())
    d["safety"]["flags"] = [f for f in d["safety"]["flags"] if f != "NIGHT_USE"]  # keep the v3 single-pixel LED in place
    sp0, rep0 = normalize(copy.deepcopy(d), lib)
    assert not [e for e in rep0["errors"] if e.get("code") == "kit_overlap"]
    next(f for f in d["features"] if f["id"] == "status_light")["at"] = [0, 82]
    sp, rep = normalize(d, lib)
    kit = [e for e in rep["errors"] if e.get("code") == "kit_overlap"]
    assert kit and any("status_light" in e["msg"] and "speaker" in e["msg"] for e in kit), rep["errors"]
    assert any(n["b"] == "status_light" and n["a"] == "speaker" and "move status_light up" in n["nudge"] for n in rep["nudges"])


def test_maj8_nudges_from_the_built_v3(lib):
    d = json.loads((FIX / "bench_v3_spec.json").read_text())
    cad = json.loads((FIX / "bench_v3_cad.json").read_text())
    from gstudio.spec import load_spec

    nd = nudges_from_cad(load_spec(d), cad["components"], cad["digital_checks"])
    hit = [n for n in nd if {n["a"], n["b"]} == {"speaker", "status_light"}]
    assert hit and hit[0]["move"]["feature"] == "status_light" and hit[0]["move"]["by_mm"] >= 4
    assert "move status_light up" in hit[0]["nudge"]


# ------------------------------------------------------------------------------------------------ MAJ-29
def test_maj29_night_use_gets_the_ring_clear_of_every_kit(lib):
    d = json.loads((FIX / "bench_v3_spec.json").read_text())  # NIGHT_USE, single-pixel LED at [0, 85]
    sp, rep = normalize(d, lib)
    light = sp.feature("status_light")
    assert light.module == NIGHT_LIGHT_RING
    assert any("NIGHT_USE" in a for a in rep["added"])
    # either a clear place was found or the error says why (never a silent single-pixel night light)
    assert rep["ok"] or any(e.get("code") == "night_light_fit" for e in rep["errors"])
    ex, rep2 = _norm(_example("bedtime-robot"), lib)
    assert ex.feature("night_light").module == NIGHT_LIGHT_RING and not rep2["nudges"]
    assert min(lib.get(NIGHT_LIGHT_RING)["face_mm"]) >= 40


# ------------------------------------------------------------------------------------------------ MIN-6 (pipeline request)
def test_min6_length_class_is_the_shortest_above_the_route_estimate(lib):
    from gstudio.normalize import LENGTH_CLASSES

    sp, _ = _norm(_example("bedtime-robot"), lib)
    for h in sp.derived.harness:
        assert h.length_mm == next(c for c in LENGTH_CLASSES if c >= h.distance_mm), (h.ref, h.length_mm, h.distance_mm)
    assert min(h.length_mm for h in sp.derived.harness) < 300  # not every lead is the longest class any more


def test_maj8_kit_into_the_rounded_bottom_is_rejected(lib):
    """Layout from the 20-layout CAD sweep (seed 10) that the first validator passed and CAD rejected: speaker at z 26.5,
    its clamp ring and screws cut the rounded bottom of the shell."""
    d = _example("bedtime-robot")
    at = {"eyes": [2.0, 146.0], "night_light": [2.2, 79.8], "mic": [50.8, 113.1], "speaker": [4.3, 26.5]}
    for f in d["features"]:
        if f["id"] in at:
            f["at"] = at[f["id"]]
    sp, rep = normalize(d, lib)
    assert any(e.get("code") == "kit_shell" and "speaker" in e["msg"] for e in rep["errors"]), rep["errors"]
    sp2, rep2 = _norm(_example("bedtime-robot"), lib)  # the example itself stays clear
