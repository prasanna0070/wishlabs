"""python -m gstudio.pcb <electrical.json> <out_pcb_dir> [--backplane path] [--name N] [--slug S] [--fast]

Builds one backplane directly from an electrical.json (no spec/pipeline needed). Prints the summary JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import backplane_path, module_footprints
from .stage import build_pcb


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gstudio.pcb")
    ap.add_argument("electrical")
    ap.add_argument("out")
    ap.add_argument("--backplane", default=None)
    ap.add_argument("--name", default="Gadget")
    ap.add_argument("--slug", default="gadget")
    ap.add_argument("--revision", default="A")
    ap.add_argument("--date", default="2026-11-01")
    ap.add_argument("--fast", action="store_true")
    a = ap.parse_args(argv)
    from ..version import ENGINE_VERSION

    bpp = Path(a.backplane) if a.backplane else backplane_path()
    el = json.loads(Path(a.electrical).read_text())
    meta = {"name": a.name, "slug": a.slug, "revision": a.revision, "date": a.date, "engine_version": ENGINE_VERSION}

    def prog(msg, pct=None):
        print(f"[pcb] {'' if pct is None else f'{pct:5.1f}% '}{msg}", file=sys.stderr, flush=True)

    s = build_pcb(bpp, el, Path(a.out), meta, {"fast": a.fast}, prog, module_footprints())
    slim = {k: v for k, v in s.items() if k not in ("files",)}
    print(json.dumps(slim, indent=2))
    return 0 if s["drc_errors"] == 0 and s["erc_errors"] == 0 and s["routed"] else 1


if __name__ == "__main__":
    sys.exit(main())
