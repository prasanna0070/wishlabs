"""A printed object's feet need a base to sit on (2026-11-21, live bench).

The live shark teapot (a blob body with a small rounded base and body.feet = 4) passed the design check, then the CAD
found "room for 0 of 4 feet on the base" and placed none while the parts list still bought four, so the build failed
at the manual (MAJ-14: purchased rows used in no assembly step). normalize_printed now refuses that design with a fix
the agent can follow (feet 0, or a wider base), using the CAD's own rule: a spot (seat radius + 2 mm) inside the base.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gstudio.library import Library  # noqa: E402
from gstudio.normalize import normalize  # noqa: E402

STUDIO = Path(__file__).resolve().parents[2]
SHARK = Path(__file__).resolve().parent / "fixtures" / "printed-shark-small-base.json"
EXAMPLES = sorted((STUDIO / "library" / "packs" / "examples").glob("printed*/*/spec.json"))


@pytest.fixture(scope="module")
def lib():
    return Library()


@pytest.fixture()
def printed_on(monkeypatch):
    monkeypatch.setenv("GS_PACKS_ENABLED", "printed,printed_cuts")


def _codes(rep: dict) -> set:
    return {e.get("code") for e in rep["errors"]}


def test_small_base_with_feet_is_refused_with_a_fix(printed_on, lib):
    spec = json.loads(SHARK.read_text())
    assert spec["body"]["feet"] == 4
    t0 = time.monotonic()
    _, rep = normalize(spec, lib)
    took = time.monotonic() - t0
    hit = [e for e in rep["errors"] if e.get("code") == "feet_room"]
    assert hit, rep["errors"]
    assert hit[0]["path"] == "body.feet" and "set body.feet to 0" in hit[0].get("fix", "")
    assert took < 10.0, f"validation took {took:.1f} s"


def test_the_same_design_without_feet_passes(printed_on, lib):
    spec = json.loads(SHARK.read_text())
    spec["body"]["feet"] = 0
    _, rep = normalize(spec, lib)
    assert "feet_room" not in _codes(rep), rep["errors"]
    assert rep["ok"], rep["errors"]


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.parent.name)
def test_printed_examples_keep_their_feet(printed_on, lib, path):
    spec = json.loads(path.read_text())
    _, rep = normalize(spec, lib)
    assert "feet_room" not in _codes(rep), rep["errors"]
    assert rep["ok"], rep["errors"]
