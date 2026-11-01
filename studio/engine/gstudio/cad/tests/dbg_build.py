"""Dev helper (VM): python engine/gstudio/cad/tests/dbg_build.py <spec.json> <out_dir> -> full CAD stage with a full traceback."""
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from gstudio.cad import run  # noqa: E402
from gstudio.context import BuildContext  # noqa: E402

spec, out = sys.argv[1], Path(sys.argv[2]).resolve()
opts = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
t = time.time()
ctx = BuildContext.prepare(spec, out, options=opts)
try:
    from gstudio.cad.build import build
    from gstudio.cad.params import build_params

    if opts.get("geometry_only"):
        p = build_params(ctx.spec, ctx.lib, getattr(ctx, "electrical", None))
        m = build(p)
        print("geometry ok", round(time.time() - t, 1), m.warnings)
        sys.exit(0)
    s = run(ctx)
except Exception:
    traceback.print_exc()
    sys.exit(1)
print("RUN", round(time.time() - t, 1), json.dumps(s["timings"]))
print("warnings:", s["warnings"])
for c in s["checks"]:
    print(" ", c["status"].upper(), c["id"], "|", c["detail"][:260])
