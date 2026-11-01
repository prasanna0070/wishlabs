"""REVIEW-1 integration: pull the facts each acceptance line needs from one built version folder (run on the VM).

    python3 -I tools/int/evidence.py <version dir> [<version dir> ...]  -> JSON on stdout
Read-only. Facts only: no check is re-implemented here beyond simple joins and counts.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path


def rj(p: Path, default=None):
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return default


def check(dc: dict, cid: str):
    for c in (dc or {}).get("checks") or []:
        if c.get("id") == cid:
            return {"status": c.get("status"), "detail": str(c.get("detail"))[:400]}
    return None


def facts(v: Path) -> dict:
    out: dict = {"dir": str(v)}
    st = rj(v / "status.json", {}) or {}
    out["stages"] = {s["id"]: s["state"] for s in st.get("stages") or []}
    out["verdict"] = st.get("verdict")
    out["buildable"] = st.get("buildable")
    dc = rj(v / "checks" / "digital-checks.json", {}) or {}
    out["digital_summary"] = dc.get("summary")
    out["digital_failed"] = [c["id"] for c in dc.get("checks") or [] if c.get("status") == "failed"]
    out["digital_warned"] = [c["id"] for c in dc.get("checks") or [] if c.get("status") == "warning"]
    cad = rj(v / "cad" / "digital-checks.json", {}) or {}
    for cid in ("collisions", "feature_module_clearance", "clearances", "unsupported_ceiling", "decor_inside_silhouette",
                "decor_on_flat_face", "min_wall", "print_single_solid", "fit_coupon", "bottom_edge_round", "power_entry",
                "unique_labels", "cable_routes"):
        out[f"cad:{cid}"] = check(cad, cid)
    for cid in ("harness-module-end", "wiring-via-join", "harness-straight-through", "firmware-storage-has-part",
                "story-audio-present", "night-light-diffused", "rail-voltage", "power-budget", "power-caps-in-firmware",
                "power-entry", "purchased-fields", "pack-size-known", "price-evidence", "price-receipts-signed",
                "harness-length-one-source", "harness-slack", "no-soldering", "devkit-header-table", "pcb-u1-pad-nets",
                "pins-h-hash", "manual-purchased-rows-used", "manual-module-ends", "manual-wiring-overlaps",
                "manual-priority-labels", "manual-step-titles", "pcb.silk_designators", "pcb.port_keying", "pcb.amp_sd_boot",
                "pcb.bom_orderable", "pcb.cpl_clean", "cad-bom-join"):
        out[f"chk:{cid}"] = check(dc, cid)
    out["records_without_artifact"] = [c.get("id") for c in dc.get("checks") or [] if not c.get("artifact")]
    out["details_with_list_syntax"] = [c.get("id") for c in dc.get("checks") or [] if re.search(r"\[['\"]", str(c.get("detail") or ""))]
    spec = rj(v / "spec.json", {}) or {}
    out["spec_name"] = spec.get("name")
    out["template"] = (spec.get("behaviour") or {}).get("template")
    out["settings"] = (spec.get("behaviour") or {}).get("settings")
    out["safety_flags"] = (spec.get("safety") or {}).get("flags")
    out["features"] = [(f.get("id"), f.get("module"), f.get("at")) for f in spec.get("features") or []]
    out["decor"] = [(d.get("type"), d.get("style"), d.get("size"), d.get("roundness")) for d in spec.get("decor") or []]
    out["body"] = {k: (spec.get("body") or {}).get(k) for k in ("family", "depth", "edge_round", "feet")}
    out["colors"] = spec.get("colors")
    b = rj(v / "build.json", {}) or {}
    pur = b.get("purchased") or []
    out["purchased_n"] = len(pur)
    out["purchased_missing_sku"] = [r["name"] for r in pur if not r.get("sku")]
    out["purchased_missing_url_and_drawing"] = [r["name"] for r in pur if not r.get("url") and not (r.get("made_to_drawing") or (r.get("factory_make") or {}).get("drawing"))]
    out["purchased_missing_pack"] = [r["name"] for r in pur if r.get("packSize") is None]
    out["purchased_no_gst_basis_but_seen"] = [r["name"] for r in pur if r.get("evidence_tier") == "seen" and not r.get("gst_basis")]
    seen = [r for r in pur if r.get("evidence_tier") == "seen"]
    out["seen_rows"] = len(seen)
    out["seen_missing_stock_or_iso"] = [r["name"] for r in seen if not ((r.get("listing") or {}).get("stock") and re.match(r"\d{4}-\d\d-\d\dT", str(r.get("seen_on") or "")))]
    out["indiamart_rows"] = [r["name"] for r in pur if "indiamart" in str(r.get("vendor") or "").lower() or "indiamart" in str(r.get("url") or "")]
    card = [r for r in pur if str(r.get("module_id") or "").startswith("STORAGE_MICROSD")]
    out["card_row"] = [{k: r.get(k) for k in ("name", "vendor", "sku", "price_inr_seen", "price_inr_planning", "evidence_tier")} for r in card]
    sdm = [r for r in pur if r.get("module_id") == "MICROSD_SPI_MODULE_5V"]
    out["sd_module_planning"] = [r.get("price_inr_planning") for r in sdm]
    out["adapter_row"] = [{k: r.get(k) for k in ("name", "in_box", "price_inr_seen", "vendor")} for r in pur if r.get("role") == "adapter"]
    out["bp1_row"] = [{k: r.get(k) for k in ("name", "sku", "price_inr_planning", "price_inr_planning_volume", "price_basis")} for r in pur if r.get("module_id") == "BACKPLANE_V1"]
    out["usbc_rows"] = [{k: r.get(k) for k in ("name", "included_in", "price_inr_planning")} for r in pur if r.get("role") == "power_in"]
    ins = [r for r in pur if r.get("role") == "inserts"]
    out["insert_rows"] = [{k: r.get(k) for k in ("name", "quantity", "packSize", "packsToBuy", "sku")} for r in ins]
    out["screw_rows"] = [{k: r.get(k) for k in ("name", "quantity", "sku")} for r in pur if r.get("role") == "screws"]
    out["feet_rows"] = [{k: r.get(k) for k in ("name", "quantity", "packSize", "vendor", "sku", "evidence_tier")} for r in pur if r.get("role") == "feet"]
    gk = [r for r in pur if r.get("role") == "gasket"]
    out["gasket_rows"] = [{k: r.get(k) for k in ("name", "modelPart", "modelParts", "use")} for r in gk]
    out["requiresSoldering_null"] = [r["name"] for r in pur if r.get("requiresSoldering") is None]
    hw = [r for r in pur if r.get("kind") == "cable"]
    out["cable_rows"] = [{k: r.get(k) for k in ("name", "sku", "made_to_drawing", "refs", "length_mm", "harness_length_mm")} for r in hw]
    # MAJ-23 hand recompute: seen rows at their GST-inclusive seen price, estimates at the one-off mode (0.70 of the range),
    # times whole packs bought (packSize None -> one set)
    import math
    tot = 0.0
    for r in pur:
        qty = int(r.get("quantity") or 1)
        pk = r.get("packSize")
        q_one = 1 if pk is None else (math.ceil(qty / int(pk)) if int(pk) > 1 else qty)
        if r.get("evidence_tier") == "seen" and r.get("price_inr_incl_gst") is not None:
            unit = float(r["price_inr_incl_gst"])
        elif r.get("price_inr_planning"):
            lo, hi = r["price_inr_planning"]
            unit = float(lo) + 0.70 * (float(hi) - float(lo))
        else:
            continue
        tot += unit * q_one
    out["hand_parts_total_inr"] = round(tot, 2)
    out["rows_without_gst_basis_with_price"] = [r["name"] for r in pur if r.get("price_inr_seen") is not None and not r.get("gst_basis")]
    fw = b.get("factory_work") or []
    out["factory_work_kinds"] = sorted({w.get("kind") for w in fw})
    out["continuity_tests"] = [w.get("refs") for w in fw if w.get("kind") == "continuity_test"]
    wiring = b.get("wiring") or {}
    names = {r["name"] for r in pur}
    conns = wiring.get("connections") or []
    out["wiring_connections"] = len(conns)
    out["wiring_via_not_purchased"] = sorted({c.get("via") for c in conns if c.get("via") and c.get("via") not in names})[:10]
    out["wiring_u1_rows"] = sum(1 for c in conns if str(c.get("from") or "").startswith("U1.J"))
    vias = {}
    for c in conns:
        ref = str(c.get("from") or "").split(".")[0]
        if ref in ("DISP1", "MIC1", "BTN1", "LED1", "SPK1") and c.get("via"):
            vias.setdefault(ref, c["via"])
    out["module_vias"] = vias
    wparts = wiring.get("parts") or []
    out["wiring_parts_mic"] = [{k: p.get(k) for k in ("id", "item", "module_id", "sizeMm")} for p in wparts if p.get("id") == "MIC1"]
    tests = b.get("tests") or []
    out["tests_n"] = len(tests)
    out["tests_titles"] = [(t.get("title"), t.get("kind"), t.get("status")) for t in tests]
    steps = b.get("steps") or []
    out["step_ids"] = [s.get("id") for s in steps]
    out["step_titles"] = [s.get("title") for s in steps]
    titles = [s.get("title") for s in steps]
    out["repeated_step_titles"] = sorted({t for t in titles if titles.count(t) > 1})
    o = b.get("order") or {}
    out["order"] = {k: o.get(k) for k in ("status", "list_price_inr", "parts_total_inr", "delivered_price_inr")}
    fwb = b.get("firmware") or {}
    out["firmware"] = {k: fwb.get(k) for k in ("compiled", "tests_passed", "tests_total", "tests_product", "tests_shared", "flash")}
    pcb = b.get("pcb") or {}
    out["pcb"] = {k: pcb.get(k) for k in ("drc_errors", "erc_errors", "routed", "revision", "pcb_key", "reused")}
    el = rj(v / "electrical.json", {}) or {}
    inst = {i.get("ref"): i for i in el.get("instances") or []}
    dh = (inst.get("DISP1") or {}).get("harness") or {}
    out["disp1_harness"] = {k: dh.get(k) for k in ("module_end_order", "crossed", "made_to_drawing", "sku", "cable_module")}
    if dh.get("module_end_housings"):
        out["disp1_pos1"] = dh["module_end_housings"][0]["positions"][0]
    pw = el.get("power") or {}
    out["power_supply_basis"] = (pw.get("supply") or {}).get("basis")
    out["drop_budget"] = {k: (pw.get("drop_budget") or {}).get(k) for k in ("equation", "ok", "rail_min_v")}
    out["power_caps"] = pw.get("caps")
    q = rj(v / "order" / "quote.json", {}) or {}
    out["quote_status"] = q.get("status")
    out["quote_parts_total"] = q.get("parts_total_inr")
    out["quote_delivered"] = q.get("delivered_price_inr") if not isinstance(q.get("delivered_price_inr"), dict) else {k: q["delivered_price_inr"].get(k) for k in ("one_off", "label", "includes", "excludes") if k in q["delivered_price_inr"]}
    out["quote_inbound_vendors"] = (q.get("inbound_shipping") or {}).get("vendor_count")
    out["quote_consolidation"] = str((q.get("inbound_shipping") or {}).get("consolidation"))[:300]
    out["quote_reasons"] = [r for sc in q.get("scenarios") or [] for r in (sc.get("reasons") or [])][:0]
    qtxt = json.dumps(q)
    out["quote_mentions"] = {w: (w in qtxt) for w in ("microSD card", "Backplane PCBA BP1", "Raspberry Pi 15W", "missing_prices")}
    cfg = (v / "firmware" / "include" / "config.h")
    ctext = cfg.read_text() if cfg.exists() else ""
    out["config_h"] = [ln for ln in ctext.splitlines() if re.search(r"GS_CFG_(NETWORK|LED_MAX_PERCENT|NIGHT_LEVEL_MAX|NIGHT_DUTY|CAPTURE_MODE|CLIP_MS|VOLUME_MAX|LED_CAP)\b", ln)]
    sj = rj(v / "firmware" / "sdcard" / "stories.json", None)
    if isinstance(sj, dict):
        files = [f for s in sj.get("stories") or [] for f in s.get("files") or []]
        out["stories"] = {"audio": sj.get("audio"), "files": len(files),
                          "missing": [f for f in files if not (v / "firmware" / "sdcard" / f).is_file()][:5]}
    rt = rj(v / "firmware" / "speech-roundtrip.json", None)
    if isinstance(rt, dict):
        out["speech_roundtrip"] = {k: rt.get(k) for k in ("ok", "scope", "segments_total", "segments_passed", "worst_segment_wer", "names_exact")}
    out["prompts"] = sorted(p.name for p in (v / "firmware" / "sdcard" / "prompts").glob("*.wav")) if (v / "firmware" / "sdcard" / "prompts").exists() else []
    g = (v / "BUILD_GUIDE.md")
    gt = g.read_text() if g.exists() else ""
    out["guide"] = {"banner": "known-issues-banner" in gt and "Do not print" in gt, "known_issue_lines": gt.count("Known design issue"),
                    "as_shown": gt.count("as shown in the image"), "bis_crs": "BIS CRS" in gt, "5v3a": "5 V 3 A" in gt,
                    "pipeline_words": [w for w in ("normalizer", "blueprint", "catalog", "slot map", "backplane-v1") if w in gt],
                    "selftest_line": "GS-SELFTEST" in gt, "sha256sum -c": "sha256sum -c" in gt, "UART": "UART" in gt,
                    "speaker_gasket_on_mic": bool(re.search(r"mic[^\n]{0,80}Speaker gasket", gt, re.I)), "ellipsis_labels": gt.count("…")}
    fc = re.search(r"## Final checks to record\n(.*?)(\n## |\Z)", gt, re.S)
    out["guide_final_check_titles"] = re.findall(r"^- \*\*(.+?):\*\*", fc.group(1), re.M) if fc else []
    ms = rj(v / "manual" / "manual-summary.json", {}) or {}
    out["manual_summary"] = {k: ms.get(k) for k in ("tests", "tests_failed", "images_png", "images_svg", "final_checks_match_tests",
                                                    "purchased_rows_unused", "harness_module_end_missing", "wiring_overlaps",
                                                    "labels_dropped_priority", "repeated_step_titles", "known_issue_banner")}
    dl = rj(v / "checks" / "delivery-checks.json", {}) or {}
    out["delivery"] = {k: dl.get(k) for k in ("manualImagesPng", "manualImagesSvg", "storyAudio", "listingSnapshots", "buildable")}
    pj = (v / "PROJECT.md").read_text() if (v / "PROJECT.md").exists() else ""
    out["project_md"] = {"not_produced": pj.count("(not produced)"), "brief_version": bool(re.search(r"[Bb]rief version \d", pj)),
                         "5v3a": "5 V 3 A" in pj, "bis_crs": "BIS CRS" in pj}
    pe = (v / "PARTS_EVIDENCE.md").read_text() if (v / "PARTS_EVIDENCE.md").exists() else ""
    out["parts_evidence_totals"] = re.findall(r"₹[\d,]+", pe)[:12]
    out["parts_evidence_bad_words"] = [w for w in ("1206", "GPIO15/16", "Schottky") if w in pe]
    comps = rj(v / "cad" / "components.json", {}) or {}
    labels = [c.get("label") for c in comps.values() if isinstance(c, dict)]
    out["duplicate_labels"] = sorted({x for x in labels if labels.count(x) > 1})
    refs = [c.get("ref") for c in comps.values() if isinstance(c, dict) and c.get("ref")]
    out["duplicate_refs"] = sorted({x for x in refs if refs.count(x) > 1})
    out["decor_components"] = [(d.get("name"), d.get("spec_path"), d.get("label")) for d in (comps.get("PRINT_Shell") or {}).get("decor") or []]
    out["source_files"] = sum(len(c.get("source_files") or []) for c in comps.values() if isinstance(c, dict))
    cs = rj(v / "cad" / "cad-summary.json", {}) or {}
    out["cad_summary"] = {"decor_adjustments": cs.get("decor_adjustments"), "tabs": {k: (vv or {}).get("ring_tabs_deg") for k, vv in (cs.get("kits") or {}).items() if isinstance(vv, dict) and (vv or {}).get("ring_tabs_deg")},
                          "fragments": (cs.get("info") or {}).get("fragments"), "load_path": ((cs.get("info") or {}).get("usb_c") or {}).get("load_path", {}).get("feature") if isinstance(((cs.get("info") or {}).get("usb_c") or {}).get("load_path"), dict) else None,
                          "recolored": cs.get("recolored"), "timings_total": (cs.get("timings") or {}).get("total"),
                          "print_materials": sorted({str((p or {}).get("material")) for p in (cs.get("print") or {}).values()})}
    glb = v / "cad" / "assembly.glb"
    if glb.exists():
        import struct

        bb = glb.read_bytes()
        n = struct.unpack("<I", bb[12:16])[0]
        gj = json.loads(bb[20:20 + n])
        scr = [nd.get("name") for nd in gj.get("nodes") or [] if "#screen" in str(nd.get("name"))]
        em = [m.get("name") for m in gj.get("materials") or [] if m.get("emissiveTexture")]
        out["glb"] = {"screen_nodes": scr, "emissive_textured_materials": em,
                      "decor_nodes": [nd.get("name") for nd in gj.get("nodes") or [] if "#decor" in str(nd.get("name"))]}
    z = v / "export.zip"
    if z.exists():
        with zipfile.ZipFile(z) as zf:
            nl = zf.namelist()
        out["zip"] = {"bytes": z.stat().st_size, "entries": len(nl),
                      "firmware_bins": [x for x in nl if x.endswith("/firmware.bin")],
                      "reference_breps": sum(1 for x in nl if "/cad/components/REFERENCE_" in x),
                      "concept_refs": [x for x in nl if "/references/concept" in x],
                      "project_record": any(x.endswith("project-record.json") for x in nl),
                      "render_manual": any(x.endswith("manual/render_manual.py") for x in nl),
                      "figures_json": any(x.endswith("manual/figures.json") for x in nl),
                      "source_files": sum(1 for x in nl if "/references/library/sources/" in x),
                      "listings": sum(1 for x in nl if "/references/listings/" in x)}
    pk = rj(v / ".gstudio" / "stages" / "package.json", {}) or {}
    out["package_checks"] = [(c.get("id"), c.get("status"), str(c.get("detail"))[:160]) for c in (pk.get("summary") or {}).get("checks") or []]
    pj2 = rj(v / "project.json", {}) or {}
    out["project_json"] = {k: pj2.get(k) for k in ("brief_version", "concept_id", "version", "spec_hash")}
    timings = {}
    for sid in out["stages"]:
        r = rj(v / ".gstudio" / "stages" / f"{sid}.json", {}) or {}
        timings[sid] = r.get("seconds") or r.get("elapsed_s") or (r.get("summary") or {}).get("timings_s", {}).get("total") if isinstance((r.get("summary") or {}).get("timings_s"), dict) else r.get("seconds")
    out["stage_seconds"] = timings
    return out


if __name__ == "__main__":
    print(json.dumps([facts(Path(a)) for a in sys.argv[1:]], indent=1, default=str))
