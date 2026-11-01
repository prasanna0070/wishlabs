from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

FIX = Path(__file__).resolve().parent / "fixtures"
TEMPLATES = ("story_player", "desk_companion", "notifier_clock", "ambient_light", "sensor_display")
STUDIO = Path(__file__).resolve().parents[4]


def load_fixture(template: str) -> tuple[dict, dict]:
    spec = json.loads((FIX / f"spec-{template}.json").read_text())
    elec = json.loads((FIX / f"electrical-{template}.json").read_text())
    return spec, elec


@pytest.fixture(params=TEMPLATES)
def design(request):
    spec, elec = load_fixture(request.param)
    return request.param, copy.deepcopy(spec), copy.deepcopy(elec)


def example_specs() -> list[Path]:
    """Real example specs (examples/<slug>/spec.json) written by the foundation and cad agents."""
    return sorted((STUDIO / "examples").glob("*/spec.json"))


def real_design(spec_path: Path, tmp: Path):
    """Normalize the example with foundation's code and build its electrical.json with gstudio.electrical."""
    from gstudio.context import BuildContext
    from gstudio.electrical import build_electrical

    ctx = BuildContext.prepare(spec_path, tmp)
    return ctx, build_electrical(ctx.spec, ctx.lib)
