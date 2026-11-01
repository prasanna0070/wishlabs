"""Manual summary for digital checks, computed from the files on disk at the end of the stage (QB-45):
images rendered, referenced vs orphan, missing references, label-layout violations, empty-parts steps,
and whether the guide's final checks match tests[] one to one.
"""

from __future__ import annotations

from pathlib import Path

from .guide import guide_final_titles, guide_images

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".mp4", ".webm"}


def referenced_paths(build: dict, guide_md: str) -> dict[str, set[str]]:
    refs: dict[str, set[str]] = {"build": set(), "guide": set()}
    for s in build.get("steps") or []:
        for v in s.get("visuals") or []:
            refs["build"].add(v.get("path"))
    for s in (build.get("wiring") or {}).get("steps") or []:
        for v in s.get("visuals") or []:
            refs["build"].add(v.get("path"))
    refs["guide"] = {p for p in guide_images(guide_md) if p.startswith("manual/")}
    return refs


def audit(out: Path, build: dict, guide_md: str, figure_results: list, seconds: float) -> dict:
    out = Path(out)
    mdir = out / "manual"
    files = sorted(p.relative_to(out).as_posix() for p in mdir.iterdir() if p.suffix.lower() in IMG_EXT) if mdir.is_dir() else []
    refs = referenced_paths(build, guide_md)
    all_refs = refs["build"] | refs["guide"]
    orphans = [f for f in files if f not in all_refs]
    missing = sorted(r for r in all_refs if r and not (out / r).is_file())
    not_in_guide = sorted(r for r in refs["build"] if r not in refs["guide"])
    steps = build.get("steps") or []
    empty = [s["id"] for s in steps if not s.get("parts")]
    no_use = [s["id"] for s in steps if not str(s.get("instruction", "")).startswith("**Use:**") or "**Tools:**" not in str(s.get("instruction", ""))]
    tests = [t["title"] for t in build.get("tests") or []]
    finals = guide_final_titles(guide_md)
    violations = [f"{r.file}: {v}" for r in figure_results for v in r.violations]
    failed = [{"file": r.file, "error": r.error} for r in figure_results if not r.ok]
    dropped = [f"{r.file}: {d}" for r in figure_results for d in r.dropped]
    return {
        "schema": "gadget-manual-summary/1",
        "images_rendered": len(files),
        "images": files,
        "images_referenced": len([f for f in files if f in all_refs]),
        "orphans": orphans,
        "missing_references": missing,
        "build_visuals_not_in_guide": not_in_guide,
        "labels_placed": sum(r.labels for r in figure_results),
        "label_overlaps": len(violations),
        "label_violations": violations,
        "labels_dropped": dropped,
        "figures_failed": failed,
        "steps": len(steps),
        "steps_with_empty_parts": empty,
        "steps_missing_use_or_tools": no_use,
        "wiring_steps": len((build.get("wiring") or {}).get("steps") or []),
        "wiring_connections": len((build.get("wiring") or {}).get("connections") or []),
        "tests": len(tests),
        "final_checks_match_tests": finals == tests,
        "seconds": round(seconds, 1),
        "ok": not orphans and not missing and not violations and not empty and not failed and finals == tests and not no_use,
        "limits": ["Counts are computed from the files written by this stage; they say nothing about physical assembly."],
    }
