"""Package stage: delivery checks, manifest and export.

    run(ctx) -> summary

1. Re-assembles build.json from its fragments, regenerates tests[] from checks/digital-checks.json (nothing is copied
   from the shipped file, MIN-21) and checks the result equals the file on disk (QB-46). Re-runs
   gstudio.manual.finalize and diffs BUILD_GUIDE.md and manual/manual-summary.json with timestamps normalised.
2. checks/delivery-checks.json: every count computed from the files present at packaging time (QB-45), plus
   the physical checks that are still needed. Nothing physical is ever reported as done.
3. manifest.json: {engine_version, created, root, files: [{path, bytes, sha256}]} for every delivered file,
   then re-verified from disk (QB-44).
4. export.zip: every delivered file plus manifest.json under a top folder `<slug>/`, with fixed timestamps so
   identical inputs give an identical zip; re-opened and CRC-tested after writing.

Delivered = everything under the version root except logs/, .gstudio/, build caches (.pio, __pycache__),
status.json (it keeps changing), the live CAD draft (cad/draft.glb), export.zip and manifest.json themselves.
Everything the firmware stage writes under firmware/ (including firmware-merged.bin and
web-flasher-manifest.json for in-browser flashing) is delivered.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Any

STAGE = "package"
EXCLUDE_DIRS = {"logs", ".gstudio", ".pio", "__pycache__", ".cache", "node_modules", ".git", ".vscode"}
EXCLUDE_TOP = {"export.zip", "manifest.json", "status.json"}
EXCLUDE_REL = {"cad/draft.glb"}  # the live CAD draft; the delivered model is cad/assembly.glb
ZIP_TIME = (2026, 1, 1, 0, 0, 0)


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


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def delivered_files(out: Path) -> list[Path]:
    out = Path(out)
    files: list[Path] = []
    for root, dirs, names in os.walk(out):
        rel_root = Path(root).relative_to(out)
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith("."))
        for n in sorted(names):
            if n.startswith(".") or n.endswith((".pyc", ".tmp")):
                continue
            if rel_root == Path(".") and n in EXCLUDE_TOP:
                continue
            if (rel_root / n).as_posix() in EXCLUDE_REL:
                continue
            if rel_root.as_posix() == "cad/components" and n.startswith("REFERENCE_") and n.endswith(".brep"):
                continue  # MAJ-38: cable-route / slack-loop envelopes; cad/cable-routes.json and assembly.glb carry them
            if rel_root.as_posix() == "firmware/flash" and n == "firmware.bin":
                continue  # MAJ-38: one firmware.bin (firmware/firmware.bin is the app part at 0x10000)
            p = Path(root) / n
            if p.is_file() and not p.is_symlink():
                files.append(p)
    return sorted(files, key=lambda p: p.relative_to(out).as_posix())


def make_manifest(out: Path, files: list[Path], created: str) -> dict:
    try:
        from gstudio.version import ENGINE_VERSION
    except ImportError:  # pragma: no cover
        ENGINE_VERSION = "unknown"
    return {"schema": "gadget-manifest/1", "engine_version": ENGINE_VERSION, "created": created, "root": ".",
            "files": [{"path": p.relative_to(out).as_posix(), "bytes": p.stat().st_size, "sha256": sha256(p)} for p in files]}


def verify_manifest(out: Path, manifest: dict) -> list[str]:
    """Problems found re-reading every listed file, and any delivered file the manifest misses."""
    probs = []
    listed = set()
    for f in manifest.get("files") or []:
        p = Path(out) / f["path"]
        listed.add(f["path"])
        if not p.is_file():
            probs.append(f"missing: {f['path']}")
        elif p.stat().st_size != f["bytes"]:
            probs.append(f"size differs: {f['path']}")
        elif sha256(p) != f["sha256"]:
            probs.append(f"sha256 differs: {f['path']}")
    for p in delivered_files(out):
        rel = p.relative_to(out).as_posix()
        if rel not in listed:
            probs.append(f"not in manifest: {rel}")
    return probs


def make_zip(out: Path, slug: str, files: list[Path], zpath: Path) -> int:
    tmp = zpath.with_name(".export.zip.tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in [*files, out / "manifest.json"]:
            rel = p.relative_to(out).as_posix()
            zi = zipfile.ZipInfo(f"{slug}/{rel}", date_time=ZIP_TIME)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            with open(p, "rb") as src, zf.open(zi, "w") as dst:
                for chunk in iter(lambda: src.read(1 << 20), b""):
                    dst.write(chunk)
    tmp.replace(zpath)
    return zpath.stat().st_size


def verify_zip(zpath: Path, slug: str, manifest: dict) -> list[str]:
    probs = []
    with zipfile.ZipFile(zpath) as zf:
        bad = zf.testzip()
        if bad:
            probs.append(f"CRC error in {bad}")
        names = set(zf.namelist())
        want = {f"{slug}/{f['path']}" for f in manifest["files"]} | {f"{slug}/manifest.json"}
        if names != want:
            probs.append(f"zip entries differ from the manifest: {sorted(names ^ want)[:10]}")
        if any(not n.startswith(f"{slug}/") for n in names):
            probs.append("an entry is outside the top folder")
    return probs


# ------------------------------------------------------------------ delivery checks (QB-45)

DEFAULT_PHYSICAL = [
    ("Fit coupon", "the printed fit coupon accepts the real button and screws without force"),
    ("First power-on", "with only USB-C power connected the device boots and shows its ready state"),
    ("Controls, light and sound", "every button, light, display and speaker function works on the real device"),
    ("Closed-case warm-up", "after 30 minutes running closed, no surface is uncomfortably warm and nothing smells hot"),
    ("Stability and cord", "the closed device stands steady on a table and its cord is routed away from the user"),
]


def delivery_checks(out: Path, build: dict, digital: dict | None) -> dict:
    comps = read_json(out / "cad" / "components.json", {}) or {}
    boxes = [c.get("bbox") for n, c in comps.items() if isinstance(c, dict) and not n.startswith("REFERENCE_")
             and isinstance(c.get("bbox"), (list, tuple)) and len(c["bbox"]) == 6]
    size = None
    if boxes:
        lo = [min(b[i] for b in boxes) for i in (0, 2, 4)]
        hi = [max(b[i] for b in boxes) for i in (1, 3, 5)]
        size = [round(hi[i] - lo[i], 3) for i in range(3)]
    checks = (digital or {}).get("checks") or []
    by = lambda pre: [c for c in checks if str(c.get("id", "")).startswith(pre)]  # noqa: E731
    steps = build.get("steps") or []
    refs = sorted({str(v["path"]) for s in steps if isinstance(s, dict) for v in s.get("visuals") or [] if isinstance(v, dict) and v.get("path")})
    imgs = sorted(p.relative_to(out).as_posix() for p in (out / "manual").glob("*")
                  if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm")) if (out / "manual").exists() else []
    guide = (out / "BUILD_GUIDE.md").read_text(errors="ignore") if (out / "BUILD_GUIDE.md").exists() else ""
    in_guide = {i for i in imgs if i in guide}
    phys = [{"title": t.get("title"), "expected": t.get("expected")} for t in build.get("tests") or []
            if isinstance(t, dict) and t.get("kind") == "physical"]
    phys_src = "build.json tests[] (kind physical)"
    if not phys:
        phys = [{"title": t, "expected": e} for t, e in DEFAULT_PHYSICAL]
        phys_src = "pipeline default list (the manual stage listed none)"
    # physical checks a stage reported in its own summary (e.g. pcb.fabricated) are still needed too
    try:
        from gstudio.pipeline import stage_records

        for sid, rec in stage_records(out).items():
            s_ = rec.get("summary") if isinstance(rec.get("summary"), dict) else {}
            for c in s_.get("checks") or []:
                if isinstance(c, dict) and c.get("kind") == "physical" and c.get("status") != "passed":
                    item = {"title": c.get("title") or c.get("id"), "expected": c.get("detail") or c.get("expected"), "stage": sid}
                    if all(item["title"] != x.get("title") for x in phys):
                        phys.append(item)
    except Exception:  # noqa: BLE001 - the list above stands on its own
        pass
    fw = build.get("firmware") or {}
    pcb = build.get("pcb") or {}
    collisions = [c for c in by("cad-collisions")] or [c for c in checks if c.get("qb") == "QB-08"]
    all_imgs = sorted(p.relative_to(out).as_posix() for p in (out / "manual").glob("*")) if (out / "manual").exists() else []
    return {
        "schema": "gadget-delivery-checks/1",
        "computedFrom": "files present at packaging time",
        "assemblySizeMm": size,
        "printFiles": len(list(out.glob("print/PRINT_*.stl"))),
        "testCoupons": len(list(out.glob("print/TEST_*.stl"))),
        "watertightPrintFiles": sum(1 for c in by("watertight:") if c.get("status") == "passed"),
        "rigidPairCheckRecords": len(collisions),
        "purchasedEntries": len(build.get("purchased") or []),
        "printedEntries": len(build.get("printed") or []),
        "assemblySteps": len(steps),
        "wiringConnections": len((build.get("wiring") or {}).get("connections") or []) if isinstance(build.get("wiring"), dict) else 0,
        "manualImages": len(imgs),
        "manualImagesPng": sum(1 for i in all_imgs if i.lower().endswith(".png")),
        "manualImagesSvg": sum(1 for i in all_imgs if i.lower().endswith(".svg")),
        "manualImagesReferenced": len(set(refs) | in_guide),
        "missingImages": sorted(r for r in refs if not (out / r).exists()),
        "orphanImages": sorted(i for i in imgs if i not in refs and i not in in_guide),
        "unconfirmedPrices": [r.get("name") for r in build.get("purchased") or [] if r.get("estimate")],
        "factoryWork": [f"{w.get('name')} ({w.get('by')})" for w in build.get("factory_work") or []],
        "makerSoldering": [r.get("name") for r in build.get("purchased") or [] if r.get("solderedBy") == "maker"],
        "firmwareCompiled": fw.get("compiled"),
        "firmwareTests": f"{fw.get('tests_passed')}/{fw.get('tests_total')}" if fw.get("tests_total") is not None else None,
        "pcbDrcErrors": pcb.get("drc_errors"),
        "pcbErcErrors": pcb.get("erc_errors"),
        "pcbRouted": pcb.get("routed"),
        "digitalChecks": (digital or {}).get("summary"),
        "physicalChecksStillNeeded": phys,
        "physicalChecksSource": phys_src,
        "physicalTesting": "Not performed; see BUILD_GUIDE.md",
        "storyAudio": story_audio_delivery(out),
        "listingSnapshots": listing_snapshots(out, build),
        "buildable": buildable(out),
    }


def story_audio_delivery(out: Path) -> dict | None:
    """BLK-4 delivery check `story-audio-present`: stories.json says audio and every listed file ships."""
    sd = out / "firmware" / "sdcard"
    d = read_json(sd / "stories.json", None)
    if not isinstance(d, dict):
        return None
    files = [f for st in d.get("stories") or [] for f in st.get("files") or []]
    if isinstance(d.get("default_name"), dict) and d["default_name"].get("file"):
        files.append(d["default_name"]["file"])
    files += [n.get("file") for n in d.get("names") or [] if isinstance(n, dict) and n.get("file")]
    missing = [f for f in files if not (sd / f).is_file() or (sd / f).stat().st_size == 0]
    ok = bool(d.get("audio")) and not missing
    return {"id": "story-audio-present", "status": "passed" if ok else "failed", "audio": bool(d.get("audio")),
            "files": len(files), "missing": missing[:20], "missing_count": len(missing), "tts_model": d.get("tts_model"),
            "detail": ("every story segment and name clip has audio" if ok else
                       "stories have no audio yet (text only)" if not d.get("audio") else f"{len(missing)} audio file(s) missing")}


def listing_snapshots(out: Path, build: dict) -> dict:
    """MAJ-25: every seen row's saved listing snapshot is present and its sha256 equals the one in its receipt."""
    import gzip

    rows = [r for r in build.get("purchased") or [] if r.get("evidence_tier") == "seen"]
    bad = []
    for r in rows:
        lst = r.get("listing") or {}
        rel = lst.get("snapshot")
        try:
            raw = gzip.decompress((out / rel).read_bytes()) if rel else None
        except (OSError, EOFError):
            raw = None
        if raw is None or hashlib.sha256(raw).hexdigest() != lst.get("snapshot_sha256"):
            bad.append(r.get("name"))
    return {"seen_rows": len(rows), "snapshots_ok": len(rows) - len(bad), "bad": bad}


def buildable(out: Path) -> dict | None:
    g = read_json(out / ".gstudio" / "gate.json", None)
    return {k: g.get(k) for k in ("buildable", "verdict", "verdict_message")} if isinstance(g, dict) else None


_TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?|\"seconds\":\s*[0-9.]+|\"[a-z_]*seconds\":\s*\{[^}]*\}")


def _norm(text: str) -> str:
    return _TS.sub("<t>", text)


def finalize_reproducible(ctx, out: Path) -> tuple[str, str]:
    """(status, detail): re-run gstudio.manual.finalize and diff BUILD_GUIDE.md and manual/manual-summary.json."""
    import importlib

    modname = (getattr(ctx, "artifacts", None) or {}).get("finalize.module") or "gstudio.manual"
    try:
        fn = getattr(importlib.import_module(modname), "finalize", None)
    except Exception as exc:  # noqa: BLE001
        return "not_run", f"{modname} not importable: {exc}"
    if not callable(fn):
        return "not_run", f"{modname}.finalize is not available yet"
    files = [out / "BUILD_GUIDE.md", out / "manual" / "manual-summary.json"]
    if not files[0].exists():
        return "not_run", "no BUILD_GUIDE.md"
    before = {f: f.read_text(errors="replace") for f in files if f.exists()}
    try:
        fn(ctx)
    except Exception as exc:  # noqa: BLE001
        return "failed", f"finalize raised {type(exc).__name__}: {str(exc)[:200]}"
    diff, first = [], ""
    for f, t in before.items():
        now = f.read_text(errors="replace") if f.exists() else ""
        if _norm(now) != _norm(t):
            diff.append(f.relative_to(out).as_posix())
            a, b = _norm(t).splitlines(), _norm(now).splitlines()
            i = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]), min(len(a), len(b)))
            first = first or f"{f.name} line {i + 1}: {(a[i] if i < len(a) else '<end>')[:120]!r} -> {(b[i] if i < len(b) else '<end>')[:120]!r}"
    return ("failed" if diff else "passed"), (f"re-rendered differently: {', '.join(diff)} (first difference {first})" if diff else
                                             "BUILD_GUIDE.md and manual-summary.json re-render identically (timestamps normalised)")


# ------------------------------------------------------------------ stage entry


def ship_component_sources(out: Path) -> tuple[str, str]:
    """MIN-23: copy every source file components.json names (cad source_files) from library/ into the export, sha256 checked."""
    comps = read_json(out / "cad" / "components.json", {}) or {}
    want = [(n, f) for n, c in comps.items() if isinstance(c, dict) for f in c.get("source_files") or [] if isinstance(f, dict)]
    if not want:
        return "not_run", "components.json names no source files"
    lib = Path(__file__).resolve().parents[2] / "library"
    bad = []
    for name, f in want:
        src = lib / str(f.get("library_path") or "")
        dst = out / str(f.get("path") or "")
        try:
            raw = src.read_bytes()
        except OSError:
            bad.append(f"{name}: {f.get('library_path')} missing")
            continue
        if hashlib.sha256(raw).hexdigest() != f.get("sha256"):
            bad.append(f"{name}: {f.get('library_path')} sha256 differs")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(raw)
    files = {f.get("path") for _n, f in want}
    return ("passed" if not bad else "failed"), (f"{len(files)} source files for {len({n for n, _f in want})} components shipped with matching sha256"
                                                 if not bad else "; ".join(bad[:8]))


def speech_roundtrip_delivery(out: Path) -> tuple[str, str]:
    """MAJ-18 delivery check: a card with audio ships firmware/speech-roundtrip.json with ok true and the scope line."""
    d = read_json(out / "firmware" / "sdcard" / "stories.json", None)
    if not isinstance(d, dict):
        return "not_run", "no story card in this design"
    if not d.get("audio"):
        return "not_run", "the story card has no audio, so there is no speech to round-trip"
    rt = read_json(out / "firmware" / "speech-roundtrip.json", None)
    if not isinstance(rt, dict):
        return "failed", "stories.json has audio:true but firmware/speech-roundtrip.json is missing"
    scope = str(rt.get("scope") or "")
    ok = rt.get("ok") is True and scope == "synthetic voice, no speaker or child tested"
    return ("passed" if ok else "failed"), (f"ok {rt.get('ok')}, scope '{scope}', segments {rt.get('segments_passed')}/"
                                            f"{rt.get('segments_total')} at WER <= {rt.get('wer_limit')}, worst {rt.get('worst_segment_wer')}, "
                                            f"names exact {rt.get('names_exact')}")


HOME_PATH = re.compile(rb"/(?:Users|home)/[A-Za-z0-9._-]+/")


def home_paths_in_zip(zpath: Path) -> tuple[str, str]:
    """MIN-19: no delivered file names a home directory (/Users/<name>/ or /home/<name>/)."""
    hits = []
    with zipfile.ZipFile(zpath) as zf:
        for info in zf.infolist():
            if info.file_size > 64 * 1024 * 1024:
                continue
            with zf.open(info) as fh:
                if HOME_PATH.search(fh.read()):
                    hits.append(info.filename)
    return ("passed" if not hits else "failed"), ("no home-directory paths in any file" if not hits else
                                                  f"{len(hits)} file(s) name a home directory: {', '.join(hits[:8])}")


def story_audio_in_zip(out: Path, slug: str) -> tuple[str, str]:
    sa = story_audio_delivery(out)
    if sa is None:
        return "not_run", "no story card in this design"
    sd = out / "firmware" / "sdcard"
    d = read_json(sd / "stories.json", {}) or {}
    files = [f for st in d.get("stories") or [] for f in st.get("files") or []]
    if isinstance(d.get("default_name"), dict) and d["default_name"].get("file"):
        files.append(d["default_name"]["file"])
    with zipfile.ZipFile(out / "export.zip") as zf:
        names = set(zf.namelist())
    absent = [f for f in files if f"{slug}/firmware/sdcard/{f}" not in names]
    if not d.get("audio"):
        return "failed", f"stories have no audio yet (text only); {len(absent)}/{len(files)} files not in export.zip"
    return ("passed" if not absent else "failed"), (f"{len(files)} files in export.zip" if not absent else f"not in export.zip: {', '.join(absent[:10])}")


def run(ctx) -> dict:
    from gstudio.checks import assemble_build_json, final_tests
    from gstudio.errors import StageError
    from gstudio.pipeline import now_iso

    out = Path(ctx.out)
    spec = ctx.spec.to_json_dict(True) if hasattr(ctx.spec, "to_json_dict") else dict(ctx.spec)
    slug = str(spec.get("slug") or "gadget")
    problems: list[str] = []

    ctx.progress(STAGE, "re-rendering the guide's final checks", 3)
    guide_status, guide_detail = finalize_reproducible(ctx, out)
    ctx.progress(STAGE, "re-assembling build.json", 5)
    build = read_json(out / "build.json", {}) or {}
    digital0 = read_json(out / "checks" / "digital-checks.json", None) or {}
    from gstudio.pipeline import stage_records

    regen = assemble_build_json(out)
    regen["tests"] = final_tests(regen.get("tests") or [], digital0.get("checks") or [], spec, stage_records(out), out)  # MIN-21
    reproducible = json.dumps(regen, sort_keys=True, default=str) == json.dumps(build, sort_keys=True, default=str)
    if not reproducible:
        keys = sorted(k for k in set(regen) | set(build) if json.dumps(regen.get(k), sort_keys=True, default=str)
                      != json.dumps(build.get(k), sort_keys=True, default=str))
        problems.append(f"build.json differs from a fresh assembly of its fragments and checks in: {', '.join(keys)}")

    # a stdlib-only re-checker ships with every delivery (the benchmark ships a verify_delivery.py too)
    import shutil

    shutil.copyfile(Path(__file__).resolve().parent / "docs_gen" / "verify_delivery.py", out / "verify_delivery.py")

    src_status, src_detail = ship_component_sources(out)  # MIN-23: before hashing, so the manifest lists them
    ctx.progress(STAGE, "delivery checks", 15)
    digital = read_json(out / "checks" / "digital-checks.json", None)
    write_json(out / "checks" / "delivery-checks.json", delivery_checks(out, build, digital))

    ctx.progress(STAGE, "hashing files", 30)
    files = delivered_files(out)
    manifest = make_manifest(out, files, now_iso())
    write_json(out / "manifest.json", manifest)
    vprobs = verify_manifest(out, manifest)
    problems += vprobs

    ctx.progress(STAGE, f"zipping {len(files)} files", 60)
    zbytes = make_zip(out, slug, files, out / "export.zip")
    zprobs = verify_zip(out / "export.zip", slug, manifest)
    problems += zprobs
    sa_status, sa_detail = story_audio_in_zip(out, slug)
    rt_status, rt_detail = speech_roundtrip_delivery(out)
    hp_status, hp_detail = home_paths_in_zip(out / "export.zip")
    ctx.progress(STAGE, "re-running verify_delivery.py", 90)
    import subprocess
    import sys

    vr = subprocess.run([sys.executable, "-I", str(out / "verify_delivery.py")], capture_output=True, text=True, timeout=300, cwd=str(out))
    verify_ok = vr.returncode == 0
    vlast = ((vr.stdout or vr.stderr or "").strip().splitlines() or [""])[-1]
    total = sum(f["bytes"] for f in manifest["files"])
    summary = {"files": len(files), "bytes": total, "zip_bytes": zbytes, "manifest": "manifest.json", "export": "export.zip",
               "top_folder": f"{slug}/", "build_json_reproducible": reproducible, "problems": problems,
               "verify_delivery": (vr.stdout or "")[-4000:],
               "checks": [
                   {"id": "manifest-verifies", "title": "manifest lists every delivered file and every hash verifies", "status": "passed" if not vprobs else "failed",
                    "detail": f"{len(files)} files" + (f"; {vprobs[:5]}" if vprobs else ""), "qb": "QB-44"},
                   {"id": "zip-verifies", "title": "export.zip opens, CRCs pass and entries equal the manifest", "status": "passed" if not zprobs else "failed",
                    "detail": f"{zbytes:,} bytes" + (f"; {zprobs[:5]}" if zprobs else "")},
                   {"id": "build-json-reproducible", "title": "build.json re-assembles identically from its fragments and checks (tests[] regenerated)",
                    "status": "passed" if reproducible else "failed", "qb": "QB-46",
                    "detail": "identical" if reproducible else next((p for p in problems if p.startswith("build.json differs")), "differs")},
                   {"id": "guide-reproducible", "title": "BUILD_GUIDE.md and manual-summary.json re-render identically from build.json",
                    "status": guide_status, "qb": "QB-46", "detail": guide_detail},
                   {"id": "story-audio-in-export", "title": "every story audio file the card lists is in export.zip",
                    "status": sa_status, "detail": sa_detail},
                   {"id": "component-sources-in-export", "title": "every source file components.json names ships in the export with its sha256",
                    "status": src_status, "detail": src_detail, "qb": "QB-12"},
                   {"id": "speech-roundtrip-in-export", "title": "a card with story audio ships its speech round-trip report (ok, scope stated)",
                    "status": rt_status, "detail": rt_detail, "qb": "QB-41"},
                   {"id": "no-home-paths", "title": "no delivered file names a home directory", "status": hp_status, "detail": hp_detail},
                   {"id": "verify-delivery", "title": "the shipped verify_delivery.py passes on the finished delivery",
                    "status": "passed" if verify_ok else "failed", "detail": vlast},
               ],
               "message": f"{len(files)} files, {total / 1e6:.1f} MB, export.zip {zbytes / 1e6:.1f} MB"}
    if vprobs or zprobs:
        raise StageError("package verification failed: " + "; ".join((vprobs + zprobs)[:5]), {"summary": summary})
    return summary
