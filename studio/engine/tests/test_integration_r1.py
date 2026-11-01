"""REVIEW-1 integration fixes (integrator, 2026-11-01): one test per cross-owner fix made after the area rounds.

- MIN-2 / MAJ-29: normalize lowers the LED cap until the +5V drop budget holds with the 50 mm night-light ring.
- MAJ-22: the backplane is priced as a populated BP1 PCBA from pcb/pcba.json; the USB-C receptacle is counted once.
- MAJ-24 / MIN-25: feet from an Indian retail listing (Sunrom 4436), the ID label factory-made to a drawing, no IndiaMART row.
- MIN-3: `power-entry` replaces `no-mains` and needs the CAD's cord load path; the CAD's pull check reaches tests[].
- MAJ-18 / MIN-19 / MAJ-38: package delivery checks for the speech round trip and home paths; one firmware.bin.
- MIN-19: the PCB stage rewrites build-machine paths in its text outputs.
- MAJ-30 / MAJ-34: the ear decor model carries `roundness`.
"""

from __future__ import annotations

import copy
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))

from gstudio import checks  # noqa: E402
from gstudio.library import Library  # noqa: E402
from gstudio.normalize import normalize  # noqa: E402
from gstudio.order import bom  # noqa: E402
from gstudio.order.power import drop_budget  # noqa: E402
from gstudio.electrical import power_budget  # noqa: E402


@pytest.fixture(scope="module")
def lib():
    return Library()


def _example(slug: str) -> dict:
    return json.loads((ROOT / "examples" / slug / "spec.json").read_text())


# ------------------------------------------------------------------------------------------- MIN-2 / MAJ-29
def test_min2_led_cap_lowered_until_the_drop_budget_holds(lib):
    sp, rep = normalize(_example("bedtime-robot"), lib)
    assert rep["ok"], rep["errors"]
    rows = [{"ref": i.ref, "module": i.module} for i in sp.derived.instances]
    adapter = next((lib.get(i.module) for i in sp.derived.instances if i.role == "adapter"), None)
    db = drop_budget(power_budget(sp, lib), lib.backplane, lambda m: lib.get(m) if lib.has(m) else None, rows, adapter)
    assert db["ok"] is True, db["equation"]
    pct = sp.behaviour.settings["led_max_percent"]
    assert 10 <= pct < 45
    assert any("led_max_percent: 45 ->" in a for a in rep["added"])
    # at the old default the same budget fails: the cap is what makes it hold
    sp2 = copy.deepcopy(sp)
    sp2.behaviour.settings = {**sp.behaviour.settings, "led_max_percent": 45}
    assert drop_budget(power_budget(sp2, lib), lib.backplane, lambda m: lib.get(m) if lib.has(m) else None, rows, adapter)["ok"] is False


def test_min2_no_change_when_the_budget_already_holds(lib):
    sp, rep = normalize(_example("desk-buddy"), lib)
    assert rep["ok"], rep["errors"]
    assert not any("led_max_percent" in a and "drop budget" in a for a in rep["added"])


# ------------------------------------------------------------------------------------------- MAJ-22
PCBA = {"design_id": "3cc45c17b7e6", "status": "ESTIMATE", "includes_usb_c_receptacle": True, "complete": True,
        "excludes": ["Indian customs duty and IGST on import: not modelled", "GST (the order stage applies it)"],
        "batches": {"5": {"inr_per_board": {"low": 945, "high": 2289}}, "100": {"inr_per_board": {"low": 275, "high": 819}}}}


def test_maj22_bp1_priced_from_pcba_and_usb_c_counted_once():
    rows = [{"name": "Standard backplane PCB backplane-v1 (ours)", "module_id": "BACKPLANE_V1", "role": "backplane",
             "price_inr_planning": [120, 450], "evidence_tier": "estimate", "url": None, "vendor": None, "sku": None},
            {"name": "USB-C 2.0 receptacle on backplane", "module_id": "USBC_RECEPTACLE_GCT_USB4105", "role": "power_in",
             "mounted_on": "backplane PCB", "price_inr_planning": [80, 250], "vendor": "DigiKey (US site)", "url": "https://x", "sku": "USB4105-GF-A"}]
    reasons = bom.apply_pcba(rows, PCBA)
    bp, usb = rows
    assert bp["sku"] == "BACKPLANE-V1-3CC45C17" and bp["factory_make"]["drawing"] == bp["sku"]
    assert bp["price_inr_planning"] == [945.0, 2289.0] and bp["price_inr_planning_volume"] == [275.0, 819.0]
    assert "backplane-v1 (ours)" not in bp["name"]
    assert usb["included_in"] == "BP1 PCBA" and usb["price_inr_planning"] == [0.0, 0.0] and usb["vendor"] is None
    assert any("customs" in r for r in reasons)
    from gstudio.order.quote import part_range

    assert part_range(bp)[0] == [275.0, 819.0]          # steady state: the volume batch
    assert part_range(bp, frac=0.4)[0] == [945.0, 2289.0]  # one-off / batch: the pilot batch of 5
    assert bom.apply_pcba(rows, None) == []


# ------------------------------------------------------------------------------------------- MAJ-24 / MIN-25
def test_min25_feet_are_indian_retail_and_the_label_is_factory_made(lib):
    sp, rep = normalize(_example("bedtime-robot"), lib)
    feet = next(h for h in sp.derived.hardware if h.group == "Feet")
    assert feet.module == "FEET_RUBBER_8X2_5_SUNROM_4436"
    rows, _ = bom.build_purchased(sp.to_json_dict(True) if hasattr(sp, "to_json_dict") else sp.model_dump(), lib, {}, None)
    f = next(r for r in rows if r["module_id"] == "FEET_RUBBER_8X2_5_SUNROM_4436")
    assert f["vendor"] == "Sunrom" and f["sku"] == "4436" and f["packSize"] == 10 and f["url"].startswith("https://www.sunrom.com/")
    lab = next(r for r in rows if r["module_id"] == "LABEL_QR_POLYESTER_50X25_PACK")
    assert lab["sku"] == "GS-LBL-QR-50X25" and lab["factory_make"]["drawing"] == "GS-LBL-QR-50X25" and lab["made_to_drawing"]
    assert not any("indiamart" in str(r.get("vendor") or "").lower() or "indiamart" in str(r.get("url") or "") for r in rows)
    work = bom.factory_work(rows, None, lib)
    assert any(w["kind"] == "make_to_drawing" and w["module_id"] == "LABEL_QR_POLYESTER_50X25_PACK" for w in work)


def test_min25_snapshot_of_the_feet_listing_is_saved_and_indexed():
    import hashlib

    ix = json.loads((ROOT / "library" / "references" / "index.json").read_text())
    e = next(f for f in ix["files"] if f["path"] == "listings/sunrom-4436-rubber-feet-8mm.html")
    body = (ROOT / "library" / "references" / e["path"]).read_bytes()
    assert hashlib.sha256(body).hexdigest() == e["sha256"]
    assert b"Product Code 4436" in body and b"Rs.5.43" in body


# ------------------------------------------------------------------------------------------- MIN-3
def test_min3_power_entry_needs_the_cad_load_path_and_the_pull_check_reaches_tests(tmp_path):
    (tmp_path / "cad").mkdir()
    spec = {"electronics": {"power": {"input": "usb_c_5v"}}, "behaviour": {}, "derived": {"instances": []}}
    (tmp_path / "cad" / "cad-summary.json").write_text(json.dumps({"info": {"usb_c": {}}}))
    cs = checks.cross_checks(tmp_path, spec, {}, None)
    pe = next(c for c in cs if c["id"] == "power-entry")
    assert pe["status"] == "failed" and not any(c["id"] == "no-mains" for c in cs)
    (tmp_path / "cad" / "cad-summary.json").write_text(json.dumps({
        "info": {"usb_c": {"load_path": {"feature": "cord tie bridge: 2 tie slots above the USB-C opening"}}},
        "physical_checks": [{"id": "usb-cord-pull", "title": "Pull the USB-C cord along its axis", "expected": "the plug releases first"}]}))
    cs = checks.cross_checks(tmp_path, spec, {}, None)
    pe = next(c for c in cs if c["id"] == "power-entry")
    assert pe["status"] == "passed" and "cord tie bridge" in pe["detail"]
    tests = checks.physical_tests(cs, spec, {}, tmp_path)
    assert any(t["title"] == "Pull the USB-C cord along its axis" and t["kind"] == "physical" for t in tests)


# ------------------------------------------------------------------------------------------- MAJ-18 / MIN-19 / MAJ-38
def test_maj18_speech_roundtrip_delivery_check(tmp_path):
    from gstudio.package import speech_roundtrip_delivery

    sd = tmp_path / "firmware" / "sdcard"
    sd.mkdir(parents=True)
    (sd / "stories.json").write_text(json.dumps({"audio": True, "stories": []}))
    assert speech_roundtrip_delivery(tmp_path)[0] == "failed"
    (tmp_path / "firmware" / "speech-roundtrip.json").write_text(json.dumps(
        {"ok": True, "scope": "synthetic voice, no speaker or child tested", "segments_total": 30, "segments_passed": 30}))
    assert speech_roundtrip_delivery(tmp_path)[0] == "passed"
    (sd / "stories.json").write_text(json.dumps({"audio": False}))
    assert speech_roundtrip_delivery(tmp_path)[0] == "not_run"


def test_min19_home_paths_in_zip_and_maj38_exclusions(tmp_path):
    from gstudio.package import delivered_files, home_paths_in_zip

    (tmp_path / "cad" / "components").mkdir(parents=True)
    (tmp_path / "cad" / "components" / "REFERENCE_CableMic1.brep").write_text("x")
    (tmp_path / "cad" / "components" / "PRINT_Shell.brep").write_text("x")
    (tmp_path / "firmware" / "flash").mkdir(parents=True)
    (tmp_path / "firmware" / "flash" / "firmware.bin").write_text("x")
    (tmp_path / "firmware" / "firmware.bin").write_text("x")
    rel = {p.relative_to(tmp_path).as_posix() for p in delivered_files(tmp_path)}
    assert "cad/components/PRINT_Shell.brep" in rel and "firmware/firmware.bin" in rel
    assert "cad/components/REFERENCE_CableMic1.brep" not in rel and "firmware/flash/firmware.bin" not in rel
    z = tmp_path / "export.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("g/pcb/board.dsn", "(pcb pcb/board.dsn)")
    assert home_paths_in_zip(z)[0] == "passed"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("g/pcb/board.dsn", "(pcb /Users/someone/work/out/pcb/board.dsn)")
    assert home_paths_in_zip(z)[0] == "failed"


def test_min19_pcb_scrub_paths(tmp_path):
    from gstudio.pcb import reuse

    pcb = tmp_path / "pcb"
    pcb.mkdir()
    (pcb / "board.dsn").write_text('(pcb "/Users/someone/x/_out/v1/pcb/board.dsn")\n')
    (pcb / "freerouting.log").write_text("read /home/builder/work/v2/pcb/en/freerouting.log ok\n"
                                          "stale '/home/someone/.local/state/freerouting/logs/freerouting.log'\n")
    (pcb / "backplane-F_Cu.gbr").write_text("%unchanged /Users/a/pcb/x%\n")
    assert reuse.scrub_paths(pcb) == 2
    assert (pcb / "board.dsn").read_text() == '(pcb "pcb/board.dsn")\n'
    log = (pcb / "freerouting.log").read_text()
    assert "pcb/en/freerouting.log" in log and "/home/" not in log and "~/.local/state" in log
    assert "/Users/a/" in (pcb / "backplane-F_Cu.gbr").read_text()  # gerbers are never touched
    assert reuse.scrub_obj({"f": "/Users/x/y/pcb/a.json"}) == {"f": "pcb/a.json"}


# ------------------------------------------------------------------------------------------- MAJ-30 / MAJ-34
def test_maj34_ear_roundness_is_in_the_spec(lib):
    from gstudio.spec import Ear

    assert "roundness" in Ear.model_fields
    sp = _example("bedtime-robot")
    for d in sp.get("decor") or []:
        if d.get("type") == "ear":
            d["roundness"] = 0.85
    s2, rep = normalize(sp, lib)
    assert rep["ok"], rep["errors"]
    assert any(getattr(d, "roundness", None) == 0.85 for d in s2.decor if getattr(d, "type", "") == "ear") or \
        not any(d.get("type") == "ear" for d in sp.get("decor") or [])
    bad = copy.deepcopy(sp)
    for d in bad.get("decor") or []:
        if d.get("type") == "ear":
            d["roundness"] = 1.5
    if any(d.get("type") == "ear" for d in bad.get("decor") or []):
        with pytest.raises(Exception):
            normalize(bad, lib, raise_on_error=True)


# ------------------------------------------------------------------------------------------- e2e r1: rear kits on rear covers
def test_e2e_r1_rear_kit_on_a_rear_cover_is_rejected_with_a_fix(lib):
    """The e2e r1 Story Box put its speaker on the rear face of a rear_cover design; the CAD cut the kit at the seam and dropped
    649 + 95 mm3 fragments (print_single_solid) and had no speaker fit coupon. normalize now rejects that with a fix, and the
    same design with the speaker moved to the front validates."""
    spec = json.loads((Path(__file__).parent / "fixtures_integration" / "e2e-r1-story-box-v1-spec.json").read_text())
    _, rep = normalize(copy.deepcopy(spec), lib)
    errs = [e for e in rep["errors"] if e.get("code") == "rear_cover_face"]
    assert errs and "speaker" in errs[0]["msg"] and "front" in errs[0]["fix"]
    base = copy.deepcopy(spec)
    base["body"]["split"] = "base_plate"
    _, rep2 = normalize(base, lib)
    assert not [e for e in rep2["errors"] if e.get("code") == "rear_cover_face"]
