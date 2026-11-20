"""Compile the firmware for every examples/<slug>/spec.json through the real normalize + electrical stages,
plus every library/packs/examples/<pack>/<slug>/spec.json of the packs that are on (so the builder image's warm-up,
builder/Dockerfile, also installs the pinned libraries a pack uses), plus the firmware fixtures for templates that
have no example yet. Run on the VM from engine/:

    python -m gstudio.firmware.tests.build_examples <out_root> [--stories-audio] [--only slug,...]

Prints one JSON line per design and writes <out_root>/<slug>/ (electrical.json, firmware/, summary). A pack example
is named pack-<pack>-<slug>; --only accepts that name or the bare slug.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from gstudio.context import BuildContext
from gstudio.electrical import build_electrical
from gstudio.errors import SpecError, StageError
from gstudio.firmware import run

from .conftest import FIX, example_specs, load_fixture


def pack_example_specs() -> list[Path]:
    """Example specs of the packs that are on (library/packs/enabled.json, GS_PACKS_ENABLED, GS_PACKS_OFF)."""
    try:
        from gstudio import packs
    except ImportError:  # a checkout without the pack mechanism (CATALOG-PLAN 0.5) has no pack examples
        return []
    return list(packs.example_specs())


class Ctx:
    def __init__(self, spec, lib, electrical, out, options):
        self.spec, self.lib, self.electrical, self.out, self.options = spec, lib, electrical, Path(out), options
        self.artifacts, self.warnings = {}, []

    def progress(self, stage, msg, pct=None):
        pass

    def warn(self, stage, msg):
        self.warnings.append(msg)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--stories-audio", action="store_true")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    only = {s for s in a.only.split(",") if s}
    root = Path(a.out)
    jobs = []
    seen_templates = set()
    for sp in example_specs():
        jobs.append(("example", sp.parent.name, sp))
        try:
            seen_templates.add(json.loads(sp.read_text())["behaviour"]["template"])
        except Exception:
            pass
    for sp in pack_example_specs():
        jobs.append(("example", f"pack-{sp.parent.parent.name}-{sp.parent.name}", sp))
    for t in ("story_player", "desk_companion", "notifier_clock", "ambient_light", "sensor_display"):
        if t not in seen_templates:
            jobs.append(("fixture", f"fixture-{t}", t))
    rc = 0
    for kind, slug, src in jobs:
        if only and slug not in only and not (isinstance(src, Path) and src.parent.name in only):
            continue
        out = root / slug
        t0 = time.monotonic()
        try:
            if kind == "example":
                ctx0 = BuildContext.prepare(src, out)
                elec = build_electrical(ctx0.spec, ctx0.lib)
                spec, lib = ctx0.spec, ctx0.lib
            else:
                spec, elec = load_fixture(src)
                lib = None
            out.mkdir(parents=True, exist_ok=True)
            (out / "electrical.json").write_text(json.dumps(elec, indent=1, default=str))
            s = run(Ctx(spec, lib, elec, out, {"stories_audio": a.stories_audio}))
            (out / "firmware-summary.json").write_text(json.dumps(s, indent=1, default=str))
            line = {"design": slug, "source": kind, "template": s["template"], "compiled": s["compiled"],
                    "tests": f"{s['tests_passed']}/{s['tests_total']}", "app_bytes": s["size"].get("app_bin_bytes"),
                    "flash_pct": (s["size"].get("flash") or {}).get("percent"), "ram_pct": (s["size"].get("ram") or {}).get("percent"),
                    "sha256": s["sha256"], "seconds": round(time.monotonic() - t0, 1), "warnings": s["warnings"]}
        except (SpecError, StageError) as e:
            rc = 1
            line = {"design": slug, "source": kind, "error": str(e)[:400], "details": getattr(e, "details", None)}
        print(json.dumps(line, default=str), flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
