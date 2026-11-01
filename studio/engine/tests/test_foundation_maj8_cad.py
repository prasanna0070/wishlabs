"""REVIEW-1 MAJ-8 acceptance (slow, VM only): every seeded random layout that passes the validator (normalize with kit
footprints) also passes the CAD collision check. Runs the real CAD stage, so it is skipped unless GS_SLOW_CAD=1.

    GS_SLOW_CAD=1 GS_CAD_OUT=../_out/r1-foundation/maj8 python -m pytest -q tests/test_foundation_maj8_cad.py -s
"""

import copy
import json
import os
import random
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
N_LAYOUTS = int(os.environ.get("GS_CAD_LAYOUTS", "20"))
pytestmark = pytest.mark.skipif(os.environ.get("GS_SLOW_CAD") != "1", reason="slow: runs the CAD stage 20 times (set GS_SLOW_CAD=1)")


def layouts(n: int) -> list[tuple[int, dict]]:
    from gstudio.library import Library
    from gstudio.normalize import normalize

    lib = Library()
    base = json.loads((ROOT / "examples" / "bedtime-robot" / "spec.json").read_text())
    out, seed = [], 0
    while len(out) < n and seed < 40 * n:
        rnd = random.Random(seed)
        d = copy.deepcopy(base)
        for f in d["features"]:
            if f["face"] == "front":
                f["at"] = [round(f["at"][0] + rnd.uniform(-14, 14), 1), round(f["at"][1] + rnd.uniform(-14, 14), 1)]
        sp, rep = normalize(d, lib)
        if rep["ok"]:
            d["features"] = [f.model_dump(mode="json", exclude_none=True) for f in sp.features]  # keep the validator's moves
            out.append((seed, d))
        seed += 1
    return out


def run_cad(seed: int, spec: dict, base: Path) -> dict:
    out = base / f"seed-{seed:03d}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "input-spec.json").write_text(json.dumps(spec, indent=1))
    p = subprocess.run([sys.executable, "-m", "gstudio", "build", str(out / "input-spec.json"), str(out / "build"), "--only", "cad",
                        "--no-auto-repair"], cwd=ROOT / "engine", capture_output=True, text=True, timeout=1200)
    dc = out / "build" / "cad" / "digital-checks.json"
    if not dc.exists():
        return {"seed": seed, "status": "cad_failed", "stderr": p.stderr[-600:]}
    checks = {c["id"]: c for c in json.loads(dc.read_text())["checks"]}
    col = checks.get("collisions", {})
    return {"seed": seed, "status": col.get("status"), "detail": col.get("detail"),
            "pairs": [x["parts"] for x in (col.get("data") or {}).get("intersections", [])],
            "clearance": checks.get("feature_module_clearance", {}).get("status"),
            "at": {f["id"]: f["at"] for f in spec["features"]}}


def test_validated_layouts_pass_cad_collisions():
    base = Path(os.environ.get("GS_CAD_OUT", str(ROOT / "_out" / "r1-foundation" / "maj8")))
    lays = layouts(N_LAYOUTS)
    assert len(lays) == N_LAYOUTS, f"only {len(lays)} random layouts passed the validator"
    with ThreadPoolExecutor(max_workers=int(os.environ.get("GS_CAD_WORKERS", "4"))) as ex:
        res = list(ex.map(lambda sd: run_cad(sd[0], sd[1], base), lays))
    (base / "summary.json").write_text(json.dumps(res, indent=1))
    bad = [r for r in res if r["status"] != "passed"]
    for r in res:
        print(r["seed"], r["status"], r.get("detail"), r.get("pairs"))
    assert not bad, json.dumps(bad, indent=1)
