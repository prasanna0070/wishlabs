"""Manual summary for digital checks, computed from the files on disk (QB-45): images (PNG and SVG counted apart,
MAJ-19), referenced vs orphan, missing references, label-layout violations, dropped priority labels (MIN-31), wiring
diagram overlaps (MIN-30), empty-parts steps, purchased rows used in no step (MAJ-14), harnesses without a module-end
instruction (MAJ-10), and whether the guide's final checks list every tests[] record (MAJ-19).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Optional

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


def figure_fields(figure_results: list) -> dict:
    """The parts of the summary that only the render pass knows (kept by finalize)."""
    return {
        "labels_placed": sum(r.labels for r in figure_results),
        "label_overlaps": sum(len(r.violations) for r in figure_results),
        "label_violations": [f"{r.file}: {v}" for r in figure_results for v in r.violations],
        "labels_dropped": [f"{r.file}: {d}" for r in figure_results for d in r.dropped],
        "labels_dropped_priority": [f"{r.file}: {d}" for r in figure_results for d in getattr(r, "dropped_priority", [])],
        "figures_failed": [{"file": r.file, "error": r.error} for r in figure_results if not r.ok],
        "figure_seconds": {r.file: round(r.seconds, 2) for r in figure_results},
    }


def purchased_unused(build: dict, comps: Optional[dict] = None) -> list[str]:
    """Purchased rows with quantity > 0 that no step (other than the parts check) uses (MAJ-14). A row is used when a
    step's parts[] names its modelPart or module_id, or a component that carries its module_id or one of its refs."""
    used = {p.split("#", 1)[0] for s in build.get("steps") or [] if s.get("id") != "check-parts"
            for p in [*(s.get("parts") or []), *(s.get("purchased") or [])]}
    comps = comps or {}
    used_mods = {getattr(comps[n], "module_id", None) for n in used if n in comps}
    used_refs = {getattr(comps[n], "ref", None) for n in used if n in comps}
    out = []
    for p in build.get("purchased") or []:
        if not int(p.get("quantity") or 0):
            continue
        if p.get("modelPart") in used or p.get("module_id") in used or (p.get("module_id") and p.get("module_id") in used_mods):
            continue
        if p.get("kind") == "module" and set(p.get("refs") or []) & (used_refs - {None}):
            continue
        out.append(str(p.get("name") or p.get("module_id")))
    return out


def audit(out: Path, build: dict, guide_md: str, fig: dict, seconds: Optional[float], extra: Optional[dict] = None) -> dict:
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
    titles = [s.get("title") for s in steps]
    repeated = sorted(t for t, k in Counter(titles).items() if k > 1)
    tests = [t["title"] for t in build.get("tests") or []]
    finals = guide_final_titles(guide_md)
    match = sorted(finals) == sorted(tests) and len(finals) == len(tests)
    extra = dict(extra or {})
    unused = purchased_unused(build, extra.pop("_comps", None))
    png = [f for f in files if f.endswith(".png")]
    svg = [f for f in files if f.endswith(".svg")]
    s = {
        "schema": "gadget-manual-summary/2",
        "images_rendered": len(png),
        "images_png": len(png),
        "images_svg": len(svg),
        "images": files,
        "images_referenced": len([f for f in files if f in all_refs]),
        "orphans": orphans,
        "missing_references": missing,
        "build_visuals_not_in_guide": not_in_guide,
        **{k: v for k, v in fig.items()},
        "steps": len(steps),
        "steps_with_empty_parts": empty,
        "steps_missing_use_or_tools": no_use,
        "repeated_step_titles": repeated,
        "purchased_rows_unused": unused,
        "wiring_steps": len((build.get("wiring") or {}).get("steps") or []),
        "wiring_connections": len((build.get("wiring") or {}).get("connections") or []),
        "tests": len(tests),
        "tests_failed": sum(1 for t in build.get("tests") or [] if t.get("status") == "failed"),
        "final_checks_match_tests": match,
        **extra,
        "limits": ["Counts are computed from the files written by this stage; they say nothing about physical assembly."],
    }
    if seconds is not None:
        s["seconds"] = round(seconds, 1)
    s["ok"] = (not orphans and not missing and not s.get("label_violations") and not empty and not s.get("figures_failed")
               and match and not no_use and not repeated and not unused and not s.get("labels_dropped_priority")
               and not s.get("wiring_overlaps") and not s.get("harness_module_end_missing"))
    return s


def checks_from(summary: dict) -> list[dict]:
    """The manual's own digital checks for the checks stage (normalize_checks format)."""
    def c(cid, title, ok, detail, qb=None):
        d = {"id": cid, "title": title, "status": "passed" if ok else "failed", "detail": detail, "artifact": "manual/manual-summary.json"}
        if qb:
            d["qb"] = qb
        return d

    return [
        c("manual-purchased-rows-used", "every purchased row is used in an assembly step", not summary.get("purchased_rows_unused"),
          ", ".join(summary.get("purchased_rows_unused") or []) or "all rows used", "QB-29"),
        c("manual-module-ends", "every harness has a module-end instruction", not summary.get("harness_module_end_missing"),
          ", ".join(summary.get("harness_module_end_missing") or []) or "all harnesses covered", "QB-17"),
        c("manual-wiring-overlaps", "the wiring diagram has no overlapping text and no wire through text", not summary.get("wiring_overlaps"),
          f"{len(summary.get('wiring_overlaps') or [])} overlaps", "QB-33"),
        c("manual-priority-labels", "no figure dropped a priority label", not summary.get("labels_dropped_priority"),
          "; ".join(summary.get("labels_dropped_priority") or []) or "none dropped", "QB-32"),
        c("manual-step-titles", "no step title repeats", not summary.get("repeated_step_titles"),
          ", ".join(summary.get("repeated_step_titles") or []) or "all unique", "QB-29"),
    ]
