"""Dev helper (VM): python engine/gstudio/cad/tests/run_example.py <example> [hero|all] -> runs gstudio.cad.run(ctx) and prints checks."""
import sys, time, json
sys.path.insert(0, "engine")
from pathlib import Path
from gstudio.context import BuildContext
from gstudio.cad import run
n = sys.argv[1]
views = sys.argv[2] if len(sys.argv) > 2 else "all"
t = time.time()
ctx = BuildContext.prepare(f"examples/{n}/spec.json", Path(f"examples/{n}/_out").resolve(), options={"cad_views": views})
s = run(ctx)
print(n, "RUN", round(time.time() - t, 1), json.dumps(s["timings"]))
print("   warnings:", s["warnings"])
for c in s["checks"]:
    print("   ", c["status"].upper(), c["id"], "|", c["detail"][:230])
print("   print:", {k: v["orientation_note"][:50] for k, v in s["print"].items()})
