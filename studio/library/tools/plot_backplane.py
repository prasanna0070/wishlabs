#!/usr/bin/env python3
"""Plot library/backplane-v1.json (top view) to a PNG for a visual check. Needs matplotlib (VM).
Usage: python library/tools/plot_backplane.py library/_out/backplane-v1.png
"""
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mp  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

bp = json.loads((Path(__file__).resolve().parents[1] / "backplane-v1.json").read_text())
out = Path(sys.argv[1] if len(sys.argv) > 1 else "backplane-v1.png")
out.parent.mkdir(parents=True, exist_ok=True)
W, H = bp["board"]["size"]
fig, ax = plt.subplots(figsize=(9, 11))
ax.add_patch(mp.Rectangle((0, 0), W, H, fc="#1f6f43", ec="k", lw=1.5, alpha=0.9))
slots = {s["id"]: s for s in bp["slots"]}
u1 = slots["U1"]
x0, x1, y0, y1 = u1["devkit_pcb_plan"]
ax.add_patch(mp.Rectangle((x0, y0), x1 - x0, y1 - y0, fc="none", ec="#222", lw=2, ls="--"))
ax.text((x0 + x1) / 2, y1 + 0.8, "U1 DevKitC-1 (elevated +11, USB ->)", ha="center", fontsize=8, color="w")
a = u1["antenna_plan"]
ax.add_patch(mp.Rectangle((a[0], a[2]), a[1] - a[0], a[3] - a[2], fc="#e9b44c", ec="#a66", alpha=0.6))
ax.text(a[0] + 0.3, a[3] + 0.5, "antenna", fontsize=7)
for sid in ("A1", "S1"):
    x0, x1, y0, y1 = slots[sid]["module_plan"]
    ax.add_patch(mp.Rectangle((x0, y0), x1 - x0, y1 - y0, fc="#3c64b4", ec="#123", alpha=0.35, ls="--"))
    ax.text((x0 + x1) / 2, (y0 + y1) / 2, f"{sid} module\n(elevated +11)", ha="center", va="center", fontsize=8, color="w")
col = {"port": "#f4f1ea", "core_socket": "#111", "module_socket": "#333", "power_in": "#aaa", "support": "#d9822b",
       "mounting_hole": "#ddd", "test_pad": "#ffd700"}
for p in bp["parts"]:
    c = p["courtyard"]
    ls = ":" if p["side"] == "bottom" else "-"
    ax.add_patch(mp.Rectangle((c[0], c[2]), c[1] - c[0], c[3] - c[2], fc=col.get(p["role"], "#999"), ec="k", lw=0.6, alpha=0.85, ls=ls))
    if p["role"] in ("port", "power_in", "module_socket", "mounting_hole", "test_pad") or p["ref"].startswith(("U", "F", "D")):
        ax.text((c[0] + c[1]) / 2, (c[2] + c[3]) / 2, p["ref"], ha="center", va="center", fontsize=5.5,
                color="k" if p["role"] in ("port", "mounting_hole", "test_pad", "support") else "w")
for s in bp["slots"]:
    for pin in s["pins"]:
        if pin.get("at") and s["kind"] == "port" and pin["pin"] == 1:
            ax.plot(*pin["at"], "s", ms=3, color="r")
ax.set_xlim(-8, W + 4)
ax.set_ylim(-3, H + 4)
ax.set_aspect("equal")
ax.set_title(f"backplane-v1 top view {W} x {H} mm  (red square = pin 1; dotted = bottom side)", fontsize=9)
ax.set_xlabel("x mm (+x = robot's left)")
ax.set_ylabel("y mm (+y = rear)")
fig.tight_layout()
fig.savefig(out, dpi=140)
print(out)
