"""Dev helper: normalize -> electrical -> cad -> (pcb render if present) -> manual for one example spec.

    PYTHONPATH=engine python engine/gstudio/manual/tests/run_real.py <example-slug> <out_dir> [--skip-cad]
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

STUDIO = Path(__file__).resolve().parents[4]


def main() -> None:
    slug, out = sys.argv[1], Path(sys.argv[2])
    from gstudio import electrical
    from gstudio.context import BuildContext

    t0 = time.time()
    ctx = BuildContext.prepare(STUDIO / "examples" / slug / "spec.json", out)
    (out / "spec.json").write_text(ctx.spec.dumps())
    electrical.run(ctx)
    if "--skip-cad" not in sys.argv:
        from gstudio import cad

        t = time.time()
        r = cad.run(ctx)
        print(f"cad done in {time.time() - t:.1f}s: {json.dumps(r, default=str)[:400]}", file=sys.stderr, flush=True)
    pcb_png = STUDIO / "examples" / slug / "_out" / "pcb" / "board-top.png"
    if pcb_png.is_file() and not (out / "pcb" / "board-top.png").is_file():
        (out / "pcb").mkdir(exist_ok=True)
        shutil.copy(pcb_png, out / "pcb" / "board-top.png")
    from gstudio import manual

    t = time.time()
    s = manual.run(ctx)
    print(f"manual done in {time.time() - t:.1f}s (total {time.time() - t0:.1f}s)", file=sys.stderr)
    print(json.dumps({k: v for k, v in s.items() if k not in ("images",)}, indent=1))


if __name__ == "__main__":
    main()
