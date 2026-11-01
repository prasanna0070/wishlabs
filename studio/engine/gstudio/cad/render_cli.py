"""Render views from a GLB in a separate process (no OCCT state, killable on timeout).

    python -m gstudio.cad.render_cli --glb cad/assembly.glb --out cad/renders --views hero[,rear_open,xray]
                                     [--screens screens.json] [--split rear_cover] [--size 1200]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[2]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--glb", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--views", default="hero")
    ap.add_argument("--screens", default=None)
    ap.add_argument("--split", default="rear_cover")
    ap.add_argument("--size", type=int, default=1200)
    ap.add_argument("--name", default=None, help="file name for a single view")
    a = ap.parse_args(argv)
    from . import render as R

    items = R.items_from_glb(a.glb)
    screens = json.loads(Path(a.screens).read_text()) if a.screens and Path(a.screens).exists() else None
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    fns = {"hero": R.hero, "rear_open": R.rear_open, "xray": R.xray}
    res = {}
    for v in a.views.split(","):
        fn = out / (a.name if a.name and "," not in a.views else f"{v}.png")
        fns[v](items, fn, (a.size, a.size), 2, split=a.split, screens=screens)
        res[v] = str(fn)
    print(json.dumps(res))
    return 0


def render_subprocess(glb: Path, out_dir: Path, views=("hero",), screens=None, split="rear_cover", size=1200,
                      timeout: float = 150.0, name: str = None) -> dict:
    """Run the renderer in a fresh interpreter with a hard timeout. Returns {view: path} or {"error": msg}."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sfile = None
    if screens:
        sfile = out_dir / ".screens.json"
        sfile.write_text(json.dumps(screens))
    cmd = [sys.executable, "-m", "gstudio.cad.render_cli", "--glb", str(glb), "--out", str(out_dir), "--views", ",".join(views),
           "--split", split, "--size", str(size)]
    if sfile:
        cmd += ["--screens", str(sfile)]
    if name:
        cmd += ["--name", name]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ENGINE) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, start_new_session=True)
    except subprocess.TimeoutExpired:
        return {"error": f"render timed out after {timeout:.0f} s"}
    finally:
        if sfile and sfile.exists():
            sfile.unlink()
    if r.returncode != 0:
        return {"error": (r.stderr or r.stdout)[-600:]}
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return {"error": "renderer printed no result"}


if __name__ == "__main__":
    sys.exit(main())
