"""Run the real PCB stage (gstudio.pcb.stage.build_pcb) for the fixture's electrical.json in a subprocess.

    python fx_pcb_run.py <electrical.json> <pcb_dir> <summary.json> <name> <slug>
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def main() -> None:
    from gstudio.pcb import backplane_path, module_footprints
    from gstudio.pcb.stage import build_pcb
    from gstudio.version import ENGINE_VERSION

    el = json.loads(Path(sys.argv[1]).read_text())
    meta = {"name": sys.argv[4], "slug": sys.argv[5], "revision": "v1", "date": time.strftime("%Y-%m-%d"),
            "engine_version": ENGINE_VERSION}
    t0 = time.time()
    s = build_pcb(backplane_path(None), el, Path(sys.argv[2]), meta, {"fast": True, "route_timeout": 110}, None, module_footprints(None))
    s["fixture_seconds"] = round(time.time() - t0, 1)
    Path(sys.argv[3]).write_text(json.dumps(s, indent=1, default=str))


if __name__ == "__main__":
    main()
