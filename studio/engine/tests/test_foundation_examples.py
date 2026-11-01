"""Foundation: the example specs (examples/<slug>/spec.json, created by cad) must load, normalize and wire up.

Skips an example that does not exist yet. Problems go to docs/REQUESTS.md addressed to cad (foundation never edits them).
Run `python tests/test_foundation_examples.py` to print a REQUESTS-ready report.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ["bedtime-robot", "cloud-lamp", "desk-buddy"]


def check(slug: str) -> list[str]:
    from gstudio.electrical import build_electrical
    from gstudio.errors import SpecError
    from gstudio.library import Library
    from gstudio.normalize import normalize
    from gstudio.spec import load_spec

    p = ROOT / "examples" / slug / "spec.json"
    try:
        sp = load_spec(p)
    except SpecError as e:
        return [f"schema: {m}" for m in e.messages()]
    lib = Library()
    sp, rep = normalize(sp, lib)
    probs = [f"normalize: {e['path']}: {e['msg']}" + (f" Fix: {e['fix']}" if e.get("fix") else "") for e in rep["errors"]]
    if not probs:
        el = build_electrical(sp, lib)
        probs += [f"electrical warning: {w}" for w in el["warnings"]]
    return probs


@pytest.mark.parametrize("slug", EXAMPLES)
def test_example(slug):
    if not (ROOT / "examples" / slug / "spec.json").exists():
        pytest.skip(f"examples/{slug}/spec.json not created yet")
    probs = [p for p in check(slug) if not p.startswith("electrical warning")]
    assert not probs, "\n".join(probs)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "engine"))
    out = {}
    for s in EXAMPLES:
        if (ROOT / "examples" / s / "spec.json").exists():
            out[s] = check(s)
        else:
            out[s] = ["missing"]
    print(json.dumps(out, indent=2))
