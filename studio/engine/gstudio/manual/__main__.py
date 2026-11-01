"""Standalone runner: python -m gstudio.manual <out_dir> [--no-render] [--workers N] [--cache DIR]

Reads <out_dir>/spec.json and electrical.json (and cad/, print/, pcb/, firmware/ if present) and runs the
manual stage on that output directory. Uses gstudio.library.Library when it imports, else library/*.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gstudio.manual")
    ap.add_argument("out")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--cache", default=None)
    a = ap.parse_args(argv)
    out = Path(a.out)
    try:
        from gstudio.library import Library  # type: ignore

        lib = Library()
    except Exception:
        lib = None
    spec = json.loads((out / "spec.json").read_text()) if (out / "spec.json").is_file() else {}
    elec = json.loads((out / "electrical.json").read_text()) if (out / "electrical.json").is_file() else None
    opts = {"manual_no_render": a.no_render}
    if a.workers:
        opts["manual_workers"] = a.workers
    if a.cache:
        opts["mesh_cache"] = a.cache

    def progress(stage, msg, pct=None):
        print(f"[{time.strftime('%H:%M:%S')}] {stage} {'' if pct is None else f'{pct:5.1f}% '}{msg}", file=sys.stderr, flush=True)

    ctx = SimpleNamespace(spec=spec, lib=lib, out=out, electrical=elec, artifacts={}, options=opts, progress=progress,
                          warn=lambda s, m: print(f"WARN {s}: {m}", file=sys.stderr))
    from . import run

    summary = run(ctx)
    print(json.dumps({k: v for k, v in summary.items() if k not in ("images", "figure_seconds")}, indent=2))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
