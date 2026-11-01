"""Dev helper: build one fixture board (full board uses the XH J_LED backplane) and print checks and silk skips.

    python -m gstudio.pcb.tests.fixtures.build_one <full|minimal> <out_dir> [fast] [live|xh|xh=<pin1 y>]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from gstudio.pcb import library_modules, module_footprints
from gstudio.pcb.stage import build_pcb
from gstudio.pcb.tests.test_units import FIX, BACKPLANE, META, xh_backplane

name, out = sys.argv[1], Path(sys.argv[2])
fast = len(sys.argv) > 3 and sys.argv[3] == "fast"
variant = sys.argv[4] if len(sys.argv) > 4 else ("xh" if name == "full" else "live")
el = json.loads((FIX / f"electrical.{name}.json").read_text())
bp = json.loads(BACKPLANE.read_text())
if variant.startswith("xh"):
    bp = xh_backplane(bp)
    if "=" in variant:
        y = float(variant.split("=")[1])
        H = float(bp["board"]["size"][1])
        for p in bp["parts"] + bp["slots"]:
            if p.get("id", p.get("ref")) == "J_LED":
                p["at"] = [p["at"][0], y]
                p["kicad"] = {"x": p["at"][0], "y": round(H - y, 3), "rot": 0}
t = time.time()
s = build_pcb(bp, el, out / "pcb", dict(META, slug=f"fixture-{name}"), {"fast": fast, "reuse": False}, None,
              module_footprints(), library_modules())
print(f"built in {time.time() - t:.1f} s")
for c in s["checks"]:
    print(f"{c['status']:8} {c['id']}: {c['detail'][:150]}")
print("silk skipped:", s.get("silk_skipped"))
print("silk:", s["silk"])
print("pcba:", json.dumps(s["pcba"]["inr_per_board"]))
print("deviations:", s["deviations"])
