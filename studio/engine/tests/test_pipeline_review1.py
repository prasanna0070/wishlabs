"""REVIEW-1 pipeline items: one or more tests per item (BLK-3, BLK-4, BLK-5, MAJ-4..6, MAJ-19, MAJ-21, MAJ-23, MAJ-25,
MAJ-29, MAJ-36, MIN-2, MIN-4, MIN-6, MIN-8, MIN-12, MIN-20..22, MIN-26, MIN-28). Pure functions and small file fixtures;
the scheduler-level tests (MAJ-6 gate, finalize) are in test_pipeline_run.py style and need Linux (forked stages)."""
import copy
import gzip
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from gstudio import checks
from gstudio.order import bom, power, pricecheck, quote


# ---------------------------------------------------------------- fixtures

OLED = {"id": "OLED", "name": "1.3 in OLED", "role": "display", "module_connector": {"type": "header_2.54", "pin_order": ["VCC", "GND", "SCL", "SDA"]},
        "price_inr_planning": [200, 400], "sourcing_in": [{"vendor": "Probots", "url": "https://p.in/oled", "price_inr_seen": 329, "seen_on": "2026-11-01",
                                                            "status": "page_read", "gst": "incl"}]}
BTN = {"id": "BTN", "name": "Arcade button", "role": "button",
       "module_connector": {"type": "wire_leads", "pin_order": ["COM", "NO"], "location": "microswitch spade terminals at the back"},
       "price_inr_planning": [40, 90], "sourcing_in": []}
CAB_PH = {"id": "CABLE_JSTPH_3P_150", "name": "JST-PH 3p to PH 150 mm", "role": "harness", "price_inr_planning": [30, 100],
          "module_end": {"family": "jst_ph", "housings": [{"housing": "PHR-3", "positions": [{"pos": 1, "module_pin": "GND"}]}]}, "sourcing_in": []}
CAB_SPADE = {"id": "CABLE_JSTPH_3P_SPADE28_150", "name": "JST-PH 3p to 2.8 mm spades 150 mm", "role": "harness", "price_inr_planning": [30, 100],
             "module_end": {"family": "spade_2.8", "housings": [{"housing": "spade", "positions": [{"pos": 1, "module_pin": "COM"}, {"pos": 2, "module_pin": "NO"}]}]},
             "factory_make": {"drawing": "spade lead"}, "sourcing_in": []}
CAB_DUP_STRAIGHT = {"id": "CABLE_JSTPH_4P_DUPONT1X4_150_S", "name": "JST-PH 4p to Dupont 1x4 straight 150 mm", "role": "harness", "sku": "S-1",
                    "price_inr_planning": [30, 100], "module_end": {"family": "dupont_2.54", "housings": [{"housing": "1x4", "positions": [
                        {"pos": i + 1, "module_pin": p} for i, p in enumerate(["GND", "VCC", "SDA", "SCL"])]}]},
                    "sourcing_in": [{"vendor": "Probots", "url": "https://p.in/c", "price_inr_seen": 20, "seen_on": "2026-11-01", "status": "page_read"}]}
CAB_DUP_DRAWING = {"id": "CABLE_JSTPH_4P_DUPONT1X4_150", "name": "JST-PH 4p to Dupont 1x4 150 mm (made to drawing)", "role": "harness", "sku": "GS-H4D-150",
                   "price_inr_planning": [30, 100], "made_to_drawing": True, "module_end": {"family": "dupont_2.54", "housings": [{"housing": "1x4", "positions": [
                       {"pos": i + 1, "module_pin": p} for i, p in enumerate(["VCC", "GND", "SCL", "SDA"])]}]}, "sourcing_in": []}


class Lib:
    def __init__(self, mods):
        self.MODS = {m["id"]: m for m in mods}

    def get(self, mid):
        if mid not in self.MODS:
            raise KeyError(mid)
        return self.MODS[mid]

    def by_role(self, role):
        roles = {role} if isinstance(role, str) else set(role)
        return [m for m in self.MODS.values() if m.get("role") in roles]

    def ids(self):
        return list(self.MODS)


def oled_harness(order=None):
    wires = [{"module_pin": p, "slot_pin": i + 1, "slot_pin_name": n, "net": n, "color": c}
             for i, (p, n, c) in enumerate([("GND", "GND", "black"), ("VCC", "3V3", "red"), ("SDA", "SDA", "blue"), ("SCL", "SCL", "yellow")])]
    h = {"cable": "JST-PH 2.0 4p", "length_mm": 150, "wires": wires}
    if order:
        h["module_end_order"] = order
    return {"ref": "DISP1", "module": "OLED", "slot": "J_I2C1", "harness": h}


def btn_harness():
    return {"ref": "BTN1", "module": "BTN", "slot": "J_IO1", "harness": {"cable": "JST-PH 2.0 3p", "length_mm": 150, "wires": [
        {"module_pin": "COM", "slot_pin": 1, "slot_pin_name": "GND", "net": "GND"}, {"module_pin": "NO", "slot_pin": 3, "slot_pin_name": "SIG", "net": "IO1"}]}}


def cable_row(m, refs):
    r = bom._row(m["name"], 1, m, module_id=m["id"], catalog_row=None, refs=refs, model_parts=[], kind="cable")
    return r


# ---------------------------------------------------------------- BLK-3 / MAJ-4


def test_blk3_spade_module_with_ph_only_cable_fails_harness_module_end():
    lib = Lib([BTN, CAB_PH])
    el = {"instances": [btn_harness()]}
    build = {"purchased": [cable_row(CAB_PH, ["BTN1"])], "factory_work": []}
    cs = {c["id"]: c for c in checks.harness_checks(build, el, lib)}
    assert cs["harness-module-end"]["status"] == "failed"
    assert "spade_2.8" in cs["harness-module-end"]["detail"] and "jst_ph" in cs["harness-module-end"]["detail"]


def test_blk3_matching_spade_cable_passes():
    lib = Lib([BTN, CAB_SPADE])
    build = {"purchased": [cable_row(CAB_SPADE, ["BTN1"])], "factory_work": []}
    cs = {c["id"]: c for c in checks.harness_checks(build, {"instances": [btn_harness()]}, lib)}
    assert cs["harness-module-end"]["status"] == "passed"


def test_blk3_old_cable_rows_state_no_module_end_and_fail():
    old = {"id": "CABLE_JSTPH_4P_300", "name": "JST-PH 2.0 4-pin harness", "role": "harness", "module_connector": {"type": "jst_ph_2.0"},
           "price_inr_planning": [30, 100], "sourcing_in": []}
    lib = Lib([OLED, old])
    build = {"purchased": [cable_row(old, ["DISP1"])], "factory_work": []}
    c = {c["id"]: c for c in checks.harness_checks(build, {"instances": [oled_harness()]}, lib)}["harness-module-end"]
    assert c["status"] == "failed" and "unstated module end" in c["detail"]


def test_blk3_wiring_via_is_the_purchased_name_and_u1_rows_are_per_pin():
    row = cable_row(CAB_DUP_DRAWING, ["DISP1"])
    bp = {"name": "Standard backplane PCB", "module_id": "BACKPLANE_V1", "sku": None, "kind": "module"}
    wiring = {"parts": [], "connections": [{"from": "DISP1.GND", "to": "BP1.J_I2C1.1", "kind": "cable", "via": "JST-PH 2.0 4p 300 mm"},
                                           {"from": "AMP1.LRC", "to": "BP1.A1.1", "kind": "socket", "via": "socket A1"},
                                           {"from": "U1.header", "to": "BP1.U1", "kind": "socket", "via": "board socket"}]}
    pins = [{"pin": "J1-1", "devkit_label": "3V3", "gpio": None, "net": "+3V3", "socket": "J_U1A", "socket_pin": 1},
            {"pin": "J1-4", "devkit_label": "4", "gpio": 4, "net": "IO1_SIG", "socket": "J_U1A", "socket_pin": 4}]
    w = checks.fix_wiring(wiring, [row, bp], pins)
    c0, c1 = w["connections"][0], w["connections"][1]
    assert c0["via"] == row["name"] and c0["via_sku"] == "GS-H4D-150" and c0["via_generic"] == "JST-PH 2.0 4p 300 mm"
    assert c1["via"] == bp["name"] and c1["socket"] == "A1"
    u1 = [c for c in w["connections"] if c["from"].startswith("U1.")]
    assert [c["from"] for c in u1] == ["U1.J1-1", "U1.J1-4"] and u1[1]["to"] == "BP1.J_U1A.4" and u1[1]["gpio"] == 4
    assert checks.fix_wiring(w, [row, bp], pins) == w          # idempotent (package re-assembles it)
    cs = {c["id"]: c for c in checks.harness_checks({"purchased": [row, bp], "wiring": w, "factory_work": []},
                                                    {"instances": [oled_harness()]}, Lib([OLED, CAB_DUP_DRAWING]))}
    assert cs["wiring-via-join"]["status"] == "passed"
    bad = json.loads(json.dumps(w))
    bad["connections"][0]["via"] = "JST-PH 2.0 4p 300 mm"
    cs = {c["id"]: c for c in checks.harness_checks({"purchased": [row, bp], "wiring": bad, "factory_work": []},
                                                    {"instances": [oled_harness()]}, Lib([OLED, CAB_DUP_DRAWING]))}
    assert cs["wiring-via-join"]["status"] == "failed"


def test_maj4_crossed_oled_needs_made_to_drawing_and_continuity():
    lib = Lib([OLED, CAB_DUP_DRAWING, CAB_DUP_STRAIGHT])
    el = {"instances": [oled_harness(["VCC", "GND", "SCL", "SDA"])]}
    good = cable_row(CAB_DUP_DRAWING, ["DISP1"])
    work = bom.factory_work([good], el, lib)
    kinds = [w["kind"] for w in work]
    assert "harness_to_drawing" in kinds and "continuity_test" in kinds
    ct = next(w for w in work if w["kind"] == "continuity_test")
    assert ct["refs"] == ["DISP1"] and "port 1 (GND) -> GND" in ct["what"]
    c = {x["id"]: x for x in checks.harness_checks({"purchased": [good], "factory_work": work}, el, lib)}["harness-straight-through"]
    assert c["status"] == "passed" and "VCC, GND, SCL, SDA" in c["detail"]
    straight = cable_row(CAB_DUP_STRAIGHT, ["DISP1"])
    c = {x["id"]: x for x in checks.harness_checks({"purchased": [straight], "factory_work": bom.factory_work([straight], el, lib)}, el, lib)}
    assert c["harness-straight-through"]["status"] == "failed" and "straight-through" in c["harness-straight-through"]["detail"]


def test_maj4_is_crossed_from_pin_orders():
    assert bom.is_crossed(oled_harness()["harness"], OLED) is True
    same = dict(OLED, module_connector={"type": "header_2.54", "pin_order": ["GND", "VCC", "SDA", "SCL"]})
    assert bom.is_crossed(oled_harness()["harness"], same) is False


# ---------------------------------------------------------------- MAJ-5 / MIN-2

LIMITS = {"usb_source_ma": 3000, "fuse_hold_ma": 1500, "limit_5v_ma": 1350, "limit_5v_why": "90% of the PTC hold"}
ADAPTER = {"id": "PSU", "role": "adapter", "name": "Pi 15W USB-C supply (5.1 V 3 A)", "electrical": {"current_ma_max": 3000.0},
           "dims_mm": {"notes": "Captive cable 1.5 m, 18 AWG, USB-C plug"}, "price_inr_planning": [250, 600],
           "sourcing_in": [{"vendor": "TR", "url": "https://t.in/psu", "price_inr_seen": 739.99, "seen_on": "2026-11-01", "status": "page_read"}]}


def test_maj5_supply_limit_is_min_of_fuse_and_rating_and_default_usb_rows():
    pw = {"total_ma_max": 1367.4, "total_ma_max_capped": 822.4, "limit_ma": 1350}
    sb = power.supply_basis(pw, LIMITS, ADAPTER)
    assert sb["limit_ma"] == 1350 and "min(PTC fuse budget 1350 mA, supply rating 3000 mA)" == sb["basis"]
    assert sb["ok"] is True and sb["ok_uncapped"] is False
    usb2 = next(s for s in sb["sources"] if "USB 2.0" in s["source"])
    assert usb2["ma"] == 500 and usb2["ok_capped"] is False
    weak = dict(ADAPTER, electrical={"current_ma_max": 1000.0})
    assert power.supply_basis(pw, LIMITS, weak)["limit_ma"] == 1000


def test_maj5_child_directed_design_ships_the_adapter_in_purchased_and_quote():
    spec = {"name": "T", "audience": {"child_directed": True}, "safety": {"flags": ["CHILD_DIRECTED"]},
            "electronics": {"power": {"input": "usb_c_5v", "adapter_in_box": False}},
            "derived": {"instances": [{"ref": "DISP1", "module": "OLED", "role": "display", "source": "feature"}], "harness": [], "hardware": []}}
    rows, probs = bom.build_purchased(spec, Lib([OLED, ADAPTER]), {}, {"power": {"total_ma_max": 900, "limit_ma": 1350}})
    psu = next(r for r in rows if r.get("role") == "adapter")
    assert psu["in_box"] is True and "BIS CRS" in psu["notes"] and "child-directed" in psu["in_box_basis"]
    q = quote.make_quote(rows, {"PRINT_Shell": {"filament_g": 100.0, "print_h": 5.0}}, spec, None, draws=300)
    assert "the 5 V 3 A USB-C adapter (in the box)" in q["delivered_price_inr"]["includes"]
    assert "USB-C power adapter" not in q["delivered_price_inr"]["excludes"]
    adult = dict(spec, audience={"child_directed": False}, safety={"flags": []})
    rows2, _ = bom.build_purchased(adult, Lib([OLED, ADAPTER]), {}, {"power": {"total_ma_max": 500, "limit_ma": 1350}})
    assert not any(r.get("role") == "adapter" for r in rows2)


def test_min2_drop_budget_terms_and_a_failing_fixture():
    instances = [{"ref": "SD1", "module": "SD"}, {"ref": "AMP1", "module": "AMP"}]
    mods = {"SD": {"electrical": {"supply_v": "4.5-5.5", "supply_rail": "+5V"}}, "AMP": {"electrical": {"supply_v": "2.7-5.5", "supply_rail": "+5V"}}}
    ok = power.drop_budget({"total_ma_max_capped": 800.0}, {}, mods.get, instances, ADAPTER)
    assert set(ok["resistances_ohm"]) == {"cable", "contacts", "ptc", "trace"}
    assert ok["resistances_ohm"]["cable"]["value"] == pytest.approx(2 * 1.5 * 0.02095, abs=1e-4)
    assert ok["required_min_v"] == 4.5 and ok["required_by"].startswith("SD1")
    assert ok["rail_min_v"] == pytest.approx(4.75 - 0.8 * ok["r_total_ohm"], abs=1e-3) and ok["ok"] is True
    bad = power.drop_budget({"total_ma_max_capped": 1500.0}, {}, mods.get, instances, ADAPTER)
    assert bad["rail_min_v"] < 4.5 and bad["ok"] is False


def test_maj5_power_checks_name_their_limit_and_fail_without_firmware_caps(tmp_path):
    class L(Lib):
        backplane = {"power": {"limits": LIMITS}}
    lib = L([ADAPTER, {"id": "SD", "electrical": {"supply_v": "4.5-5.5", "supply_rail": "+5V"}}])
    el = {"instances": [{"ref": "SD1", "module": "SD"}],
          "power": {"total_ma_max": 1400.0, "total_ma_max_capped": 800.0, "limit_ma": 1350, "firmware_caps_required": True,
                    "caps": {"volume_max": 60.0, "led_max_percent": 45.0}}}
    build = {"purchased": [{"name": "PSU", "role": "adapter", "in_box": True, "module_id": "PSU"}]}
    cs = {c["id"]: c for c in checks.power_checks(tmp_path, {}, build, el, lib)}
    assert "min(PTC fuse budget 1350 mA, supply rating 3000 mA)" in cs["power-budget"]["title"]
    assert cs["power-caps-in-firmware"]["status"] == "failed" and cs["power-budget-uncapped"]["status"] == "failed"
    (tmp_path / "firmware" / "include").mkdir(parents=True)
    (tmp_path / "firmware" / "include" / "config.h").write_text(
        "#define GS_CFG_VOLUME_MAX 60  // software cap\n#define GS_CFG_LED_CAP 115  // 45 % of LED full scale\n")
    cs = {c["id"]: c for c in checks.power_checks(tmp_path, {}, build, el, lib)}
    assert cs["power-caps-in-firmware"]["status"] == "passed" and cs["power-budget-uncapped"]["status"] == "warning"
    assert cs["power-default-usb"]["status"] == "warning" and cs["rail-voltage"]["status"] in ("passed", "failed")


# ---------------------------------------------------------------- MAJ-23 / MIN-26


def _row(mid, vendor, price, gst, plan, qty=1, tier_status="page_read", alts=()):
    m = {"id": mid, "name": mid, "role": "display", "price_inr_planning": plan,
         "sourcing_in": [{"vendor": vendor, "url": f"https://{vendor}.in/{mid}", "price_inr_seen": price, "seen_on": "2026-11-01",
                          "status": tier_status, "gst": gst}, *alts]}
    return m, bom._row(mid, qty, m, module_id=mid, catalog_row=None, refs=[], model_parts=[], kind="module")


def test_maj23_gst_normalised_parts_total_matches_a_hand_recompute():
    _m1, a = _row("A", "Electropi", 1449.0, "excl", [650, 1300])
    _m2, b = _row("B", "Probots", 49.0, "incl", [120, 380])
    _m3, c = _row("C", "DigiKey", 787.51, None, [80, 250])
    for r in (a, b, c):
        assert r["gst_basis"] in ("incl", "excl", "unknown") and r["price_inr_incl_gst"] is not None
    assert a["price_inr_incl_gst"] == pytest.approx(1709.82) and b["price_inr_incl_gst"] == 49.0
    assert c["gst_basis"] == "unknown" and c["price_inr_incl_gst"] == pytest.approx(929.26)
    q = quote.make_quote([a, b, c], {"PRINT_Shell": {"filament_g": 100.0, "print_h": 5.0}}, {"name": "x"}, None, draws=2000)
    hand = 1449.0 * 1.18 + 49.0 + 787.51 * 1.18
    assert abs(q["parts_total_inr"]["one_off"] - hand) / hand < 0.02
    pos = {d["name"]: d["position"] for d in q["price_deltas"]["rows"]}
    assert pos == {"A": "above", "B": "below", "C": "above"}           # the below-range item is reported too
    assert any(d["delta_one_off_inr"] < 0 for d in q["price_deltas"]["rows"])
    mean = q["scenarios"][0]["breakdown_mean"]["parts"]
    inb = q["inbound_shipping"]["one_off_inr"]
    assert abs(mean - inb - hand) / hand < 0.03                          # the Monte Carlo centres on the same parts total


def test_min26_inbound_line_per_vendor_and_consolidation():
    alt = {"vendor": "Probots", "url": "https://probots.in/x", "price_inr_seen": 300, "seen_on": "2026-11-01", "status": "page_read", "gst": "incl"}
    spec = {"name": "x", "derived": {"instances": [{"ref": "D1", "module": "D", "role": "display"}, {"ref": "E1", "module": "E", "role": "display"}],
                                     "harness": [], "hardware": []}}
    D = {"id": "D", "name": "D", "role": "display", "price_inr_planning": [200, 400],
         "sourcing_in": [{"vendor": "Lonely", "url": "https://l.in/d", "price_inr_seen": 310, "seen_on": "2026-11-01", "status": "page_read", "gst": "incl"}, alt]}
    E = {"id": "E", "name": "E", "role": "display", "price_inr_planning": [50, 90],
         "sourcing_in": [{"vendor": "Probots", "url": "https://probots.in/e", "price_inr_seen": 60, "seen_on": "2026-11-01", "status": "page_read", "gst": "incl"}]}
    rows, _ = bom.build_purchased(spec, Lib([D, E]), {})
    d = next(r for r in rows if r["name"] == "D")
    assert d["vendor"] == "Probots" and d["consolidated_from"] == "Lonely"
    q = quote.make_quote(rows, {"PRINT_Shell": {"filament_g": 100.0, "print_h": 5.0}}, {"name": "x"}, None, draws=300)
    inb = q["inbound_shipping"]
    assert inb["vendor_count"] == 1 and inb["vendors"][0]["vendor"] == "Probots" and inb["consolidation"]["moves"]
    rows2, _ = bom.build_purchased(spec, Lib([D, E]), {}, consolidate=False)
    q2 = quote.make_quote(rows2, {"PRINT_Shell": {"filament_g": 100.0, "print_h": 5.0}}, {"name": "x"}, None, draws=300)
    assert q2["inbound_shipping"]["vendor_count"] == 2 and all("basis" in v for v in q2["inbound_shipping"]["vendors"])


# ---------------------------------------------------------------- MAJ-25


SHOPIFY = json.dumps({"title": "OLED", "variants": [{"sku": "PRO1243", "price": 32900, "available": True}]}).encode()
LDJSON = (b'<html><script type="application/ld+json">{"@type":"Product","offers":{"@type":"Offer","price":"49.00",'
          b'"priceCurrency":"INR","availability":"https://schema.org/InStock"}}</script></html>')


def test_maj25_receipts_sign_verify_and_detect_tampering(tmp_path, monkeypatch):
    monkeypatch.setenv("GSTUDIO_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("GSTUDIO_PRICE_KEY", "test-key")
    _m, a = _row("OLED", "Robocraze", 329.0, "incl", [200, 400])
    a["url"] = "https://robocraze.com/products/oled"
    _m, b = _row("SD", "Probots", 49.0, "incl", [120, 380])
    _m, c = _row("GONE", "Nowhere", 10.0, "incl", [5, 20])

    def fetch(url):
        if url.endswith("/products/oled.js"):
            return 200, "text/javascript", SHOPIFY
        if "probots" in url.lower():
            return 200, "text/html", LDJSON
        raise OSError("connection refused")
    out = tmp_path / "out"
    rep = pricecheck.check_rows([a, b, c], out, mode="live", fetch=fetch)
    assert rep["signed"] == 2 and rep["dropped"] == ["GONE"]
    assert a["price_inr_seen"] == 329.0 and a["listing"]["read_method"].startswith("shopify") and a["sku"] == "PRO1243"
    assert "and signed" in a["notes"] and "signed receipt" not in a["notes"]
    assert b["price_inr_seen"] == 49.0 and b["listing"]["stock"] == "in_stock"
    assert c["evidence_tier"] == "estimate" and c["listing"]["receipt"] is None
    for r in (a, b):
        assert pricecheck.verify_row(r, out)[0]
        raw = gzip.decompress((out / r["listing"]["snapshot"]).read_bytes())
        assert hashlib.sha256(raw).hexdigest() == r["listing"]["snapshot_sha256"]
    tampered = json.loads(json.dumps(a))
    tampered["price_inr_seen"] = 299.0
    ok, why = pricecheck.verify_row(tampered, out)
    assert not ok and "price" in why
    c1 = checks.price_evidence_check(out, {"purchased": [a, b, c]})
    assert c1["status"] == "passed" and c1["artifact"] == "references/listings"
    assert checks.price_evidence_check(out, {"purchased": [tampered, b, c]})["status"] == "failed"
    off = json.loads(json.dumps(_row("X", "V", 5.0, "incl", [1, 9])[1]))
    pricecheck.check_rows([off], tmp_path / "o2", mode="off")
    assert off["evidence_tier"] == "estimate"


def test_maj25_cache_only_uses_the_cached_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("GSTUDIO_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("GSTUDIO_PRICE_KEY", "k")
    _m, a = _row("SD", "Probots", 49.0, "incl", [120, 380])
    pricecheck.check_rows([a], tmp_path / "o1", fetch=lambda u: (200, "text/html", LDJSON))
    _m, b = _row("SD", "Probots", 49.0, "incl", [120, 380])
    rep = pricecheck.check_rows([b], tmp_path / "o2", mode="cache_only", fetch=lambda u: (_ for _ in ()).throw(AssertionError("no fetch")))
    assert rep["signed"] == 1 and b["evidence_tier"] == "seen"


# ---------------------------------------------------------------- MAJ-29


def test_maj29_night_light_needs_40mm_diffused_light():
    ring = {"id": "RING", "role": "led", "dims_mm": {"x": 50.0, "y": 50.0}}
    dot = {"id": "DOT", "role": "led", "dims_mm": {"x": 10.0, "y": 10.0}}
    spec = {"safety": {"flags": ["NIGHT_USE"]}, "features": [{"id": "night_light", "module": "RING", "face": "front"}]}
    comps = {"PRINT_LedDiffuser": {"feature_id": "night_light", "bbox": [-27, 27, -55, -51, 60, 114]}}
    assert checks.night_light_check(spec, comps, Lib([ring]))["status"] == "passed"
    small = {"PRINT_LedDiffuser": {"feature_id": "night_light", "bbox": [-5, 5, -55, -51, 80, 90]}}
    assert checks.night_light_check(spec, small, Lib([ring]))["status"] == "failed"
    spec2 = dict(spec, features=[{"id": "night_light", "module": "DOT", "face": "front"}])
    assert checks.night_light_check(spec2, comps, Lib([dot]))["status"] == "failed"
    assert checks.night_light_check({"safety": {"flags": []}}, comps, Lib([ring])) is None


# ---------------------------------------------------------------- MIN-4 / MIN-8 / MIN-12


def test_min4_unknown_soldering_stays_null_and_gets_a_factory_header_check():
    hdr = {"id": "MIC", "name": "INMP441", "role": "mic", "module_connector": {"type": "header_2.54"}, "price_inr_planning": [80, 200], "sourcing_in": []}
    core = {"id": "CORE", "name": "DevKitC", "role": "core", "module_connector": {"type": "header_2.54"}, "price_inr_planning": [650, 1300], "sourcing_in": []}
    rm = bom._row("INMP441", 1, hdr, module_id="MIC", catalog_row=None, refs=["MIC1"], model_parts=[], kind="module")
    rc = bom._row("DevKitC", 1, core, module_id="CORE", catalog_row=None, refs=["U1"], model_parts=[], kind="module")
    assert rm["requiresSoldering"] is None and rc["requiresSoldering"] is None
    rb = bom._row("Button", 1, BTN, module_id="BTN", catalog_row=None, refs=["BTN1"], model_parts=[], kind="module")
    assert rb["requiresSoldering"] is False and "spade" in rb["requiresSoldering_basis"]
    work = bom.factory_work([rm, rc, rb])
    assert sorted(w["name"] for w in work if w["kind"] == "header_fit") == ["DevKitC", "INMP441"]


def test_min8_primary_model_part_is_by_role_not_sort_order():
    comps = {"PURCHASE_MicGasket": {"module_id": "GASKET", "ref": "MIC1"}, "PURCHASE_SpeakerGasket": {"module_id": "GASKET", "ref": "SPK1"}}
    for order in (["PURCHASE_MicGasket", "PURCHASE_SpeakerGasket"], ["PURCHASE_SpeakerGasket", "PURCHASE_MicGasket"]):
        assert bom.primary_part(order, comps, module_id="GASKET", groups=["SpeakerGasket"])[0] == "PURCHASE_SpeakerGasket"
    mods = {"PURCHASE_Microphone": {"module_id": "MIC", "ref": "MIC1"}, "PURCHASE_MicGasket": {"module_id": "MIC", "ref": "MIC1"}}
    assert bom.primary_part(["PURCHASE_MicGasket", "PURCHASE_Microphone"], mods, module_id="MIC", refs=["MIC1"])[0] == "PURCHASE_Microphone"


def test_min12_support_and_material_notes_come_from_the_slicer():
    o, s = bom.support_note("outer back face on the bed, lip and tubes up; no supports", {"ok": True, "support_g": 2.53},
                            {"support_material_buildplate_only": "1"})
    assert "no supports" not in o and s.startswith("needs supports: 2.53 g") and "expected none" in s
    o, s = bom.support_note("upright; tree supports for the domed top and the inside of the head", {"ok": True, "support_g": 9.0},
                            {"support_material_buildplate_only": "1"})
    assert "tree" not in o and "build plate only" in s and "domed top" in s
    rows = bom.build_printed({"PRINT_RearCover": {"file": "PRINT_RearCover.stl", "ok": True, "support_g": 2.5, "supports": True}},
                             {"PRINT_RearCover": {"print": {"material": "PLA", "orientation_note": "flat; no supports"}}}, {}, "PETG", None,
                             {"support_material_buildplate_only": "1"})
    assert rows[0]["material"] == "PETG" and "PLA" in rows[0]["material_basis"] and "no supports:" not in rows[0]["orientation_note"]
    from gstudio.order import slicer
    pp = slicer.printer_profile()
    assert pp["class"].startswith("Prusa i3 MK3S") and pp["build_volume_mm"] == [250.0, 210.0, 210.0] and slicer.profile_material() == "PETG"


# ---------------------------------------------------------------- MIN-6


def test_min6_one_length_source_and_slack_window(tmp_path):
    (tmp_path / "cad").mkdir()
    (tmp_path / "cad" / "cable-routes.json").write_text(json.dumps([
        {"id": "H1", "to": "SPK1.CONN", "cable": "JST-PH 2.0 2p 100 mm", "points": [[0, 0, 0], [0, 0, 58]]},
        {"id": "H2", "to": "BTN1.CONN", "cable": "JST-PH 2.0 3p 300 mm", "points": [[0, 0, 0], [0, 0, 133]]}]))
    el = {"instances": [{"ref": "SPK1", "harness": {"length_mm": 100}}, {"ref": "BTN1", "harness": {"length_mm": 300}}]}
    build = {"purchased": [{"kind": "cable", "refs": ["SPK1"], "length_mm": 100}, {"kind": "cable", "refs": ["BTN1"], "length_mm": 300}],
             "factory_work": [{"refs": ["SPK1"], "prewire": {"lead": "JST-PH 2.0 2p 100 mm"}}]}
    cs = {c["id"]: c for c in checks.harness_length_checks(tmp_path, build, el, None)}
    assert cs["cable-length"]["status"] == "passed" and cs["harness-length-one-source"]["status"] == "passed"
    assert cs["harness-slack"]["status"] == "warning" and "BTN1: 300 mm for a 133 mm route; 200 mm" in cs["harness-slack"]["detail"]
    build["factory_work"][0]["prewire"]["lead"] = "JST-PH 2.0 2p 200 mm"
    cs = {c["id"]: c for c in checks.harness_length_checks(tmp_path, build, el, None)}
    assert cs["harness-length-one-source"]["status"] == "failed"


def test_min6_factory_prewire_lead_uses_the_harness_length():
    spk = {"id": "SPK", "name": "Speaker", "role": "speaker", "price_inr_planning": [80, 250], "sourcing_in": [],
           "prewire": {"by": "factory", "lead": "JST-PH 2.0 2p 100 mm",
                       "note": "The factory solders a lead (length = the design's harness length class, 100 mm default)."}}
    r = bom._row("Speaker", 1, spk, module_id="SPK", catalog_row=None, refs=["SPK1"], model_parts=[], kind="module")
    el = {"instances": [{"ref": "SPK1", "module": "SPK", "harness": {"cable": "JST-PH 2.0 2p", "length_mm": 150, "wires": []}}]}
    w = bom.factory_work([r], el)[0]
    assert w["prewire"]["lead"] == "JST-PH 2.0 2p 150 mm" and "length 150 mm for this design" in w["what"]


# ---------------------------------------------------------------- MIN-20 / MIN-21 / MIN-28


def test_min20_physical_checks_follow_failed_and_warning_checks_and_voice_capture():
    cs = [checks.check("collisions", "no unintended intersection", "failed", "6 pairs", stage="cad"),
          checks.check("a", "thing A", "failed", "x"), checks.check("min_wall", "walls", "warning", "1.08 mm"),
          checks.check("b", "fine", "passed")]
    spec = {"safety": {"flags": ["LOCAL_VOICE_CAPTURE"]}, "behaviour": {"settings": {"capture_mode": "child_voice_clip"}},
            "derived": {"instances": [{"ref": "MIC1", "role": "mic"}]}}
    ph = checks.physical_tests(cs, spec)
    follow = [t for t in ph if t.get("from_check")]
    assert len(follow) == 3 and {t["from_check"] for t in follow} == {"collisions", "a", "min_wall"}
    assert all(t["kind"] == "physical" and t["status"] == "not_run" for t in ph)
    assert any(t["title"] == "Record a favourite name in your child's voice" and "30 cm" in t["expected"] for t in ph)
    tests = checks.final_tests([{"title": "Power-on", "kind": "physical", "status": "not_run"}], cs, spec)
    assert tests[0]["title"] == "Power-on" and checks.final_tests(tests, cs, spec) == tests     # re-running adds nothing


def test_min28_plain_details_and_default_artifacts():
    assert checks.plain("covers ['boss_insert', 'tie_slot']") == "covers boss_insert, tie_slot"
    c = checks.normalize_checks([{"id": "drc", "status": True, "detail": "['a']"}], "pcb")[0]
    assert c["artifact"] == "pcb/drc.json" and c["detail"] == "a"
    assert checks.default_artifact("order", "sliced:PRINT_Shell") == "print/slice-stats.json"
    assert all(checks.check(f"x{i}", "t", True, stage=s)["artifact"] for i, s in enumerate(checks.DEFAULT_ARTIFACT))


# ---------------------------------------------------------------- MIN-22


def test_min22_pins_hash_label_and_independent_pad_nets(tmp_path):
    el = {"gpio": {"A": 1}, "b": [1, 2]}
    (tmp_path / "electrical.json").write_text(json.dumps(el, indent=1))
    canon = hashlib.sha256(json.dumps(el, sort_keys=True).encode()).hexdigest()
    fileh = hashlib.sha256((tmp_path / "electrical.json").read_bytes()).hexdigest()
    ph = tmp_path / "pins.h"
    ph.write_text(f"// electrical.json sha256 {canon}\n")
    assert checks.pins_hash_check(tmp_path, ph)["status"] == "warning"
    ph.write_text(f"// electrical.json canonical-JSON sha256 {canon}\n")
    assert checks.pins_hash_check(tmp_path, ph)["status"] == "passed"
    ph.write_text(f"// electrical.json sha256 {fileh}\n")
    assert checks.pins_hash_check(tmp_path, ph)["status"] == "passed"
    ph.write_text("// electrical.json sha256 " + "0" * 64 + "\n")
    assert checks.pins_hash_check(tmp_path, ph)["status"] == "failed"
    pcb = ('(kicad_pcb\n\t(footprint "Conn"\n\t\t(property "Reference" "J_U1A"\n\t\t)\n\t\t(pad "1" thru_hole rect\n\t\t\t(net 1 "+3V3")\n\t\t)'
           '\n\t\t(pad "4" thru_hole circle\n\t\t\t(net 17 "/IO1_SIG")\n\t\t)\n\t)\n)')
    assert checks._kicad_pad_nets(pcb) == {"J_U1A": {"1": "+3V3", "4": "/IO1_SIG"}}
    (tmp_path / "pcb").mkdir()
    (tmp_path / "pcb" / "backplane.kicad_pcb").write_text(pcb)

    class L:
        _backplane_path = tmp_path / "nolib" / "backplane-v1.json"

        def __init__(self, pins):
            self.pins = pins

        def slot(self, sid):
            return {"pins": self.pins}
    good = [{"pin": "J1-1", "devkit_label": "3V3", "gpio": None, "net": "+3V3", "socket": "J_U1A", "socket_pin": 1},
            {"pin": "J1-4", "devkit_label": "4", "gpio": 4, "net": "IO1_SIG", "socket": "J_U1A", "socket_pin": 4}]
    cs = {c["id"]: c for c in checks.pin_map_checks(tmp_path, L(good), None)}
    assert cs["pcb-u1-pad-nets"]["status"] == "passed" and cs["backplane-gpio-labels"]["status"] == "passed"
    assert cs["devkit-header-table"]["status"] == "not_run"
    unpop = copy.deepcopy(good) + [{"pin": "J1-5", "devkit_label": "5", "gpio": 5, "net": "IO2_SIG", "socket": "J_U1A", "socket_pin": 5}]
    open_pcb = pcb.replace('\n\t)\n)', '\n\t\t(pad "5" thru_hole circle\n\t\t\t(net 35 "unconnected-(J_U1A-GPIO5-Pad5)")\n\t\t)\n\t)\n)')
    (tmp_path / "pcb" / "backplane.kicad_pcb").write_text(open_pcb)
    c = {c["id"]: c for c in checks.pin_map_checks(tmp_path, L(unpop), None)}["pcb-u1-pad-nets"]
    assert c["status"] == "passed" and "1 left open" in c["detail"]          # an unpopulated slot's pad is not a mismatch
    (tmp_path / "pcb" / "backplane.kicad_pcb").write_text(pcb)
    swapped = copy.deepcopy(good)
    swapped[1]["gpio"], swapped[1]["net"] = 5, "IO2_SIG"           # two GPIOs swapped in backplane-v1.json
    cs = {c["id"]: c for c in checks.pin_map_checks(tmp_path, L(swapped), None)}
    assert cs["backplane-gpio-labels"]["status"] == "failed" and cs["pcb-u1-pad-nets"]["status"] == "failed"


# ---------------------------------------------------------------- BLK-4 / BLK-5


def test_blk4_stories_audio_defaults_on_for_story_player_only():
    from gstudio.pipeline import apply_option_defaults

    class C:
        options = {}
    c = C()
    c.options = {}
    apply_option_defaults(c, {"behaviour": {"template": "story_player"}})
    assert c.options["stories_audio"] is True and "story_player" in c.options["stories_audio_basis"]
    c.options = {"stories_audio": False}
    apply_option_defaults(c, {"behaviour": {"template": "story_player"}})
    assert c.options["stories_audio"] is False
    c.options = {}
    apply_option_defaults(c, {"behaviour": {"template": "desk_companion"}})
    assert c.options["stories_audio"] is False


def test_blk4_cli_stories_audio_is_tri_state_and_brief_facts():
    from gstudio import cli

    p = cli.make_parser()
    assert p.parse_args(["build", "s.json", "o"]).stories_audio is None          # unset: the pipeline decides (on for story_player)
    assert p.parse_args(["build", "s.json", "o", "--stories-audio"]).stories_audio is True
    assert p.parse_args(["build", "s.json", "o", "--no-stories-audio"]).stories_audio is False
    assert cli.brief_facts({"version": 3, "concept_id": "c2"}) == {"brief_version": 3, "concept_id": "c2"}
    assert cli.brief_facts({"brief_version": 1, "selected_concept": {"id": "k"}}) == {"brief_version": 1, "concept_id": "k"}
    assert cli.brief_facts({}) == {}


def _story_card(sd: Path, audio: bool, write: bool):
    sd.mkdir(parents=True)
    d = {"audio": audio, "tts_model": "m" if audio else None, "default_name": {"name": "little star", "file": "names/_default.wav"},
         "names": [], "stories": [{"id": "s1", "files": ["stories/s1/s00.wav", "stories/s1/s01.wav"]}]}
    (sd / "stories.json").write_text(json.dumps(d))
    if write:
        for f in ("names/_default.wav", "stories/s1/s00.wav", "stories/s1/s01.wav"):
            (sd / f).parent.mkdir(parents=True, exist_ok=True)
            (sd / f).write_bytes(b"RIFF")


def test_blk4_story_audio_present_check(tmp_path):
    spec = {"behaviour": {"template": "story_player"}}
    _story_card(tmp_path / "firmware" / "sdcard", False, False)
    rec = {"firmware": {"summary": {"warnings": ["story audio was requested but TTS is unavailable: no creds; the card has text only"],
                                    "timings": {"story_pack_s": 0.1}}}}
    c = checks.story_audio_check(tmp_path, spec, rec)
    assert c["status"] == "failed" and "no audio yet" in c["detail"] and "TTS is unavailable" in c["detail"]
    t2 = tmp_path / "b"
    _story_card(t2 / "firmware" / "sdcard", True, True)
    assert checks.story_audio_check(t2, spec, {})["status"] == "passed"
    (t2 / "firmware" / "sdcard" / "stories" / "s1" / "s01.wav").unlink()
    assert checks.story_audio_check(t2, spec, {})["status"] == "failed"
    assert checks.story_audio_check(t2, {"behaviour": {"template": "desk_companion"}}, {}) is None


def test_blk5_storage_card_must_be_purchased(tmp_path):
    (tmp_path / "firmware" / "sdcard").mkdir(parents=True)
    sd = {"id": "MICROSD_SPI_MODULE_5V", "role": "storage", "requires": [{"module": "STORAGE_MICROSD_32GB_A1", "qty": 1}]}
    card = {"id": "STORAGE_MICROSD_32GB_A1", "role": "storage_media", "name": "SanDisk 32 GB microSDHC A1 card"}
    lib = Lib([sd, card])
    rows = [{"name": "microSD SPI module", "module_id": "MICROSD_SPI_MODULE_5V"}]
    c = checks.storage_part_check(tmp_path, {"purchased": rows}, lib)
    assert c["status"] == "failed" and "requires STORAGE_MICROSD_32GB_A1" in c["detail"]
    rows.append({"name": card["name"], "module_id": card["id"]})
    assert checks.storage_part_check(tmp_path, {"purchased": rows}, lib)["status"] == "passed"
    no_req = Lib([dict(sd, requires=[]), card])           # deleting the requires entry: the card is no longer bought -> fails
    assert checks.storage_part_check(tmp_path, {"purchased": rows[:1]}, no_req)["status"] == "failed"


# ---------------------------------------------------------------- MAJ-19 / MAJ-21 verify_delivery


def _delivery(tmp_path: Path, guide_titles, test_titles, stale=None):
    import shutil

    from gstudio import package
    out = tmp_path / "d"
    out.mkdir(parents=True)
    tests = [{"title": t, "kind": "physical", "status": "not_run"} for t in test_titles]
    (out / "build.json").write_text(json.dumps({"purchased": [], "steps": [], "tests": tests}))
    (out / "BUILD_GUIDE.md").write_text("# G\n\n## Final checks to record\n\n" + "\n".join(f"- **{t}:** x" for t in guide_titles) + "\n\n## Next\n")
    (out / "PROJECT.md").write_text("# P\n" + (f"`{stale}` (not produced)\n" if stale else "`manifest.json` (written at packaging)\n"))
    (out / "checks").mkdir()
    (out / "checks" / "digital-checks.json").write_text(json.dumps({"checks": [checks.check("a", "t", True)]}))
    shutil.copyfile(Path(package.__file__).parent / "docs_gen" / "verify_delivery.py", out / "verify_delivery.py")
    files = package.delivered_files(out)
    (out / "manifest.json").write_text(json.dumps(package.make_manifest(out, files, "t")))
    return subprocess.run([sys.executable, "-I", str(out / "verify_delivery.py")], capture_output=True, text=True, cwd=str(out))


def test_maj19_verify_delivery_joins_guide_final_checks_with_tests(tmp_path):
    ok = _delivery(tmp_path / "1", ["A", "B"], ["A", "B"])
    assert ok.returncode == 0, ok.stdout
    dropped = _delivery(tmp_path / "2", ["A"], ["A", "B"])
    assert dropped.returncode == 1 and "every tests[] record is in the guide's final checks: B" in dropped.stdout


def test_maj21_stale_not_produced_label_fails_verify_delivery(tmp_path):
    from gstudio.docs_gen.util import link

    assert link("checks/delivery-checks.json", tmp_path) == "`checks/delivery-checks.json` (written at packaging)"
    assert link("nope.md", tmp_path) == "`nope.md` (not produced)"
    r = _delivery(tmp_path / "3", ["A"], ["A"], stale="BUILD_GUIDE.md")
    assert r.returncode == 1 and "PROJECT.md marks no shipped file" in r.stdout


# ---------------------------------------------------------------- foundation's requests to pipeline (this round)


def test_consolidation_never_swaps_in_a_different_part():
    clone = {"vendor": "Probots", "url": "https://probots.in/clone", "price_inr_seen": 300, "seen_on": "2026-11-01", "status": "page_read",
             "gst": "incl", "same_part": False}
    D = {"id": "D", "name": "D", "role": "display", "price_inr_planning": [200, 400],
         "sourcing_in": [{"vendor": "Lonely", "url": "https://l.in/d", "price_inr_seen": 310, "seen_on": "2026-11-01", "status": "page_read",
                          "gst": "incl", "selected": True}, clone]}
    E = {"id": "E", "name": "E", "role": "display", "price_inr_planning": [50, 90],
         "sourcing_in": [{"vendor": "Probots", "url": "https://probots.in/e", "price_inr_seen": 60, "seen_on": "2026-11-01", "status": "page_read"}]}
    spec = {"name": "x", "derived": {"instances": [{"ref": "D1", "module": "D"}, {"ref": "E1", "module": "E"}], "harness": [], "hardware": []}}
    rows, _ = bom.build_purchased(spec, Lib([D, E]), {})
    d = next(r for r in rows if r["name"] == "D")
    assert d["vendor"] == "Lonely" and "different part" in d["consolidation_note"]
    assert bom.eligible_alternative(clone) is False


def test_receipt_falls_back_to_the_library_snapshot_with_its_own_date(tmp_path, monkeypatch):
    monkeypatch.setenv("GSTUDIO_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("GSTUDIO_PRICE_KEY", "k")
    lib_root = tmp_path / "library"
    (lib_root / "references" / "listings").mkdir(parents=True)
    (lib_root / "references" / "listings" / "p-sd.html").write_bytes(LDJSON)
    (lib_root / "references" / "index.json").write_text(json.dumps({"files": [{"path": "listings/p-sd.html",
                                                                               "sha256": hashlib.sha256(LDJSON).hexdigest()}]}))
    m = {"id": "SD", "name": "SD", "role": "storage", "price_inr_planning": [41, 49],
         "sourcing_in": [{"vendor": "Probots", "url": "https://probots.co.in/sd.html", "price_inr_seen": 49, "status": "page_read", "gst": "incl",
                          "checked_at": "2026-11-01T13:00:00Z", "snapshot": "references/listings/p-sd.html", "sku": "PRO1129"}]}
    r = bom._row("SD", 1, m, module_id="SD", catalog_row=None, refs=[], model_parts=[], kind="module")
    assert r["listing"]["library_snapshot_path"] == "references/listings/p-sd.html" and r["seen_on"] == "2026-11-01T13:00:00Z"
    out = tmp_path / "out"
    rep = pricecheck.check_rows([r], out, fetch=lambda u: (503, "text/html", b""), library_root=lib_root)
    assert rep["signed"] == 1 and rep["from_library_snapshot"] == ["SD"] and r["seen_on"] == "2026-11-01T13:00:00Z"
    assert pricecheck.verify_row(r, out)[0] and "live re-read failed" in r["notes"]
    cp = pricecheck.copy_library_snapshots([r], out, lib_root)
    assert cp == {"copied": 1, "sha256_mismatch": []} and (out / "references" / "library" / "listings" / "p-sd.html").exists()


def test_storage_card_needs_no_cad_model_and_purchased_fields_fail_without_sku(tmp_path):
    card = {"id": "STORAGE_MICROSD_32GB_A1", "name": "32 GB microSD card", "role": "storage_card", "price_inr_planning": [300, 600],
            "sourcing_in": [{"vendor": "A", "url": "https://a.in/c", "price_inr_seen": 399, "status": "page_read", "sku": "SD32"}]}
    spec = {"name": "x", "derived": {"instances": [{"ref": "CARD1", "module": card["id"], "role": "storage_card"}], "harness": [], "hardware": []}}
    rows, _ = bom.build_purchased(spec, Lib([card]), {})
    assert rows[0]["no_cad_model"] is True and rows[0]["sku"] == "SD32"
    cs = {c["id"]: c for c in checks.cross_checks(tmp_path, spec, {"purchased": rows}, None, Lib([card]))}
    assert "bom-cad-join" not in cs or cs["bom-cad-join"]["status"] == "passed"


def test_min22_real_backplane_matches_espressif_header_tables_and_a_swap_fails(tmp_path):
    from gstudio.library import Library

    lib = Library()
    p = checks._find_header_table(lib)
    if p is None:
        pytest.skip("no saved DevKitC-1 user guide under library/references")
    tab = checks.parse_devkit_tables(p)
    assert len(tab) == 44 and tab["J1-4"][0] == "4" and "GPIO4" in tab["J1-4"][1] and tab["J3-2"][0] == "TX" and "GPIO43" in tab["J3-2"][1]
    cs = {c["id"]: c for c in checks.pin_map_checks(tmp_path, lib, None)}
    assert cs["devkit-header-table"]["status"] == "passed" and cs["backplane-gpio-labels"]["status"] == "passed", cs

    class Swapped:
        _backplane_path = lib._backplane_path

        def slot(self, sid):
            s = copy.deepcopy(lib.slot(sid))
            a = next(x for x in s["pins"] if x["pin"] == "J1-4")
            b = next(x for x in s["pins"] if x["pin"] == "J1-5")
            a["gpio"], b["gpio"] = b["gpio"], a["gpio"]
            return s
    cs = {c["id"]: c for c in checks.pin_map_checks(tmp_path, Swapped(), None)}
    assert cs["devkit-header-table"]["status"] == "failed" and "J1-4: GPIO5" in cs["devkit-header-table"]["detail"]
    assert cs["backplane-gpio-labels"]["status"] == "failed"


def test_maj25_opencart_price_element_and_conflicts():
    page = b'<div><span id="price_old">Rs.1449.00</span></div><script>"price": "Rs.1449.00"</script>'
    got = pricecheck.parse_listing(page, "text/html", "https://e.in/x")
    assert got["price"] == 1449.0 and got["method"].startswith("price element")
    two = b'<span id="price_old">Rs.1449.00</span><span id="price_new">Rs.1299.00</span>'
    assert "disagree" in pricecheck.parse_listing(two, "text/html", "https://e.in/x")["error"]
    assert "error" in pricecheck.parse_listing(b"<p>only Rs. 99 in prose</p>", "text/html", "https://e.in/x")


def test_prewired_module_gets_no_make_to_drawing_entry():
    spk = {"id": "SPK", "name": "Speaker", "role": "speaker", "price_inr_planning": [80, 250], "sourcing_in": [],
           "prewire": {"by": "factory", "lead": "JST-PH 2.0 2p 100 mm"}}
    cab = {"id": "CABLE_JSTPH_2P_150", "name": "JST-PH 2p both ends 150 mm", "role": "harness", "price_inr_planning": [30, 100], "sourcing_in": []}
    lib = Lib([spk, cab])
    el = {"instances": [{"ref": "SPK1", "module": "SPK", "harness": {"cable": "JST-PH 2.0 2p", "length_mm": 150, "wires": []}}]}
    rs = bom._row("Speaker", 1, spk, module_id="SPK", catalog_row=None, refs=["SPK1"], model_parts=[], kind="module")
    rc = cable_row(cab, ["SPK1"])
    kinds = [w["kind"] for w in bom.factory_work([rs, rc], el, lib)]
    assert kinds == ["prewire"]


def test_min6_route_label_spade_size_is_not_a_length():
    assert checks.route_cable_length({"cable": "JST-PH 2.0 3-pin to 2 x 2.8 mm female spade harness, 300 mm"}) == 300.0
    assert checks.route_cable_length({"cable": "JST-PH 2.0 2p 100 mm", "cable_length_mm": 150.0}) == 150.0
    assert checks.route_cable_length({"cable": "no length"}) is None
