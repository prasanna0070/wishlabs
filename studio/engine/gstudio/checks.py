"""Digital checks: one place that says what was checked, by which stage, against which file.

    run(ctx) -> summary          (pipeline stage "checks")

1. Re-assembles build.json from its fragments (order's purchased/printed/order, manual's steps/wiring/tests,
   firmware and pcb blocks from their summaries and files), so no stage's write can be lost to a race.
2. Collects every stage's own checks (`summary["checks"]`) and the well-known check files
   (cad/digital-checks.json, pcb/drc.json, pcb/erc.json, firmware/test-results.txt).
3. Adds the pipeline's cross-stage joins: QB-06 CAD <-> BOM, QB-15 cable length, QB-16 BREP vs mesh volume,
   QB-07 watertight print meshes, QB-18 wiring <-> BOM, QB-19 firmware pins <-> electrical, QB-20 power budget,
   QB-21 no mains, QB-29/30 step shape, QB-34 manual images.
4. Writes checks/digital-checks.json. Physical checks are never "passed": they go to delivery-checks.json
   (written by gstudio.package at packaging time, QB-45).
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


def check(cid: str, title: str, status: Any, detail: str = "", *, qb: str | None = None, stage: str = "pipeline",
          artifact: str | None = None) -> dict:
    d = {"id": cid, "title": title, "status": status if status in STATUS_ORDER else norm_status(status), "detail": detail, "stage": stage}
    if qb:
        d["qb"] = qb
    if artifact:
        d["artifact"] = artifact
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
        d = check(cid, str(c.get("title") or c.get("name") or cid), norm_status(st), str(c.get("detail") or c.get("message") or c.get("details") or ""),
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
    for k, v in cur.items():  # keep anything else a stage added (e.g. hardware)
        b.setdefault(k, v)
    return b


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


def cross_checks(out: Path, spec: dict, build: dict, electrical: dict | None) -> list[dict]:
    cs: list[dict] = []
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
                        (f"{len(pcs) - len(unlinked)}/{len(pcs)} linked" + (f"; unlinked: {unlinked}" if unlinked else "")
                         + (f"; modelPart not in CAD: {dangling}" if dangling else "")), qb="QB-06", artifact="cad/components.json"))
        nogeo = [r["name"] for r in purchased if r.get("kind") in ("module",) and not r.get("modelPart")]
        cs.append(check("bom-cad-join", "every purchased module has CAD geometry", "passed" if not nogeo else "warning",
                        "all linked" if not nogeo else f"no PURCHASE_ component for: {nogeo}", qb="QB-06"))
    else:
        cs.append(check("cad-bom-join", "every PURCHASE_ component maps to a purchased row", "not_run", "no cad/components.json", qb="QB-06"))

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
                cs.append(check(f"watertight:{name}", f"{name}: closed mesh, one body", "not_run", f"trimesh could not load it: {exc}", qb="QB-07"))

    # QB-15 cable route length <= purchased cable length
    routes = read_json(out / "cad" / "cable-routes.json", None)
    if isinstance(routes, dict):
        routes = routes.get("routes") or list(routes.values())
    if isinstance(routes, list) and routes:
        bad, okc, unk = [], 0, []
        for r in routes:
            if not isinstance(r, dict):
                continue
            m = re.search(r"(\d+(?:\.\d+)?)\s*mm", str(r.get("cable") or ""))
            have = float(m.group(1)) if m else None
            need = r.get("route_length_mm") or r.get("path_length_mm") or r.get("pathLengthMm") or polyline_length(r.get("points"))
            if not need and r.get("length_mm") and have is None:
                need = r.get("length_mm")
            rid = str(r.get("id") or r.get("name") or "?")
            if have is None or not need:
                unk.append(rid)
            elif float(need) <= have + 0.5:
                okc += 1
            else:
                bad.append(f"{rid}: route {float(need):.0f} mm > cable {have:.0f} mm")
        st = "failed" if bad else ("passed" if okc and not unk else ("warning" if okc else "not_run"))
        cs.append(check("cable-length", "every cable route is no longer than its purchased cable", st,
                        f"{okc} ok" + (f"; too long: {bad}" if bad else "") + (f"; length unknown: {unk}" if unk else ""),
                        qb="QB-15", artifact="cad/cable-routes.json"))

    # QB-18 wiring.parts[].item == purchased[].name
    wiring = build.get("wiring") or {}
    names = {r.get("name") for r in purchased}
    if isinstance(wiring, dict) and wiring.get("parts"):
        missing = sorted({str(p.get("item")) for p in wiring["parts"] if isinstance(p, dict) and p.get("item") not in names})
        cs.append(check("wiring-bom-join", "every wiring part is a purchased item", not missing,
                        "all joined" if not missing else f"not in purchased[]: {missing}", qb="QB-18"))
        part_ids = {p.get("id") for p in wiring["parts"] if isinstance(p, dict)}
        bad_ends = sorted({e for c in wiring.get("connections") or [] if isinstance(c, dict)
                           for e in (str(c.get("from", "")).split(".")[0], str(c.get("to", "")).split(".")[0]) if e not in part_ids})
        cs.append(check("wiring-endpoints", "every wiring connection is <part>.<pin> on a listed part", not bad_ends,
                        "all endpoints known" if not bad_ends else f"unknown parts: {bad_ends}", qb="QB-17"))
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
        cs.append(check("pins-match", "firmware pin constants equal the electrical pin map", st,
                        f"{len(found)} constants compared" + (f"; mismatches: {mism}" if mism else "")
                        + ("" if found else "; no pins.h constant matched an electrical pin or signal name"),
                        qb="QB-19", artifact="firmware/include/pins.h"))
    elif gpio:
        cs.append(check("pins-match", "firmware pin constants equal the electrical pin map", "not_run", "no firmware/include/pins.h", qb="QB-19"))

    # in-browser flashing: every file the web-flasher manifest names ships in the delivery
    man, parts = flasher_parts(out)
    if man is not None:
        missing = [pth for pth, _o, ok in parts if not ok]
        cs.append(check("web-flasher", "the web-flasher manifest names firmware files that ship in this delivery", bool(parts) and not missing,
                        f"{len(parts)} part(s)" + (f"; missing or empty: {missing}" if missing else "") if parts else "manifest lists no parts",
                        artifact="firmware/web-flasher-manifest.json", stage="firmware"))

    # step parts exist in the CAD (the join the benchmark's verify_delivery.py asserts)
    comp_names = set(comps)
    if comps and build.get("steps"):
        coupons = {p.stem for p in (out / "print").glob("TEST_*.stl")}  # coupons are STL-only by convention
        unknown = sorted({p for s_ in build["steps"] if isinstance(s_, dict) for p in (s_.get("parts") or []) + (s_.get("highlight") or [])
                          if isinstance(p, str) and p not in comp_names and p not in coupons})
        cs.append(check("step-parts-in-cad", "every part a step names exists in the CAD (or as a TEST_ coupon STL)", not unknown,
                        "all found" if not unknown else f"not in cad/components.json: {unknown}", qb="QB-29"))

    # QB-20 power budget, QB-21 no mains
    pw = (electrical or {}).get("power") or {}
    if pw:
        ok = pw.get("ok")
        cs.append(check("power-budget", "worst-case current is within the USB-C supply limit", "passed" if ok else ("failed" if ok is False else "not_run"),
                        f"{pw.get('total_ma_max')} mA max vs {pw.get('limit_ma')} mA limit (calculated from module data, not measured)", qb="QB-20",
                        artifact="electrical.json"))
    inp = ((spec.get("electronics") or {}).get("power") or {}).get("input")
    cs.append(check("no-mains", "no mains voltage inside the enclosure (5 V USB-C input only)", inp == "usb_c_5v", f"power input: {inp}", qb="QB-21"))

    # QB-29 / QB-30 step shape, QB-34 manual images
    steps = build.get("steps") or []
    if steps:
        no_parts = [s.get("id") for s in steps if isinstance(s, dict) and not s.get("parts")]
        bad_head = [s.get("id") for s in steps if isinstance(s, dict)
                    and not (str(s.get("instruction", "")).lstrip().startswith("**Use:**") and "**Tools:**" in str(s.get("instruction", "")))]
        ids = [s.get("id") for s in steps if isinstance(s, dict)]
        cs.append(check("step-ids", "every step has a unique stable id, title, instruction and expected result",
                        len(set(ids)) == len(ids) and all(isinstance(s, dict) and s.get("id") and s.get("title") and s.get("instruction") and s.get("expected") for s in steps),
                        f"{len(steps)} steps", qb="QB-29"))
        cs.append(check("step-parts", "every step lists the parts it handles", "passed" if not no_parts else "warning",
                        "all steps list parts" if not no_parts else f"steps without parts: {no_parts}", qb="QB-29"))
        cs.append(check("step-use-tools", "every step opens with **Use:** and **Tools:**", not bad_head,
                        "all steps" if not bad_head else f"missing in: {bad_head}", qb="QB-30"))
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
                        f"{len(imgs)} images, {len(refs)} referenced" + (f"; missing: {missing}" if missing else "") + (f"; orphans: {orphans}" if orphans else ""),
                        qb="QB-34"))
    return cs


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
        cs = normalize_checks((rec.get("summary") or {}).get("checks"), sid) if isinstance(rec.get("summary"), dict) else []
        if state in ("failed", "skipped") or not rec:
            cs.append(check(f"stage:{sid}", f"{sid} stage completed", "failed" if state == "failed" else "not_run",
                            rec.get("message") or ("did not run" if not rec else state), stage=sid))
        stages[sid] = {"state": state if rec else "not_run", "message": rec.get("message"), "log": rec.get("log"), "checks": len(cs)}
        all_checks += cs
    all_checks += file_checks(out, records)
    ctx.progress(STAGE, "cross-stage joins", 50)
    all_checks += cross_checks(out, spec, build, electrical)

    kept = [t for t in build.get("tests") or [] if t.get("source") != "checks"]
    tests = kept + digital_tests(all_checks, kept)
    build["tests"] = tests
    write_json(out / "build.json", build)

    counts = summarize(all_checks)
    doc = {"schema": "gadget-digital-checks/1", "summary": counts,
           "all_passed": counts["failed"] == 0 and counts["not_run"] == 0,
           "stages": stages, "checks": all_checks, "limits": LIMITS}
    write_json(out / "checks" / "digital-checks.json", doc)
    msg = f"{counts['passed']} passed, {counts['failed']} failed, {counts['warning']} warnings, {counts['not_run']} not run"
    return {"message": msg, "summary": counts, "file": "checks/digital-checks.json"}
