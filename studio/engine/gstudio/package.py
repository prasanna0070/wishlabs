"""Package stage: delivery checks, manifest and export.

    run(ctx) -> summary

1. Re-assembles build.json from its fragments and checks the result equals the file on disk (QB-46).
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
        "manualImagesReferenced": len(set(refs) | in_guide),
        "missingImages": sorted(r for r in refs if not (out / r).exists()),
        "orphanImages": sorted(i for i in imgs if i not in refs and i not in in_guide),
        "unconfirmedPrices": [r.get("name") for r in build.get("purchased") or [] if r.get("estimate")],
        "firmwareCompiled": fw.get("compiled"),
        "firmwareTests": f"{fw.get('tests_passed')}/{fw.get('tests_total')}" if fw.get("tests_total") is not None else None,
        "pcbDrcErrors": pcb.get("drc_errors"),
        "pcbErcErrors": pcb.get("erc_errors"),
        "pcbRouted": pcb.get("routed"),
        "digitalChecks": (digital or {}).get("summary"),
        "physicalChecksStillNeeded": phys,
        "physicalChecksSource": phys_src,
        "physicalTesting": "Not performed; see BUILD_GUIDE.md",
    }


# ------------------------------------------------------------------ stage entry


def run(ctx) -> dict:
    from gstudio.checks import assemble_build_json
    from gstudio.errors import StageError
    from gstudio.pipeline import now_iso

    out = Path(ctx.out)
    spec = ctx.spec.to_json_dict(True) if hasattr(ctx.spec, "to_json_dict") else dict(ctx.spec)
    slug = str(spec.get("slug") or "gadget")
    problems: list[str] = []

    ctx.progress(STAGE, "re-assembling build.json", 5)
    build = read_json(out / "build.json", {}) or {}
    regen = assemble_build_json(out)
    regen["tests"] = build.get("tests", regen.get("tests"))  # tests[] gets the digital rows added by checks
    reproducible = json.dumps(regen, sort_keys=True, default=str) == json.dumps(build, sort_keys=True, default=str)
    if not reproducible:
        problems.append("build.json differs from a fresh assembly of its fragments (a stage changed it after checks)")

    # a stdlib-only re-checker ships with every delivery (the benchmark ships a verify_delivery.py too)
    import shutil

    shutil.copyfile(Path(__file__).resolve().parent / "docs_gen" / "verify_delivery.py", out / "verify_delivery.py")

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
                   {"id": "build-json-reproducible", "title": "build.json re-assembles identically from its fragments",
                    "status": "passed" if reproducible else "failed", "qb": "QB-46"},
                   {"id": "verify-delivery", "title": "the shipped verify_delivery.py passes on the finished delivery",
                    "status": "passed" if verify_ok else "failed", "detail": vlast},
               ],
               "message": f"{len(files)} files, {total / 1e6:.1f} MB, export.zip {zbytes / 1e6:.1f} MB"}
    if vprobs or zprobs:
        raise StageError("package verification failed: " + "; ".join((vprobs + zprobs)[:5]), {"summary": summary})
    return summary
