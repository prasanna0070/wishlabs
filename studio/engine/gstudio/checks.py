"""Digital checks: one place that says what was checked, by which stage, against which file.

    run(ctx) -> summary          (pipeline stage "checks")

1. Re-assembles build.json from its fragments (order's purchased/printed/order, manual's steps/wiring/tests,
   firmware and pcb blocks from their summaries and files), so no stage's write can be lost to a race.
2. Collects every stage's own checks (`summary["checks"]`) and the well-known check files
   (cad/digital-checks.json, pcb/drc.json, pcb/erc.json, firmware/test-results.txt).
3. Adds the pipeline's cross-stage joins: QB-06 CAD <-> BOM, QB-15 cable length, QB-16 BREP vs mesh volume,
   QB-07 watertight print meshes, QB-18 wiring <-> BOM, QB-19 firmware pins <-> electrical, QB-20 power budget,
   QB-21 no mains, QB-29/30 step shape, QB-34 manual images, and the REVIEW-1 checks: harness module ends, via joins
   and straight-through leads (BLK-3, MAJ-4), supply limit, default USB and rail voltage (MAJ-5, MIN-2), firmware caps
   and the night light (MAJ-29), price receipts (MAJ-25), story audio (BLK-4), storage card (BLK-5), harness lengths
   (MIN-6) and the independent pin map (MIN-22).
4. Writes checks/digital-checks.json (every record names its artifact, MIN-28) and build.json tests[]: the manual's
   tests, one digital test per stage family, and physical checks that follow every failed or warning digital check
   (MIN-20). Physical checks are never "passed".
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Iterable, Optional

STAGE = "checks"
STATUS_ORDER = ("failed", "warning", "not_run", "passed")

LIMITS = [
    "Digital evidence only: geometry, file, schema, compile and unit-test checks run on the builder. No physical "
    "assembly, electrical, acoustic, thermal, drop or child-safety test was performed.",
    "Purchased-part shapes are simplified envelopes from published dimensions, not factory CAD.",
    "Cable routes are display-only envelopes; bend radius and fit are physical checks.",
    "Prices are planning ranges or dated retail snapshots, never signed receipts; the quote is not binding.",
]


# ============================================================================ helpers


def read_json(p: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def write_json(p: Path, data: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str))
    tmp.replace(p)


def norm_status(v: Any) -> str:
    if isinstance(v, bool):
        return "passed" if v else "failed"
    s = str(v or "").strip().lower()
    if s in ("pass", "passed", "ok", "true", "yes", "done", "success", "succeeded"):
        return "passed"
    if s in ("fail", "failed", "error", "false", "no"):
        return "failed"
    if s in ("warn", "warning", "warnings"):
        return "warning"
    return "not_run"


_PYLIST = re.compile(r"\[((?:'[^']*'(?:,\s*)?)+)\]")


def words(xs: Any) -> str:
    """Plain comma-joined words (MIN-28): never Python list syntax in a detail string."""
    if isinstance(xs, (list, tuple, set)):
        return ", ".join(str(x) for x in xs) if xs else "none"
    return str(xs)


def plain(s: Any) -> str:
    """A detail string with any "['a', 'b']" list repr turned into "a, b"."""
    if isinstance(s, (list, tuple, set)):
        return words(s)
    return _PYLIST.sub(lambda m: ", ".join(re.findall(r"'([^']*)'", m.group(1))), str(s or ""))


DEFAULT_ARTIFACT = {"normalize": "spec.json", "electrical": "electrical.json", "cad": "cad/digital-checks.json",
                    "pcb": "pcb/backplane.kicad_pcb", "firmware": "firmware/build-log.txt", "order": "build.json",
                    "manual": "manual/manual-summary.json", "pipeline": "build.json", "package": "manifest.json"}


def default_artifact(stage: str, cid: str) -> str:
    if stage == "order":
        if cid.startswith(("bed-fit:", "sliced:")):
            return "print/slice-stats.json"
        if cid.startswith("on-bed:"):
            return f"print/{cid.split(':', 1)[1]}.stl"
        if cid == "quote":
            return "order/quote.json"
    if stage == "pcb" and ("drc" in cid or "erc" in cid):
        return f"pcb/{'drc' if 'drc' in cid else 'erc'}.json"
    if stage == "pcb" and "gerber" in cid:
        return "pcb/backplane-gerbers.zip"
    return DEFAULT_ARTIFACT.get(stage, "build.json")


def check(cid: str, title: str, status: Any, detail: str = "", *, qb: str | None = None, stage: str = "pipeline",
          artifact: str | None = None, data: Any = None) -> dict:
    d = {"id": cid, "title": title, "status": status if status in STATUS_ORDER else norm_status(status), "detail": plain(detail),
         "stage": stage, "artifact": artifact or default_artifact(stage, cid)}
    if qb:
        d["qb"] = qb
    if data is not None:
        d["data"] = data
    return d


def normalize_checks(raw: Any, stage: str) -> list[dict]:
    """A stage's `checks` (list of dicts, or {name: bool|dict}) -> normalized check dicts."""
    out = []
    if isinstance(raw, dict):
        raw = [dict(v, id=k) if isinstance(v, dict) else {"id": k, "status": v} for k, v in raw.items()]
    for i, c in enumerate(raw or []):
        if not isinstance(c, dict):
            continue
        st = c.get("status", c.get("passed", c.get("ok", c.get("result"))))
        cid = str(c.get("id") or c.get("name") or c.get("title") or f"{stage}-{i + 1}")
        d = check(cid, str(c.get("title") or c.get("name") or cid), st if st in STATUS_ORDER else norm_status(st),
                  plain(c.get("detail") or c.get("message") or c.get("details") or ""),
                  qb=c.get("qb"), stage=stage, artifact=c.get("artifact") or c.get("file"))
        if c.get("kind") == "physical":
            continue  # physical checks are never digital results
        out.append(d)
    return out


def _stage_records(out: Path) -> dict:
    from gstudio.pipeline import stage_records

    return stage_records(out)


def _component_kind(name: str, c: dict) -> str:
    return str(c.get("kind") or name.split("_", 1)[0]).upper()


# ============================================================================ build.json assembly


def firmware_block(out: Path, summary: dict | None) -> Optional[dict]:
    s = summary or {}
    blk = s.get("build", {}).get("firmware") if isinstance(s.get("build"), dict) else None
    blk = dict(blk or s.get("firmware") or {}) if isinstance(blk or s.get("firmware"), dict) else {}
    fw = out / "firmware"
    if not fw.exists() and not blk:
        return None
    for k in ("compiled", "tests_passed", "tests_total", "bin_bytes", "size", "board", "framework", "template"):
        if k not in blk and s.get(k) is not None:
            blk[k] = s[k]
    blk.setdefault("framework", "arduino")
    blk.setdefault("board", "esp32-s3-devkitc-1")
    if "compiled" not in blk:
        blk["compiled"] = (fw / "firmware.bin").exists() and (fw / "firmware.bin").stat().st_size > 0
    if "size" not in blk and (fw / "size.json").exists():
        blk["size"] = read_json(fw / "size.json")
    if ("tests_total" not in blk or "tests_passed" not in blk) and (fw / "test-results.txt").exists():
        p, t = parse_test_results((fw / "test-results.txt").read_text(errors="ignore"))
        if t is not None:
            blk.setdefault("tests_passed", p)
            blk.setdefault("tests_total", t)
    blk.setdefault("tests_passed", s.get("tests_passed"))
    blk.setdefault("tests_total", s.get("tests_total"))
    blk.setdefault("binary", "firmware/firmware.bin" if (fw / "firmware.bin").exists() else None)
    if (fw / "firmware-merged.bin").exists():
        blk.setdefault("merged_binary", "firmware/firmware-merged.bin")
    if (fw / "web-flasher-manifest.json").exists():
        blk.setdefault("web_flasher_manifest", "firmware/web-flasher-manifest.json")
    return blk


def flasher_parts(out: Path) -> tuple[Optional[dict], list[tuple[str, Any, bool]]]:
    """(manifest, [(path relative to out, offset, exists)]) for an ESP Web Tools style manifest."""
    mp = out / "firmware" / "web-flasher-manifest.json"
    man = read_json(mp, None)
    if not isinstance(man, dict):
        return None, []
    parts = []
    for b in man.get("builds") or []:
        for prt in (b.get("parts") or []) if isinstance(b, dict) else []:
            if isinstance(prt, dict) and prt.get("path"):
                rel = (mp.parent / str(prt["path"])).resolve()
                try:
                    relp = rel.relative_to(out.resolve()).as_posix()
                except ValueError:
                    relp = str(prt["path"])
                parts.append((relp, prt.get("offset"), rel.is_file() and rel.stat().st_size > 0))
    return man, parts


def parse_test_results(text: str) -> tuple[Optional[int], Optional[int]]:
    """(passed, total) from PlatformIO / Unity output."""
    m = re.search(r"(\d+)\s+test cases?:\s*(?:(\d+)\s+failed,\s*)?(?:(\d+)\s+skipped,\s*)?(\d+)\s+succeeded", text)
    if m:
        total, ok = int(m.group(1)), int(m.group(4))
        return ok, total
    m = re.findall(r"(\d+)\s+Tests\s+(\d+)\s+Failures\s+(\d+)\s+Ignored", text)
    if m:
        total = sum(int(a) for a, _, _ in m)
        fails = sum(int(b) for _, b, _ in m)
        return total - fails, total
    m = re.search(r"Ran (\d+) tests?.*?\n\s*(OK|FAILED)", text, re.S)
    if m:
        total = int(m.group(1))
        return (total if m.group(2) == "OK" else None), total
    return None, None


def drc_counts(p: Path) -> dict:
    d = read_json(p, None)
    if not isinstance(d, dict):
        return {}
    if isinstance(d.get("violations"), list) or isinstance(d.get("sheets"), list):
        vs = list(d.get("violations") or [])
        for sh in d.get("sheets") or []:
            vs += list(sh.get("violations") or [])
        errors = sum(1 for v in vs if str(v.get("severity", "error")).lower() == "error")
        warnings = sum(1 for v in vs if str(v.get("severity", "")).lower() == "warning")
        return {"errors": errors, "warnings": warnings, "unconnected": len(d.get("unconnected_items") or []),
                "parity": len(d.get("schematic_parity") or [])}
    out = {}
    for k in ("errors", "error_count", "drc_errors", "erc_errors"):
        if isinstance(d.get(k), int):
            out["errors"] = d[k]
    for k in ("warnings", "warning_count"):
        if isinstance(d.get(k), int):
            out["warnings"] = d[k]
    if isinstance(d.get("unconnected"), int):
        out["unconnected"] = d["unconnected"]
    return out


def pcb_block(out: Path, summary: dict | None) -> Optional[dict]:
    s = summary or {}
    blk = s.get("build", {}).get("pcb") if isinstance(s.get("build"), dict) else None
    blk = dict(blk or s.get("pcb") or {}) if isinstance(blk or s.get("pcb"), dict) else {}
    pdir = out / "pcb"
    if not pdir.exists() and not blk:
        return None
    drc, erc = drc_counts(pdir / "drc.json"), drc_counts(pdir / "erc.json")
    for k in ("layers", "size_mm", "thickness_mm", "routed", "unrouted", "drc_errors", "drc_warnings", "erc_errors", "erc_warnings",
              "parity_issues", "vias", "track_length_mm", "files"):
        if k not in blk and s.get(k) is not None:
            blk[k] = s[k]  # the stage's own numbers win over a raw re-count (it knows its waivers)
    blk.setdefault("layers", 2)
    if "drc_errors" not in blk and "errors" in drc:
        blk["drc_errors"] = drc["errors"]
    if "erc_errors" not in blk and "errors" in erc:
        blk["erc_errors"] = erc["errors"]
    if "unrouted" not in blk and "unconnected" in drc:
        blk["unrouted"] = drc["unconnected"]
    if "routed" not in blk and "unrouted" in blk and isinstance(blk["unrouted"], int):
        blk["routed"] = blk["unrouted"] == 0
    if "files" not in blk:
        blk["files"] = sorted(str(p.relative_to(out)) for p in pdir.rglob("*") if p.is_file()
                              and p.suffix in (".kicad_pro", ".kicad_sch", ".kicad_pcb", ".zip", ".csv", ".pdf", ".svg", ".png", ".glb"))
    return blk


def assemble_build_json(out: Path, records: dict | None = None) -> dict:
    """Deterministic build.json from fragments + files (no timestamps), written in place and returned."""
    try:
        from gstudio.version import BUILD_SCHEMA
    except ImportError:  # pragma: no cover
        BUILD_SCHEMA = "gadget-build/1"
    records = records if records is not None else _stage_records(out)
    cur = read_json(out / "build.json", {}) or {}
    frag = read_json(out / ".gstudio" / "fragments" / "order.json", {}) or {}
    manual = (records.get("manual") or {}).get("summary") or {}
    mb = manual.get("build") if isinstance(manual.get("build"), dict) else {}
    mfrag = read_json(out / ".gstudio" / "fragments" / "manual.json", None)  # finalize may refresh steps/wiring/tests here
    if isinstance(mfrag, dict):
        mb = dict(mb, **{k: v for k, v in mfrag.items() if k in ("steps", "wiring", "tests")})
    b: dict = {"schema": BUILD_SCHEMA}
    b["purchased"] = frag.get("purchased", cur.get("purchased", []))
    b["printed"] = frag.get("printed", cur.get("printed", []))
    b["steps"] = mb.get("steps", cur.get("steps", []))
    b["wiring"] = mb.get("wiring", cur.get("wiring"))
    tests = [t for t in (mb.get("tests", cur.get("tests")) or []) if isinstance(t, dict) and t.get("source") != "checks"]
    b["tests"] = tests
    fw = firmware_block(out, (records.get("firmware") or {}).get("summary"))
    pcb = pcb_block(out, (records.get("pcb") or {}).get("summary"))
    b["firmware"] = fw if fw is not None else cur.get("firmware")
    b["pcb"] = pcb if pcb is not None else cur.get("pcb")
    b["order"] = frag.get("order", cur.get("order"))
    b["factory_work"] = frag.get("factory_work", cur.get("factory_work", []))
    for k, v in cur.items():  # keep anything else a stage added (e.g. hardware)
        b.setdefault(k, v)
    b["wiring"] = fix_wiring(b.get("wiring"), b.get("purchased") or [], _u1_pins())
    return b


def _library():
    try:
        from gstudio.library import Library

        return Library()
    except Exception:  # noqa: BLE001 - checks still run without the library
        return None


def _u1_pins() -> list[dict]:
    lib = _library()
    try:
        return list(lib.slot("U1").get("pins") or []) if lib is not None else []
    except Exception:  # noqa: BLE001
        return []


def _socket_of(c: dict) -> Optional[str]:
    if c.get("socket"):
        return str(c["socket"])
    m = re.match(r"^(?:board\s+)?socket\s*(\S+)?", str(c.get("via") or ""), re.I)
    if m and m.group(1):
        return m.group(1)
    parts = str(c.get("to") or "").split(".")
    return parts[1] if len(parts) > 1 else None


def fix_wiring(w: Any, purchased: list[dict], u1_pins: list[dict]) -> Any:
    """BLK-3: every cable `via` is the exact purchased[].name (+ via_sku, via_module_id); socket rows name the backplane's
    purchased row and their socket; "U1.header -> BP1.U1" becomes one row per DevKitC pin. Idempotent."""
    if not isinstance(w, dict):
        return w
    w = json.loads(json.dumps(w))
    cable_for = {ref: r for r in purchased if r.get("kind") == "cable" for ref in r.get("refs") or []}
    bp = next((r for r in purchased if r.get("module_id") == "BACKPLANE_V1" or str(r.get("role") or "") == "backplane"), None)
    conns = []
    for c in w.get("connections") or []:
        if not isinstance(c, dict):
            conns.append(c)
            continue
        c = dict(c)
        ref = str(c.get("from") or "").split(".")[0]
        if c.get("from") == "U1.header" and u1_pins:
            for p in u1_pins:
                if not isinstance(p, dict) or not p.get("pin"):
                    continue
                row = {"from": f"U1.{p['pin']}", "to": f"BP1.{p.get('socket') or 'U1'}.{p.get('socket_pin') or p['pin']}", "kind": "socket",
                       "via": bp["name"] if bp else "board socket", "socket": p.get("socket") or "U1", "net": p.get("net"),
                       "note": (f"DevKitC pin {p['pin']} ({p.get('devkit_label')}) in socket {p.get('socket')} pin {p.get('socket_pin')}"
                                + (f"; net {p['net']}" if p.get("net") else "; not connected")
                                + (f"; ESP32-S3 GPIO{p['gpio']}" if isinstance(p.get("gpio"), int) else ""))}
                if isinstance(p.get("gpio"), int):
                    row["gpio"] = p["gpio"]
                if bp:
                    row["via_sku"], row["via_module_id"] = bp.get("sku"), bp.get("module_id")
                conns.append(row)
            continue
        if c.get("kind") in ("cable", "wire") and ref in cable_for:
            r = cable_for[ref]
            if c.get("via") != r["name"] and not c.get("via_generic"):
                c["via_generic"] = c.get("via")
            c.update({"via": r["name"], "via_sku": r.get("sku"), "via_module_id": r.get("module_id")})
        elif c.get("kind") == "socket" and bp:
            sock = _socket_of(c)
            if sock:
                c["socket"] = sock
            c.update({"via": bp["name"], "via_sku": bp.get("sku"), "via_module_id": bp.get("module_id")})
        conns.append(c)
    w["connections"] = conns
    return w


def digital_tests(checks: list[dict], existing: list[dict] | None = None) -> list[dict]:
    """A short `tests[]` list of kind digital, one per stage family, derived from the checks. Families the manual
    stage already reports as digital tests (firmware, backplane DRC) are not repeated."""
    have = " ".join(str(t.get("title", "")).lower() for t in existing or [] if isinstance(t, dict) and t.get("kind") == "digital")
    covered = {"firmware"} if "firmware" in have else set()
    if "backplane" in have or "design-rule" in have or "drc" in have:
        covered.add("pcb")
    groups: dict[str, list[dict]] = {}
    for c in checks:
        groups.setdefault(c["stage"], []).append(c)
    titles = {"cad": ("CAD geometry checks", "every CAD check passes: valid solids, no unintended collisions, closed print meshes"),
              "pcb": ("Backplane PCB checks", "DRC and ERC report no errors and every net is routed"),
              "firmware": ("Firmware compile and unit tests", "the firmware compiles for ESP32-S3 and every native unit test passes"),
              "order": ("Print slicing and parts list", "every print part fits the bed and slices; every purchased row is complete"),
              "manual": ("Build manual checks", "every manual image exists and is referenced"),
              "pipeline": ("Cross-stage consistency", "CAD, parts list, wiring, firmware pins and electrical data agree")}
    out = []
    for st, (title, expected) in titles.items():
        cs = groups.get(st)
        if not cs or st in covered:
            continue
        sts = {c["status"] for c in cs}
        status = "failed" if "failed" in sts else ("passed" if sts <= {"passed", "warning"} else "not_run")
        out.append({"title": title, "expected": expected, "kind": "digital", "status": status, "source": "checks",
                    "detail": f"{sum(c['status'] == 'passed' for c in cs)}/{len(cs)} checks passed"})
    return out


PHYS_TEXT = {
    "collisions": "Before closing, confirm {pairs} clear each other by at least 1 mm (the CAD shows them overlapping); if they touch, stop and photograph both.",
    "cad-collisions": "Before closing, confirm the parts the CAD flags clear each other by at least 1 mm; if they touch, stop and photograph them.",
    "feature_module_clearance": "Before closing, confirm the feature modules the CAD flags ({detail}) clear each other by at least 0.5 mm.",
    "min_wall": "Measure the thinnest shell walls with calipers where the CAD flags them ({detail}); each should be at least 1.2 mm.",
    "purchased-fields": "When the parts arrive, write down the vendor, SKU and pack size actually received for the rows this check names ({detail}).",
    "pack-size-known": "When the parts arrive, count the pack and confirm it covers the quantity this build uses ({detail}).",
    "no-soldering": "On receipt, confirm the pin headers are fitted on the modules this check names; the factory solders any loose header before assembly ({detail}).",
    "harness-slack": "Before closing, coil and tie the longer harnesses ({detail}) so nothing is pinched or strained at the connectors.",
    "harness-length-one-source": "Before closing, measure each harness and confirm it reaches its port without strain ({detail}).",
    "feature_module_clearance": "Before closing, confirm the feature modules the CAD flags ({detail}) clear each other by at least 0.5 mm.",
}
COVERED_BY_POWER_CHECK = {"power-budget", "power-budget-uncapped", "power-default-usb", "rail-voltage", "power-caps-in-firmware"}


def _collision_pairs(out: Optional[Path]) -> str:
    d = read_json(out / "cad" / "digital-checks.json", None) if out else None
    c = next((x for x in (d or {}).get("checks") or [] if isinstance(x, dict) and x.get("id") == "collisions"), None) if isinstance(d, dict) else None
    pairs = [" and ".join(i.get("parts") or []) for i in ((c or {}).get("data") or {}).get("intersections") or [] if isinstance(i, dict)]
    return "; ".join(pairs) or "the colliding parts"


def physical_tests(checks: list[dict], spec: dict, records: dict | None = None, out: Optional[Path] = None) -> list[dict]:
    """MIN-20: one physical check for each failed or warning digital check, the child-voice check when voice capture exists,
    and the power check that carries the power-budget numbers (MAJ-5). Deterministic from the checks and the spec."""
    tests: list[dict] = []
    for c in checks:
        if c.get("status") not in ("failed", "warning") or str(c.get("id")).startswith("stage:"):
            continue  # a stage that did not complete is a rebuild, not something to check on the real parts
        if c.get("id") in COVERED_BY_POWER_CHECK and any(x.get("id") == "power-budget" for x in checks):
            continue  # the "USB-C supply and power draw" check below carries these numbers
        tmpl = PHYS_TEXT.get(str(c.get("id")))
        exp = (tmpl.format(pairs=_collision_pairs(out), detail=c.get("detail", "")) if tmpl else
               f"On the real parts, confirm what this digital check could not ({c.get('detail') or c.get('status')}); stop and photograph it if it does not hold.")
        tests.append({"title": f"Check on the real parts: {c.get('title')}", "expected": exp, "kind": "physical", "status": "not_run",
                      "source": "checks", "from_check": c.get("id"), "digital_status": c.get("status")})
    flags = set((spec.get("safety") or {}).get("flags") or [])
    fw = ((records or {}).get("firmware") or {}).get("summary") or {}
    mode = str(((fw.get("settings") or {}) if isinstance(fw, dict) else {}).get("capture_mode")
               or ((spec.get("behaviour") or {}).get("settings") or {}).get("capture_mode") or "")
    has_mic = any(str((i or {}).get("role")) == "mic" for i in ((spec.get("derived") or {}).get("instances") or []) if isinstance(i, dict))
    if has_mic and (mode == "child_voice_clip" or ("LOCAL_VOICE_CAPTURE" in flags and mode != "none")):
        pre = "" if mode == "child_voice_clip" else "If you switch voice capture on: "
        tests.append({"title": "Record a favourite name in your child's voice", "kind": "physical", "status": "not_run", "source": "checks",
                      "expected": (pre + "with the gadget closed, hold the button and have your child say a favourite name from about 30 cm; "
                                   "the playback is clear and the right name is kept. Scope: the builder's tests use synthetic speech only, "
                                   "which does not establish that the device hears a five-year-old's voice.")})
    # MIN-3 (integration): the physical checks the CAD names (e.g. the USB-C cord pull test) go into tests[]
    cad_sum = read_json(Path(out) / "cad" / "cad-summary.json", {}) if out else {}
    for pc in (cad_sum or {}).get("physical_checks") or []:
        if not isinstance(pc, dict):
            continue
        title = str(pc.get("title") or pc.get("name") or pc.get("id") or "").strip()
        if not title:
            continue
        tests.append({"title": title, "kind": "physical", "status": "not_run", "source": "checks", "from_cad": pc.get("id"),
                      "expected": str(pc.get("expected") or pc.get("how") or pc.get("detail") or pc.get("what") or
                                      "Do this check on the built gadget; it is not simulated.")})
    pb = next((c for c in checks if c.get("id") == "power-budget"), None)
    rv = next((c for c in checks if c.get("id") == "rail-voltage"), None)
    if pb:
        d, r = pb.get("data") or {}, (rv or {}).get("data") or {}
        tests.append({"title": "USB-C supply and power draw", "kind": "physical", "status": "not_run", "source": "checks",
                      "expected": (f"Power it from {d.get('supply') or 'a 5 V 3 A USB-C adapter'}, not a computer port. The calculated peak is "
                                   f"{d.get('capped_peak_ma')} mA with the firmware caps against {d.get('limit_ma')} mA ({d.get('basis')})"
                                   + (f", and the +5V rail is calculated at {r.get('rail_min_v')} V against the {r.get('required_min_v')} V "
                                      f"{r.get('required_by') or 'the most demanding module'} needs" if r.get("rail_min_v") is not None else "")
                                   + ((" (the calculation fails: " + "; ".join(f"{x.get('title')}: {x.get('status')}" for x in checks
                                                                          if x.get("id") in COVERED_BY_POWER_CHECK and x.get("status") in ("failed", "warning"))
                                       + ")") if any(x.get("id") in COVERED_BY_POWER_CHECK and x.get("status") == "failed" for x in checks) else "")
                                   + ". With the volume at its cap and the light on, a whole story plays with no reset or flicker; "
                                     "measure the +5V rail at the microSD module during playback if you can. These numbers are calculated, "
                                     "not measured.")})
    return tests


def final_tests(base: list[dict], checks: list[dict], spec: dict, records: dict | None = None, out: Optional[Path] = None) -> list[dict]:
    """build.json tests[]: the manual's own tests, then one digital test per stage family, then the physical checks the
    pipeline adds (MIN-20). The package stage recomputes this from checks/digital-checks.json (MIN-21)."""
    kept = [t for t in base or [] if isinstance(t, dict) and t.get("source") != "checks"]
    have = {str(t.get("title")) for t in kept}
    extra = [t for t in digital_tests(checks, kept) + physical_tests(checks, spec, records, out) if str(t.get("title")) not in have]
    return kept + extra


# ============================================================================ cross-stage checks


def parse_defines(text: str) -> dict[str, int]:
    """#define NAME 12 / constexpr int NAME = 12; -> {NAME: 12} (upper-cased names)."""
    out = {}
    for m in re.finditer(r"#define\s+([A-Za-z_]\w*)\s+\(?(-?\d+)\)?|(?:constexpr|const|static)\s+[\w:<> ]*?\b([A-Za-z_]\w*)\s*=\s*(-?\d+)\s*;", text):
        out[(m.group(1) or m.group(3)).upper()] = int(m.group(2) or m.group(4))
    return out


def polyline_length(points: Iterable) -> float:
    pts = [tuple(float(x) for x in p[:3]) for p in points or [] if isinstance(p, (list, tuple)) and len(p) >= 3]
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def cross_checks(out: Path, spec: dict, build: dict, electrical: dict | None, lib: Any = None,
                 records: dict | None = None) -> list[dict]:
    cs: list[dict] = []
    lib = lib if lib is not None else _library()
    comps = read_json(out / "cad" / "components.json", {}) or {}
    purchased = build.get("purchased") or []

    # QB-06 CAD <-> BOM join
    if comps:
        linked = set()
        for r in purchased:
            linked.update([r["modelPart"]] if r.get("modelPart") else [])
            linked.update(r.get("modelParts") or [])
        pcs = sorted(n for n, c in comps.items() if _component_kind(n, c if isinstance(c, dict) else {}) == "PURCHASE")
        unlinked = [n for n in pcs if n not in linked]
        dangling = sorted(n for n in linked if n not in comps)
        cs.append(check("cad-bom-join", "every PURCHASE_ component maps to a purchased row (modelPart)", not unlinked and not dangling,
                        (f"{len(pcs) - len(unlinked)}/{len(pcs)} linked" + (f"; unlinked: {words(unlinked)}" if unlinked else "")
                         + (f"; modelPart not in CAD: {words(dangling)}" if dangling else "")), qb="QB-06", artifact="cad/components.json"))
        nogeo = [r["name"] for r in purchased if r.get("kind") in ("module",) and not r.get("modelPart") and not r.get("in_box")
                 and not r.get("no_cad_model")]
        cs.append(check("bom-cad-join", "every purchased module has CAD geometry", "passed" if not nogeo else "warning",
                        "all linked" if not nogeo else f"no PURCHASE_ component for: {words(nogeo)}", qb="QB-06", artifact="cad/components.json"))
    else:
        cs.append(check("cad-bom-join", "every PURCHASE_ component maps to a purchased row", "not_run", "no cad/components.json", qb="QB-06",
                        artifact="cad/components.json"))

    # QB-16 BREP volume vs STL volume, QB-07 watertight (independent of the CAD stage's own check)
    try:
        from gstudio.order.slicer import stl_info
    except ImportError:  # pragma: no cover
        stl_info = None
    try:
        import trimesh  # type: ignore
    except ImportError:
        trimesh = None
    for stl in sorted((out / "print").glob("PRINT_*.stl")):
        name = stl.stem
        c = comps.get(name) if isinstance(comps.get(name), dict) else {}
        vol_c = c.get("volume_mm3", c.get("volumeMm3"))
        if stl_info and vol_c:
            v = stl_info(stl)["volume_mm3"]
            rel = abs(v - float(vol_c)) / max(1e-9, float(vol_c))
            cs.append(check(f"volume:{name}", f"{name}: BREP and STL volumes agree within 1%", rel <= 0.01,
                            f"components.json {float(vol_c):,.0f} mm3 vs STL {v:,.0f} mm3 ({rel:.2%})", qb="QB-16", artifact=f"print/{stl.name}"))
        if trimesh is not None:
            try:
                m = trimesh.load(stl, force="mesh", process=True)
                parts = len(m.split(only_watertight=False)) if hasattr(m, "split") else 1
                cs.append(check(f"watertight:{name}", f"{name}: closed mesh, one body", bool(m.is_watertight) and parts == 1,
                                f"watertight={bool(m.is_watertight)}, bodies={parts}, triangles={len(m.faces)}", qb="QB-07", artifact=f"print/{stl.name}"))
            except Exception as exc:  # noqa: BLE001
                cs.append(check(f"watertight:{name}", f"{name}: closed mesh, one body", "not_run", f"trimesh could not load it: {exc}", qb="QB-07",
                                artifact=f"print/{stl.name}"))

    # QB-15 / MIN-6 cable route length vs the one harness length source (electrical.json)
    cs += harness_length_checks(out, build, electrical, lib)

    # QB-18 wiring.parts[].item == purchased[].name
    wiring = build.get("wiring") or {}
    names = {r.get("name") for r in purchased}
    if isinstance(wiring, dict) and wiring.get("parts"):
        missing = sorted({str(p.get("item")) for p in wiring["parts"] if isinstance(p, dict) and p.get("item") not in names})
        cs.append(check("wiring-bom-join", "every wiring part is a purchased item", not missing,
                        "all joined" if not missing else f"not in purchased[]: {words(missing)}", qb="QB-18"))
        part_ids = {p.get("id") for p in wiring["parts"] if isinstance(p, dict)}
        bad_ends = sorted({e for c in wiring.get("connections") or [] if isinstance(c, dict)
                           for e in (str(c.get("from", "")).split(".")[0], str(c.get("to", "")).split(".")[0]) if e not in part_ids})
        cs.append(check("wiring-endpoints", "every wiring connection is <part>.<pin> on a listed part", not bad_ends,
                        "all endpoints known" if not bad_ends else f"unknown parts: {words(bad_ends)}", qb="QB-17"))
    else:
        cs.append(check("wiring-bom-join", "every wiring part is a purchased item", "not_run", "build.json has no wiring yet", qb="QB-18"))

    # QB-19 firmware pins.h == electrical pin map: per instance pin (GS_<REF>_<PIN>, via the pin's net) and per signal name
    pins_h = out / "firmware" / "include" / "pins.h"
    gpio = (electrical or {}).get("gpio") or {}
    if pins_h.exists() and gpio:
        defs = parse_defines(pins_h.read_text(errors="ignore"))
        expect: dict[str, tuple[int, str]] = {}
        for c in (electrical or {}).get("connections") or []:
            if not isinstance(c, dict):
                continue
            net = str(c.get("net") or "")
            for end in (c.get("from"), c.get("to")):
                ref, _, pin = str(end or "").partition(".")
                if ref and pin and "." not in pin and isinstance(gpio.get(net), int):
                    expect[f"GS_{re.sub(r'[^A-Z0-9]', '_', ref.upper())}_{re.sub(r'[^A-Z0-9]', '_', pin.upper())}"] = (gpio[net], net)
        for sig, num in gpio.items():
            if isinstance(num, int):
                for k in defs:
                    if k == sig.upper() or k.endswith("_" + sig.upper()):
                        expect.setdefault(k, (num, sig))
        found = {k: v for k, v in expect.items() if k in defs}
        mism = [f"{k}: electrical {v[0]} ({v[1]}) vs pins.h {defs[k]}" for k, v in found.items() if defs[k] != v[0]]
        st = "failed" if mism else ("passed" if found else "not_run")
        cs.append(check("pins-match", "firmware pin constants equal the electrical pin map (pins.h is generated from it)", st,
                        f"{len(found)} constants compared" + (f"; mismatches: {words(mism)}" if mism else "")
                        + ("" if found else "; no pins.h constant matched an electrical pin or signal name"),
                        qb="QB-19", artifact="firmware/include/pins.h"))
        cs.append(pins_hash_check(out, pins_h))
    elif gpio:
        cs.append(check("pins-match", "firmware pin constants equal the electrical pin map", "not_run", "no firmware/include/pins.h", qb="QB-19"))

    # in-browser flashing: every file the web-flasher manifest names ships in the delivery
    man, parts = flasher_parts(out)
    if man is not None:
        missing = [pth for pth, _o, ok in parts if not ok]
        cs.append(check("web-flasher", "the web-flasher manifest names firmware files that ship in this delivery", bool(parts) and not missing,
                        f"{len(parts)} part(s)" + (f"; missing or empty: {words(missing)}" if missing else "") if parts else "manifest lists no parts",
                        artifact="firmware/web-flasher-manifest.json", stage="firmware"))

    # step parts exist in the CAD (the join the benchmark's verify_delivery.py asserts)
    comp_names = set(comps)
    if comps and build.get("steps"):
        coupons = {p.stem for p in (out / "print").glob("TEST_*.stl")}  # coupons are STL-only by convention
        unknown = sorted({p for s_ in build["steps"] if isinstance(s_, dict) for p in (s_.get("parts") or []) + (s_.get("highlight") or [])
                          if isinstance(p, str) and p not in comp_names and p not in coupons})
        cs.append(check("step-parts-in-cad", "every part a step names exists in the CAD (or as a TEST_ coupon STL)", not unknown,
                        "all found" if not unknown else f"not in cad/components.json: {words(unknown)}", qb="QB-29", artifact="cad/components.json"))

    # QB-20 / MAJ-5 / MIN-2 power: supply limit, default USB, rail voltage, firmware caps; MAJ-29 night light
    cs += power_checks(out, spec, build, electrical, lib)
    nl = night_light_check(spec, comps, lib)
    if nl:
        cs.append(nl)
    # BLK-3 / MAJ-4 harnesses, MAJ-25 receipts, BLK-4 story audio, BLK-5 storage card, MIN-22 independent pin map
    cs += harness_checks(build, electrical, lib)
    cs.append(price_evidence_check(out, build))
    sa = story_audio_check(out, spec, records or {})
    if sa:
        cs.append(sa)
    sc = storage_part_check(out, build, lib)
    if sc:
        cs.append(sc)
    cs += pin_map_checks(out, lib, electrical)
    inp = ((spec.get("electronics") or {}).get("power") or {}).get("input")
    # MIN-3 (integration): power entry = 5 V USB-C only AND a defined load path for the external cord (cad-summary)
    cad_sum = read_json(out / "cad" / "cad-summary.json", {}) or {}
    load_path = (((cad_sum.get("info") or {}).get("usb_c") or {}).get("load_path")) if isinstance(cad_sum, dict) else None
    lp_txt = (load_path if isinstance(load_path, str) else
              (load_path or {}).get("feature") or (load_path or {}).get("what") or (load_path or {}).get("summary") or
              (json.dumps(load_path)[:160] if load_path else None))
    pe_ok = inp == "usb_c_5v" and bool(load_path)
    cs.append(check("power-entry", "power entry: 5 V USB-C input only (no mains inside) and a defined load path for the cord",
                    pe_ok if cad_sum else (False if inp != "usb_c_5v" else "not_run"),
                    f"power input: {inp}; " + (f"cord load path: {lp_txt}" if load_path else
                                                ("no cord load path in cad/cad-summary.json info.usb_c" if cad_sum else "CAD did not run")),
                    qb="QB-21", artifact="cad/cad-summary.json"))

    # QB-29 / QB-30 step shape, QB-34 manual images
    steps = build.get("steps") or []
    if steps:
        no_parts = [s.get("id") for s in steps if isinstance(s, dict) and not s.get("parts")]
        bad_head = [s.get("id") for s in steps if isinstance(s, dict)
                    and not (str(s.get("instruction", "")).lstrip().startswith("**Use:**") and "**Tools:**" in str(s.get("instruction", "")))]
        ids = [s.get("id") for s in steps if isinstance(s, dict)]
        cs.append(check("step-ids", "every step has a unique stable id, title, instruction and expected result",
                        len(set(ids)) == len(ids) and all(isinstance(s, dict) and s.get("id") and s.get("title") and s.get("instruction") and s.get("expected") for s in steps),
                        f"{len(steps)} steps", qb="QB-29", artifact="build.json"))
        cs.append(check("step-parts", "every step lists the parts it handles", "passed" if not no_parts else "warning",
                        "all steps list parts" if not no_parts else f"steps without parts: {words(no_parts)}", qb="QB-29"))
        cs.append(check("step-use-tools", "every step opens with **Use:** and **Tools:**", not bad_head,
                        "all steps" if not bad_head else f"missing in: {words(bad_head)}", qb="QB-30"))
    refs = set()
    for s in steps:
        for v in (s.get("visuals") or []) if isinstance(s, dict) else []:
            if isinstance(v, dict) and v.get("path"):
                refs.add(str(v["path"]))
    guide = out / "BUILD_GUIDE.md"
    if guide.exists():
        refs.update(m.group(1) for m in re.finditer(r"!\[[^\]]*\]\(([^)\s]+)", guide.read_text(errors="ignore")))
    sw = out / "SOFTWARE_SETUP.md"
    if sw.exists():
        refs.update(m.group(1) for m in re.finditer(r"!\[[^\]]*\]\(([^)\s]+)", sw.read_text(errors="ignore")))
    imgs = sorted(str(p.relative_to(out)) for p in (out / "manual").glob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm")) \
        if (out / "manual").exists() else []
    if imgs or refs:
        missing = sorted(r for r in refs if not (out / r).exists())
        orphans = sorted(i for i in imgs if i not in refs)
        cs.append(check("manual-images", "every referenced image exists and no unreferenced image ships",
                        "failed" if missing else ("warning" if orphans else "passed"),
                        f"{len(imgs)} images, {len(refs)} referenced" + (f"; missing: {words(missing)}" if missing else "")
                        + (f"; orphans: {words(orphans)}" if orphans else ""), qb="QB-34", artifact="BUILD_GUIDE.md"))
    return cs


# ============================================================================ REVIEW-1 checks


def _lib_get(lib: Any, mid: Optional[str]) -> dict:
    if not mid or lib is None:
        return {}
    try:
        m = lib.get(mid)
        return m if isinstance(m, dict) else dict(m)
    except Exception:  # noqa: BLE001
        return {}


def _harnesses(electrical: dict | None) -> list[dict]:
    out = []
    for i in (electrical or {}).get("instances") or []:
        if isinstance(i, dict) and isinstance(i.get("harness"), dict):
            out.append({"ref": i.get("ref"), "module": i.get("module"), "slot": i.get("slot"), "h": i["harness"]})
    return out


def needed_family(mod: dict) -> Optional[str]:
    """Module-end family a module needs: foundation's module_end_family when it answers, else the local map."""
    from gstudio.order.bom import cable_family_for

    if (mod.get("prewire") or {}).get("lead"):
        return "factory_prewired"
    try:
        from gstudio import library as _l

        fn = getattr(_l, "module_end_family", None)
        if callable(fn):
            got = fn(mod)
            if got:
                return str(got)
    except Exception:  # noqa: BLE001
        pass
    return cable_family_for(mod.get("module_connector") or {})


def harness_checks(build: dict, electrical: dict | None, lib: Any) -> list[dict]:
    """BLK-3 harness-module-end and wiring-via-join; MAJ-4 harness-straight-through."""
    from gstudio.order.bom import cable_module_end, is_crossed, module_order, port_order

    cs: list[dict] = []
    purchased = build.get("purchased") or []
    work = build.get("factory_work") or []
    cable_for = {ref: r for r in purchased if r.get("kind") == "cable" for ref in r.get("refs") or []}
    prewired = {ref for w in work if w.get("kind") in ("prewire", None) and (w.get("prewire") or {}).get("lead") for ref in w.get("refs") or []}
    continuity = {ref for w in work if w.get("kind") == "continuity_test" for ref in w.get("refs") or []}
    hs = _harnesses(electrical)
    if not hs:
        return [check("harness-module-end", "every harness ends in the module's own connector family", "not_run",
                      "electrical.json lists no harness", qb="QB-17", artifact="electrical.json")]
    bad, ok_rows, bad_x, ok_x, warn_x = [], [], [], [], []
    for x in hs:
        ref, h = x["ref"], x["h"]
        mod = _lib_get(lib, x["module"])
        need = needed_family(mod)
        row = cable_for.get(ref)
        lib_cable = _lib_get(lib, row.get("module_id")) if row else {}
        me = (row or {}).get("module_end") or cable_module_end(lib_cable)
        have = me.get("family")
        mc_type = (mod.get("module_connector") or {}).get("type")
        if need == "factory_prewired":
            if ref in prewired:
                ok_rows.append(f"{ref}: factory-soldered lead")
            else:
                bad.append(f"{ref}: needs a factory pre-wired lead, but factory_work has no prewire entry for it")
        elif row is None:
            bad.append(f"{ref}: no purchased cable for this harness")
        elif need is None:
            if (row or {}).get("made_to_drawing"):
                ok_rows.append(f"{ref}: made to drawing ({mc_type})")
            else:
                bad.append(f"{ref}: no harness family mates {mc_type}; it needs factory pre-wiring or a made-to-drawing lead")
        elif have != need:
            bad.append(f"{ref}: {row['name']} ends in {have or 'an unstated module end'}, the module's {mc_type} needs {need}")
        else:
            ok_rows.append(f"{ref}: {have}")
        # MAJ-4: a crossed harness may not be a straight-through SKU
        crossed = is_crossed(h, mod)
        if crossed and need != "factory_prewired" and row is not None:
            mo, po = module_order(h, mod), port_order(h)
            pos = [p for p in (me.get("positions") or [])]
            multi = len(me.get("housings") or []) > 1  # split housings are built to the module's own layout, never straight
            straight = me.get("straight_through") is True or (not multi and bool(pos) and pos == po and [p for p in mo if p in pos] != pos)
            matches_module = multi or (bool(pos) and [p for p in mo if p in pos] == pos)
            if straight or not (row.get("made_to_drawing") or matches_module):
                bad_x.append(f"{ref}: module order {', '.join(mo)} differs from port order {', '.join(po)}, but {row['name']} is "
                             + ("a straight-through lead" if straight else "not made to drawing in the module's order"))
            elif ref not in continuity:
                bad_x.append(f"{ref}: crossed harness has no factory continuity test")
            else:
                ok_x.append(f"{ref}: made to drawing in the module order {', '.join(mo)}, continuity-tested")
            if not h.get("module_end_order"):
                warn_x.append(f"{ref}: electrical.json gives no module_end_order")
    cs.append(check("harness-module-end", "every harness ends in the module's own connector family", not bad,
                    f"{len(ok_rows)} ok ({'; '.join(ok_rows)})" + (f"; wrong: {'; '.join(bad)}" if bad else ""),
                    qb="QB-17", artifact="build.json"))
    cs.append(check("harness-straight-through", "no crossed harness is given a straight-through lead",
                    "failed" if bad_x else ("warning" if warn_x else "passed"),
                    ("; ".join(ok_x) if ok_x else "no crossed harness") + (f"; wrong: {'; '.join(bad_x)}" if bad_x else "")
                    + (f"; {'; '.join(warn_x)}" if warn_x else ""), qb="QB-17", artifact="electrical.json"))
    w = build.get("wiring") or {}
    names = {r.get("name"): r for r in purchased}
    if isinstance(w, dict) and w.get("connections"):
        miss = sorted({f"{c.get('from')} via {c.get('via')!s}" for c in w["connections"] if isinstance(c, dict) and c.get("via") not in names})
        sku = sorted({f"{c.get('from')}: {c.get('via_sku')} vs {names[c['via']].get('sku')}" for c in w["connections"]
                      if isinstance(c, dict) and c.get("via") in names and c.get("via_sku") and names[c["via"]].get("sku")
                      and c["via_sku"] != names[c["via"]].get("sku")})
        cs.append(check("wiring-via-join", "every wiring connection's via is a purchased item (name and SKU)", not miss and not sku,
                        f"{len(w['connections'])} connections" + (f"; via not purchased: {'; '.join(miss[:12])}" + (" ..." if len(miss) > 12 else "") if miss else "")
                        + (f"; SKU differs: {'; '.join(sku)}" if sku else ""), qb="QB-18", artifact="build.json"))
    return cs


def route_cable_length(r: dict) -> Optional[float]:
    """The cable length a CAD route is labelled with: `cable_length_mm`, else the last 2-4 digit "NNN mm" in its label
    (a label like "3-pin to 2 x 2.8 mm spade 300 mm" names the spade size first)."""
    v = r.get("cable_length_mm")
    if isinstance(v, (int, float)) and v > 0:
        return float(v)
    ms = re.findall(r"(?<![\d.])(\d{2,4})\s*mm\b", str(r.get("cable") or ""))
    return float(ms[-1]) if ms else None


def stocked_lengths(lib: Any) -> list[int]:
    """Harness lengths the library stocks (design harness rows' length_mm, else the CABLE_JSTPH_*_<len> ids)."""
    got: set[int] = set()
    try:
        for m in (lib.harness_rows() if hasattr(lib, "harness_rows") else []):
            if m.get("length_mm"):
                got.add(int(m["length_mm"]))
        if not got:
            for mid in lib.ids():
                mm = re.match(r"CABLE_JSTPH_\d+P_(\d{3})(?:MM)?$", str(mid))
                if mm:
                    got.add(int(mm.group(1)))
    except Exception:  # noqa: BLE001
        pass
    return sorted(got) or [100, 150, 200, 300]


def harness_length_checks(out: Path, build: dict, electrical: dict | None, lib: Any) -> list[dict]:
    """QB-15 route <= harness; MIN-6 one length per harness across electrical, purchased, factory_work and wiring, and a
    slack of 40 mm up to the next stocked length."""
    routes = read_json(out / "cad" / "cable-routes.json", None)
    if isinstance(routes, dict):
        routes = routes.get("routes") or list(routes.values())
    hs = {x["ref"]: x["h"] for x in _harnesses(electrical)}
    cs: list[dict] = []
    if not isinstance(routes, list) or not routes:
        return cs
    stocked = stocked_lengths(lib)
    purchased = build.get("purchased") or []
    work = build.get("factory_work") or []
    conns = (build.get("wiring") or {}).get("connections") or [] if isinstance(build.get("wiring"), dict) else []
    bad, okc, unk, mism, slack_warn, slack_ok = [], 0, [], [], [], []
    for r in routes:
        if not isinstance(r, dict):
            continue
        ref = str(r.get("to") or "").split(".")[0]
        rid = f"{r.get('id', '?')} ({ref})"
        route = r.get("route_length_mm") or r.get("path_length_mm") or polyline_length(r.get("points")) or r.get("length_mm")
        h = hs.get(ref) or {}
        have = h.get("length_mm")
        route_len = route_cable_length(r)
        if have is None:
            have = route_len
        if have is None or not route:
            unk.append(rid)
            continue
        have, route = float(have), float(route)
        if route > have + 0.5:
            bad.append(f"{rid}: route {route:.0f} mm > harness {have:.0f} mm")
        else:
            okc += 1
        lens = {"electrical.json": have}
        row = next((p for p in purchased if p.get("kind") == "cable" and ref in (p.get("refs") or [])), None)
        if row and row.get("length_mm") is not None and not isinstance(row.get("length_mm"), list):
            lens["purchased"] = float(row["length_mm"])
        fw = next((w for w in work if ref in (w.get("refs") or []) and (w.get("prewire") or {}).get("lead")), None)
        if fw:
            m = re.search(r"(\d+)\s*mm", str(fw["prewire"]["lead"]))
            if m:
                lens["factory_work"] = float(m.group(1))
        if route_len is not None:
            lens["cable-routes"] = route_len
        if len(set(lens.values())) > 1:
            mism.append(f"{ref}: " + ", ".join(f"{k} {v:.0f} mm" for k, v in lens.items()))
        want = next((L for L in stocked if L >= route + 40), None)
        if have - route < 40:
            slack_warn.append(f"{ref}: {have:.0f} mm leaves {have - route:.0f} mm over the {route:.0f} mm route (under 40 mm)")
        elif want is not None and have > want:
            slack_warn.append(f"{ref}: {have:.0f} mm for a {route:.0f} mm route; {want} mm is the shortest stocked length with 40 mm slack")
        else:
            slack_ok.append(f"{ref} {have:.0f}/{route:.0f} mm")
    st = "failed" if bad else ("passed" if okc and not unk else ("warning" if okc else "not_run"))
    cs.append(check("cable-length", "every cable route is no longer than its harness (electrical.json length)", st,
                    f"{okc} ok" + (f"; too long: {'; '.join(bad)}" if bad else "") + (f"; length unknown: {words(unk)}" if unk else ""),
                    qb="QB-15", artifact="cad/cable-routes.json"))
    cs.append(check("harness-length-one-source", "each harness has one length in electrical.json, purchased, factory_work and the CAD route",
                    not mism, "all agree" if not mism else "; ".join(mism), qb="QB-15", artifact="electrical.json"))
    cs.append(check("harness-slack", "each harness is the shortest stocked length with at least 40 mm over its route",
                    "warning" if slack_warn else ("passed" if slack_ok else "not_run"),
                    ("; ".join(slack_warn) if slack_warn else "") + (("; " if slack_warn and slack_ok else "") + "ok: " + ", ".join(slack_ok) if slack_ok else ""),
                    qb="QB-15", artifact="cad/cable-routes.json"))
    return cs


def _defines_any(text: str) -> dict[str, tuple[float, str]]:
    """#define NAME 60 // comment -> {NAME: (60.0, comment)} (numbers only, int or float)."""
    out = {}
    for m in re.finditer(r"#define\s+([A-Za-z_]\w*)\s+\(?(-?\d+(?:\.\d+)?)[fF]?\)?[^\n]*?(//[^\n]*)?$", text, re.M):
        out[m.group(1).upper()] = (float(m.group(2)), (m.group(3) or "").lstrip("/ ").strip())
    for m in re.finditer(r"constexpr\s+[\w:<> ]+?\b([A-Za-z_]\w*)\s*=\s*(-?\d+(?:\.\d+)?)[fF]?\s*;[^\n]*?(//[^\n]*)?$", text, re.M):
        out.setdefault(m.group(1).upper(), (float(m.group(2)), (m.group(3) or "").lstrip("/ ").strip()))
    return out


def firmware_caps(out: Path, caps: dict) -> tuple[list[str], list[str]]:
    """(found, missing) firmware constants for electrical.json power.caps (volume_max, led_max_percent)."""
    txt = ""
    for rel in ("firmware/include/config.h", "firmware/include/power.h", "firmware/include/caps.h"):
        p = out / rel
        if p.exists():
            txt += p.read_text(errors="ignore") + "\n"
    defs = _defines_any(txt)
    found, missing = [], []
    for k, v in (caps or {}).items():
        if not isinstance(v, (int, float)):
            continue
        key = str(k).upper()
        if "VOLUME" in key:
            hits = [(n, d) for n, d in defs.items() if "VOLUME_MAX" in n]
            ok = [n for n, (val, _c) in hits if abs(val - float(v)) < 0.51]
        elif "LED" in key or "BRIGHT" in key:
            hits = [(n, d) for n, d in defs.items() if any(w in n for w in ("LED_CAP", "LED_MAX", "BRIGHTNESS_MAX", "NIGHT_LEVEL_MAX"))]
            ok = []
            for n, (val, cmt) in hits:
                pct = val if ("PERCENT" in n or val <= 100 and "LEVEL" in n) else val / 255.0 * 100.0
                if abs(pct - float(v)) <= 1.0 or re.search(rf"\b{int(round(float(v)))}\s*%", cmt):
                    ok.append(n)
        else:
            continue
        (found if ok else missing).append(f"{k} {v:g}" + (f" = {ok[0]}" if ok else (f" (constants seen: {', '.join(n for n, _ in hits)})" if hits else "")))
    return found, missing


def power_checks(out: Path, spec: dict, build: dict, electrical: dict | None, lib: Any) -> list[dict]:
    from gstudio.order.power import drop_budget, supply_basis

    pw = (electrical or {}).get("power") or {}
    if not pw:
        return []
    cs: list[dict] = []
    bp = getattr(lib, "backplane", None) if lib is not None else None
    limits = ((bp or {}).get("power") or {}).get("limits") or {}
    adapter_row = next((r for r in build.get("purchased") or [] if str(r.get("role") or "") == "adapter" and r.get("in_box")), None)
    adapter = _lib_get(lib, adapter_row.get("module_id")) if adapter_row else None
    sb = pw.get("supply") if isinstance(pw.get("supply"), dict) else supply_basis(pw, limits, adapter)
    where = "electrical.json power.supply" if isinstance(pw.get("supply"), dict) else "computed by the checks stage (electrical.json has no power.supply)"
    cap, unc, lim = sb.get("capped_peak_ma"), sb.get("uncapped_peak_ma"), sb.get("limit_ma")
    cs.append(check("power-budget", f"worst-case +5V current within {sb.get('basis')}",
                    "not_run" if sb.get("ok") is None else sb["ok"],
                    f"{cap} mA peak with the firmware caps vs {lim} mA; supply: {sb.get('supply')}; {where} (calculated from module data, not measured)",
                    qb="QB-20", artifact="electrical.json", data={k: sb.get(k) for k in ("limit_ma", "basis", "capped_peak_ma", "uncapped_peak_ma", "supply")}))
    found, missing = firmware_caps(out, pw.get("caps") or {})
    need_caps = bool(pw.get("firmware_caps_required")) or (unc is not None and lim is not None and unc > lim)
    if pw.get("caps"):
        cs.append(check("power-caps-in-firmware", "the firmware config holds the current caps the power budget relies on",
                        "passed" if not missing else ("failed" if need_caps else "warning"),
                        (f"found: {'; '.join(found)}" if found else "none found") + (f"; missing: {'; '.join(missing)}" if missing else ""),
                        qb="QB-20", artifact="firmware/include/config.h"))
    if unc is not None and lim is not None and unc > lim:
        cs.append(check("power-budget-uncapped", f"worst case without the firmware caps vs {lim} mA",
                        "warning" if not missing else "failed",
                        f"{unc} mA uncapped vs {lim} mA: the budget relies on the firmware caps"
                        + (" (present in firmware/include/config.h)" if not missing else " (NOT found in the firmware config)")
                        + "; calculated, not measured", qb="QB-20", artifact="electrical.json"))
    usb = [x for x in sb.get("sources") or [] if "computer port" in str(x.get("source"))]
    low = [f"{x['source']} {x['ma']} mA" for x in usb if x.get("ok_capped") is False]
    if usb:
        cs.append(check("power-default-usb", "behaviour on a computer USB port (default power) instead of the adapter",
                        "warning" if low else "passed",
                        (f"the {cap} mA capped peak is over: {'; '.join(low)}; use the 5 V 3 A adapter" if low else
                         f"the {cap} mA capped peak fits a default USB port"), qb="QB-20", artifact="electrical.json"))
    db = pw.get("drop_budget") if isinstance(pw.get("drop_budget"), dict) else (
        drop_budget(pw, bp or {}, lambda mid: _lib_get(lib, mid) or None, (electrical or {}).get("instances") or [], adapter) if lib is not None else None)
    if db:
        cs.append(check("rail-voltage", "+5V rail stays above the highest module minimum at the capped peak",
                        "not_run" if db.get("ok") is None else db["ok"],
                        (db.get("equation") or db.get("detail") or "")
                        + ("; electrical.json power.drop_budget" if isinstance(pw.get("drop_budget"), dict) else
                           "; computed by the checks stage (electrical.json has no power.drop_budget)")
                        + "; terms marked ASSUMPTION are not datasheet values", qb="QB-20", artifact="electrical.json",
                        data={k: db.get(k) for k in ("source_min_v", "peak_ma", "r_total_ohm", "drop_v", "rail_min_v", "required_min_v",
                                                     "required_by", "resistances_ohm", "margin_v")}))
    return cs


def night_light_check(spec: dict, comps: dict, lib: Any) -> Optional[dict]:
    """MAJ-29: a NIGHT_USE design needs a diffused light at least 40 mm across."""
    flags = set((spec.get("safety") or {}).get("flags") or [])
    if "NIGHT_USE" not in flags:
        return None
    if not comps:
        return check("night-light-diffused", "a NIGHT_USE design has a diffused light at least 40 mm across", "not_run",
                     "no cad/components.json (the CAD stage did not deliver)", artifact="cad/components.json")
    lights = []
    for f in spec.get("features") or []:
        m = _lib_get(lib, f.get("module"))
        if str(m.get("role") or "") != "led":
            continue
        dims = m.get("dims_mm") or {}
        across = min(float(dims.get("x") or 0), float(dims.get("y") or 0))
        face = f.get("face") or "front"
        diffs = []
        for n, c in (comps or {}).items():
            if not isinstance(c, dict) or c.get("feature_id") != f.get("id") or not n.startswith("PRINT_"):
                continue
            if not re.search(r"(?i)diffus|window|halo|lens", n):
                continue
            b = c.get("bbox") or []
            if len(b) != 6:
                continue
            ex = {"x": b[1] - b[0], "y": b[3] - b[2], "z": b[5] - b[4]}
            plane = ("y", "z") if face in ("left", "right") else (("x", "y") if face == "top" else ("x", "z"))
            diffs.append((n, min(ex[plane[0]], ex[plane[1]])))
        best = max(diffs, key=lambda d: d[1]) if diffs else None
        lights.append({"feature": f.get("id"), "module": f.get("module"), "module_across": across, "diffuser": best})
    good = [x for x in lights if x["module_across"] >= 40 and x["diffuser"] and x["diffuser"][1] >= 40]
    det = "; ".join(f"{x['feature']}: {x['module']} {x['module_across']:.0f} mm across, "
                    + (f"{x['diffuser'][0]} {x['diffuser'][1]:.0f} mm across" if x["diffuser"] else "no PRINT_ diffuser")
                    for x in lights) or "no LED feature"
    return check("night-light-diffused", "a NIGHT_USE design has a diffused light at least 40 mm across", bool(good), det,
                 artifact="cad/components.json")


def price_evidence_check(out: Path, build: dict) -> dict:
    """MAJ-25: every "seen" row's receipt verifies against the row and its saved listing snapshot."""
    from gstudio.order.pricecheck import verify_row

    rows = build.get("purchased") or []
    seen = [r for r in rows if r.get("evidence_tier") == "seen"]
    bad = []
    for r in seen:
        ok, why = verify_row(r, out)
        if not ok:
            bad.append(f"{r.get('name')}: {why}")
    unlabelled = [r.get("name") for r in rows if r.get("evidence_tier") != "seen" and not r.get("estimate")]
    est = sum(1 for r in rows if r.get("evidence_tier") == "estimate")
    nc = sum(1 for r in rows if r.get("evidence_tier") == "not_confirmed")
    return check("price-evidence", "every seen price has a receipt that verifies; every other price is labelled an estimate",
                 not bad and not unlabelled,
                 f"{len(seen) - len(bad)}/{len(seen)} seen receipts verify, {est} estimate, {nc} not confirmed"
                 + (f"; does not verify: {'; '.join(bad)}" if bad else "") + (f"; unlabelled: {words(unlabelled)}" if unlabelled else ""),
                 qb="QB-23", artifact="references/listings")


def story_audio_check(out: Path, spec: dict, records: dict) -> Optional[dict]:
    """BLK-4: the story card has audio for every story segment and the default name."""
    if ((spec.get("behaviour") or {}).get("template")) != "story_player":
        return None
    sd = out / "firmware" / "sdcard"
    d = read_json(sd / "stories.json", None)
    fs = ((records.get("firmware") or {}).get("summary") or {}) if isinstance(records.get("firmware"), dict) else {}
    sp = fs.get("story_pack") or {}
    tm = (fs.get("timings") or {}).get("story_pack_s")
    model = sp.get("tts_model") or (d.get("tts_model") if isinstance(d, dict) else None)
    tts = (f"; story pack {'with ' + str(model) + ' ' if model else ''}written in {tm} s (TTS cache hits count as 0 s)"
           if tm is not None else "")
    if not isinstance(d, dict):
        return check("story-audio-present", "the story card has audio for every story", "failed", "no firmware/sdcard/stories.json",
                     artifact="firmware/sdcard/stories.json")
    files = [f for st in d.get("stories") or [] for f in st.get("files") or []]
    if isinstance(d.get("default_name"), dict) and d["default_name"].get("file"):
        files.append(d["default_name"]["file"])
    files += [n.get("file") for n in d.get("names") or [] if isinstance(n, dict) and n.get("file")]
    missing = [f for f in files if not (sd / f).is_file() or (sd / f).stat().st_size == 0]
    if not d.get("audio"):
        warn = [w for w in fs.get("warnings") or [] if "TTS" in str(w) or "audio" in str(w).lower()]
        return check("story-audio-present", "the story card has audio for every story", "failed",
                     f"stories have no audio yet (text only): stories.json audio false, {len(missing)}/{len(files)} files missing"
                     + (f"; {plain(warn[0])}" if warn else "") + tts, artifact="firmware/sdcard/stories.json")
    return check("story-audio-present", "the story card has audio for every story", not missing,
                 f"{len(files) - len(missing)}/{len(files)} audio files present" + (f"; missing: {words(missing[:10])}" if missing else "") + tts,
                 artifact="firmware/sdcard/stories.json")


_CARD = re.compile(r"(?i)micro\s?-?sd(?:hc|xc)?\b[^.\n]{0,30}\bcard\b")


def storage_part_check(out: Path, build: dict, lib: Any) -> Optional[dict]:
    """BLK-5: every storage medium the firmware or SOFTWARE_SETUP needs has a purchased row; every library requires[] link
    of a purchased module is bought too."""
    needs = []
    if (out / "firmware" / "sdcard").exists():
        needs.append("firmware/sdcard/ (the card content)")
    sw = out / "SOFTWARE_SETUP.md"
    if sw.exists() and _CARD.search(sw.read_text(errors="ignore")):
        needs.append("SOFTWARE_SETUP.md names a microSD card")
    rows = build.get("purchased") or []

    def is_card(r: dict) -> bool:
        mid = str(r.get("module_id") or "")
        role = str(r.get("role") or _lib_get(lib, mid).get("role") or "")
        return mid.startswith("STORAGE_") or role in ("storage_media", "storage_card", "sd_card", "memory_card", "media") or \
            (bool(_CARD.search(str(r.get("name") or ""))) and "module" not in str(r.get("name") or "").lower())
    cards = [r.get("name") for r in rows if is_card(r)]
    have = {r.get("module_id") for r in rows}
    unmet = []
    for r in rows:
        for req in (_lib_get(lib, r.get("module_id")).get("requires") or []) if r.get("module_id") else []:
            if isinstance(req, dict) and req.get("module") and req["module"] not in have:
                unmet.append(f"{r.get('name')} requires {req['module']}")
    if not needs and not unmet:
        return None
    ok = (bool(cards) or not needs) and not unmet
    return check("firmware-storage-has-part", "every storage medium the firmware needs is in the parts list", ok,
                 f"needed by: {'; '.join(needs) or 'nothing'}; purchased: {words(cards) if cards else 'no storage card row'}"
                 + (f"; unmet requires: {'; '.join(unmet)}" if unmet else ""), qb="QB-24", artifact="build.json")


def _kicad_pad_nets(text: str) -> dict[str, dict[str, str]]:
    """{reference: {pad: net}} from a .kicad_pcb (KiCad 6-9 s-expressions)."""
    out: dict[str, dict[str, str]] = {}
    for blk in re.split(r"\n\t\(footprint ", text)[1:]:
        m = re.search(r'\(property "Reference" "([^"]+)"', blk)
        if not m:
            continue
        pads = {}
        for pm in re.finditer(r'\(pad "([^"]*)"(.*?)(?=\n\t\t\(pad |\Z)', blk, re.S):
            nm = re.search(r'\(net \d+ "([^"]*)"\)', pm.group(2))
            pads[pm.group(1)] = nm.group(1) if nm else ""
        out[m.group(1)] = pads
    return out


def _references_root(lib: Any) -> Path:
    try:
        if lib is not None and hasattr(lib, "_backplane_path"):
            return Path(lib._backplane_path).parent / "references"
    except Exception:  # noqa: BLE001
        pass
    return Path(__file__).resolve().parents[2] / "library" / "references"


def _find_header_table(lib: Any) -> Optional[Path]:
    """The saved DevKitC-1 header table: a JSON table, else Espressif's saved user guide page (J1/J3 tables)."""
    root = _references_root(lib)
    if not root.exists():
        return None
    cands = sorted(root.rglob("*"))
    for p in cands:
        n = p.name.lower()
        if p.suffix == ".json" and "devkitc" in n and ("header" in n or "pinout" in n):
            return p
    for p in cands:
        n = p.name.lower()
        if p.suffix in (".html", ".htm") and "devkitc-1" in n and "user-guide" in n:
            return p
    return None


def parse_devkit_tables(path: Path) -> dict[str, tuple[str, str]]:
    """{"J1-1": (name, function), ...}. JSON: [{pin, label, function}]; HTML: the guide's two No./Name/Type/Function
    tables in page order (J1 then J3)."""
    if path.suffix == ".json":
        d = read_json(path, {}) or {}
        rows = d.get("pins") if isinstance(d, dict) else d
        return {str(r.get("pin")): (str(r.get("label") or r.get("name") or ""), str(r.get("function") or ""))
                for r in rows or [] if isinstance(r, dict) and r.get("pin")}
    import html as _html

    text = path.read_text(encoding="utf-8", errors="replace")
    out: dict[str, tuple[str, str]] = {}
    headers = ("J1", "J3")
    k = 0
    for tm in re.finditer(r"<table.*?</table>", text, re.S | re.I):
        t = tm.group(0)
        if not re.search(r">\s*No\.\s*<", t) or not re.search(r">\s*Function\s*<", t):
            continue
        rows = []
        for rm in re.finditer(r"<tr.*?</tr>", t, re.S | re.I):
            cells = [_html.unescape(re.sub(r"<[^>]+>", " ", c)).strip() for c in re.findall(r"<td.*?>(.*?)</td>", rm.group(0), re.S | re.I)]
            cells = [re.sub(r"\s+", " ", c) for c in cells]
            if len(cells) >= 4 and cells[0].isdigit():
                rows.append(cells)
        if len(rows) >= 20 and k < len(headers):
            for c in rows:
                out[f"{headers[k]}-{c[0]}"] = (c[1], c[3])
            k += 1
    return out


def pin_map_checks(out: Path, lib: Any, electrical: dict | None) -> list[dict]:
    """MIN-22: an independent pin check that does not read electrical.json: backplane U1 labels and GPIO numbers vs the
    saved DevKitC-1 v1.1 header tables, numeric labels vs GPIO numbers, and each KiCad pad net vs backplane-v1."""
    if lib is None:
        return []
    try:
        u1 = lib.slot("U1")
    except Exception:  # noqa: BLE001
        return []
    pins = [p for p in u1.get("pins") or [] if isinstance(p, dict)]
    cs: list[dict] = []
    tab_p = _find_header_table(lib)
    tab = parse_devkit_tables(tab_p) if tab_p is not None else {}
    if not tab:
        cs.append(check("devkit-header-table", "backplane U1 pin labels and GPIOs equal the saved DevKitC-1 v1.1 header tables", "not_run",
                        "no saved DevKitC-1 header table under library/references (MIN-23)" if tab_p is None else
                        f"could not read the J1/J3 tables in {tab_p.name}", qb="QB-19", artifact="library/backplane-v1.json"))
    else:
        diff, n = [], 0
        for p in pins:
            row = tab.get(str(p.get("pin")))
            if not row:
                continue
            n += 1
            name, func = row
            if str(p.get("devkit_label")) != name:
                diff.append(f"{p['pin']}: label {p.get('devkit_label')} vs table {name}")
            g = p.get("gpio")
            if isinstance(g, int) and not re.search(rf"\bGPIO{g}\b", func):
                diff.append(f"{p['pin']}: GPIO{g} vs table function {func}")
        cs.append(check("devkit-header-table", "backplane U1 pin labels and GPIOs equal the saved DevKitC-1 v1.1 header tables",
                        bool(n) and not diff, f"{n} pins compared with {tab_p.relative_to(_references_root(lib).parent).as_posix()}"
                        + (f"; differ: {'; '.join(diff[:10])}" if diff else ""), qb="QB-19", artifact="library/backplane-v1.json"))
    bad_lbl = []
    numeric = [p for p in pins if isinstance(p.get("gpio"), int) and str(p.get("devkit_label") or "").isdigit()]
    for p in numeric:
        if int(p["devkit_label"]) != p["gpio"]:
            bad_lbl.append(f"{p.get('pin')}: label {p['devkit_label']} vs GPIO{p['gpio']}")
    cs.append(check("backplane-gpio-labels", "every numeric DevKitC label on a backplane U1 pin is its GPIO number", not bad_lbl,
                    f"{len(numeric)} numeric labels" + (f"; differ: {'; '.join(bad_lbl)}" if bad_lbl else ""),
                    qb="QB-19", artifact="library/backplane-v1.json"))
    pcb = out / "pcb" / "backplane.kicad_pcb"
    if pcb.exists():
        pads = _kicad_pad_nets(pcb.read_text(errors="ignore"))
        diff, n, idle = [], 0, 0
        for p in pins:
            ref, pad = p.get("socket"), str(p.get("socket_pin"))
            if ref not in pads or pad not in pads[ref]:
                continue
            got = pads[ref][pad].lstrip("/")
            want = str(p.get("net") or "").lstrip("/")
            if got.startswith("unconnected-") or got == "":
                idle += 1  # this design does not populate the slot behind that net: the board leaves the pad open
                continue
            n += 1
            if got != want:
                diff.append(f"{ref}.{pad} ({p.get('pin')}): board {got} vs backplane-v1 {want or 'unconnected'}")
        cs.append(check("pcb-u1-pad-nets", "each connected KiCad U1 socket pad carries the net backplane-v1 gives its DevKitC pin",
                        "not_run" if not n else not diff,
                        f"{n} connected pads compared, {idle} left open by this design" + (f"; differ: {'; '.join(diff[:10])}" if diff else ""),
                        qb="QB-19", artifact="pcb/backplane.kicad_pcb"))
    return cs


def pins_hash_check(out: Path, pins_h: Path) -> dict:
    """MIN-22: pins.h names the electrical.json it was generated from: file-bytes sha256, or a labelled canonical-JSON hash."""
    import hashlib

    txt = pins_h.read_text(errors="ignore")
    m = re.search(r"(canonical[- ]JSON\s+)?sha256\s+([0-9a-f]{64})", txt, re.I)
    ep = out / "electrical.json"
    if not m or not ep.exists():
        return check("pins-h-hash", "pins.h names the sha256 of the electrical.json it came from", "not_run",
                     "no sha256 line in pins.h" if not m else "no electrical.json", qb="QB-19", artifact="firmware/include/pins.h")
    got, labelled = m.group(2), bool(m.group(1))
    file_sha = hashlib.sha256(ep.read_bytes()).hexdigest()
    try:
        canon = hashlib.sha256(json.dumps(json.loads(ep.read_text()), sort_keys=True).encode()).hexdigest()
    except ValueError:
        canon = None
    if got == file_sha:
        st, det = "passed", "matches the file bytes of electrical.json"
    elif got == canon and labelled:
        st, det = "passed", "matches the canonical-JSON sha256 of electrical.json, and is labelled so"
    elif got == canon:
        st, det = "warning", "is the canonical-JSON sha256 of electrical.json but is labelled as a file sha256 (shasum will not match)"
    else:
        st, det = "failed", "matches neither the file bytes nor the canonical JSON of this electrical.json"
    return check("pins-h-hash", "pins.h names the sha256 of the electrical.json it came from", st, det, qb="QB-19",
                 artifact="firmware/include/pins.h")


# ============================================================================ file-based stage checks


def _has_own_checks(records: dict, sid: str) -> bool:
    """True when the stage reported its own checks (then its files are not re-checked here)."""
    s = (records.get(sid) or {}).get("summary")
    return bool(isinstance(s, dict) and s.get("checks"))


def file_checks(out: Path, records: dict) -> list[dict]:
    cs: list[dict] = []
    cad = read_json(out / "cad" / "digital-checks.json", None)
    if isinstance(cad, dict) and not _has_own_checks(records, "cad"):
        if "validComponents" in cad or "valid_components" in cad:
            cs.append(check("cad-valid", "every CAD component is a valid solid", cad.get("validComponents", cad.get("valid_components")), stage="cad",
                            artifact="cad/digital-checks.json"))
        pairs = cad.get("checks") or cad.get("pairs") or []
        if isinstance(pairs, list) and pairs:
            bad = [p for p in pairs if isinstance(p, dict) and float(p.get("intersectionMm3", p.get("intersection_mm3", 0)) or 0) > 0.01 and not p.get("allowed")]
            cs.append(check("cad-collisions", "no unintended intersections between rigid parts", not bad,
                            f"{len(pairs)} pairs checked, {len(bad)} over 0.01 mm3", qb="QB-08", stage="cad", artifact="cad/digital-checks.json"))
    pdir = out / "pcb"
    if pdir.exists() and not _has_own_checks(records, "pcb"):
        for f, title in (("drc.json", "PCB design-rule check"), ("erc.json", "schematic electrical-rule check")):
            c = drc_counts(pdir / f)
            if c:
                cs.append(check(f"pcb-{f[:3]}", f"{title}: no errors", c.get("errors", 0) == 0,
                                f"{c.get('errors', 0)} errors, {c.get('warnings', 0)} warnings" + (f", {c['unconnected']} unconnected" if c.get("unconnected") else ""),
                                stage="pcb", artifact=f"pcb/{f}"))
    fw = out / "firmware"
    if fw.exists() and not _has_own_checks(records, "firmware"):
        binp = fw / "firmware.bin"
        cs.append(check("fw-compiled", "firmware compiles for ESP32-S3", binp.exists() and binp.stat().st_size > 0,
                        f"{binp.stat().st_size:,} bytes" if binp.exists() else "no firmware/firmware.bin", stage="firmware", artifact="firmware/firmware.bin"))
        tr = fw / "test-results.txt"
        if tr.exists():
            p, t = parse_test_results(tr.read_text(errors="ignore"))
            cs.append(check("fw-tests", "native unit tests pass", (p == t) if t else None,
                            f"{p}/{t} passed" if t else "could not read the test counts", qb="QB-40", stage="firmware", artifact="firmware/test-results.txt"))
    return cs


# ============================================================================ stage entry


def reconcile_tipped_prints(checks: list[dict], out: Path) -> None:
    """The CAD checks every print STL as exported. A part too big for the printer that way, which the order stage then tipped
    onto a side and sliced (live failure 2026-11-02, Storybot Pal), does print: the CAD's bed_fit failure for it becomes a
    warning that says how it prints, like the order stage's own bed-fit check. If any part in the failure was not tipped and
    sliced, or is off the bed, the check stays failed."""
    ss = (read_json(out / "print" / "slice-stats.json", {}) or {}).get("parts") or {}
    tipped = {k: v for k, v in ss.items() if isinstance(v, dict) and v.get("tipped") and v.get("ok") and v.get("on_bed")}
    if not tipped:
        return
    cad = read_json(out / "cad" / "digital-checks.json", {}) or {}
    wt = next((c for c in cad.get("checks") or [] if isinstance(c, dict) and c.get("id") == "print_watertight"), {})
    meshes = (wt.get("data") or {}).get("meshes") or {}
    bad = [n for n, v in meshes.items() if isinstance(v, dict) and (not v.get("fits_bed") or not v.get("on_bed"))]
    if not bad or any(n not in tipped or not meshes[n].get("on_bed") for n in bad):
        return
    how = "; ".join(f"{n} {(tipped[n].get('orientation') or {}).get('label')}"
                    + (f", {tipped[n].get('support_g'):g} g of support sliced" if tipped[n].get("supports") else "") for n in bad)
    for c in checks:
        if c.get("stage") == "cad" and c.get("id") == "bed_fit" and c.get("status") == "failed":
            c["status"] = "warning"
            c["detail"] = plain(f"{c.get('detail')}; too big for the printer as exported, so the order stage prints it on its side "
                                f"and sliced and priced it that way ({how}; print/slice-stats.json)")


def summarize(checks: list[dict]) -> dict:
    return {s: sum(1 for c in checks if c["status"] == s) for s in ("passed", "failed", "warning", "not_run")}


def run(ctx) -> dict:
    out = Path(ctx.out)
    ctx.progress(STAGE, "collecting stage checks", 10)
    records = _stage_records(out)
    spec = ctx.spec.to_json_dict(True) if hasattr(ctx.spec, "to_json_dict") else dict(ctx.spec)
    electrical = getattr(ctx, "electrical", None) or read_json(out / "electrical.json", None)

    build = assemble_build_json(out, records)

    stages: dict = {}
    all_checks: list[dict] = []
    for sid in ("normalize", "electrical", "cad", "pcb", "firmware", "order", "manual"):
        rec = records.get(sid) or {}
        state = rec.get("state", "not_run")
        own = rec.get("summary") if isinstance(rec.get("summary"), dict) else ((rec.get("details") or {}).get("summary")
                                                                              if isinstance(rec.get("details"), dict) else None)
        cs = normalize_checks((own or {}).get("checks"), sid) if isinstance(own, dict) else []  # a failed stage's own checks count too
        if state in ("failed", "skipped") or not rec:
            cs.append(check(f"stage:{sid}", f"{sid} stage completed", "failed" if state == "failed" else "not_run",
                            rec.get("message") or ("did not run" if not rec else state), stage=sid))
        stages[sid] = {"state": state if rec else "not_run", "message": rec.get("message"), "log": rec.get("log"), "checks": len(cs)}
        all_checks += cs
    all_checks += file_checks(out, records)
    ctx.progress(STAGE, "cross-stage joins", 50)
    all_checks += cross_checks(out, spec, build, electrical, getattr(ctx, "lib", None), records)
    for c in all_checks:  # MIN-28: every record names the file it read
        c.setdefault("artifact", default_artifact(c.get("stage", "pipeline"), str(c.get("id"))))
        if not c.get("artifact"):
            c["artifact"] = default_artifact(c.get("stage", "pipeline"), str(c.get("id")))
        c["detail"] = plain(c.get("detail"))
    reconcile_tipped_prints(all_checks, out)

    build["tests"] = final_tests(build.get("tests") or [], all_checks, spec, records, out)
    write_json(out / "build.json", build)

    counts = summarize(all_checks)
    doc = {"schema": "gadget-digital-checks/1", "summary": counts,
           "all_passed": counts["failed"] == 0 and counts["not_run"] == 0,
           "stages": stages, "checks": all_checks, "limits": LIMITS}
    write_json(out / "checks" / "digital-checks.json", doc)
    msg = f"{counts['passed']} passed, {counts['failed']} failed, {counts['warning']} warnings, {counts['not_run']} not run"
    return {"message": msg, "summary": counts, "file": "checks/digital-checks.json"}
