"""Heavy: the designs refused live / on the bench for a tiny shell clash build clean through the real CAD stage.

p_h77zjgp6fekk (speaker cup in the plate + plate corners), p_esnfk7mb466z (plate corners): PRINT_Shell /
PRINT_BasePlate 1.61 / 0.05 mm3 live 2026-11-21. p_a4avhj6v4pth: PRINT_Shell / PRINT_LedDiffuser 0.13 mm3 (the flange
under a rounded top edge). Each is built once per session in a worker process (packs on, as live).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

STUDIO = Path(__file__).resolve().parents[4]
FIX = Path(__file__).parent / "fixtures"
PACKS = "sensing,schedule,printed,printed_cuts,freeform"
SPECS = ["clinic-organizer-dock-p_h77zjgp6fekk.json", "step-counter-console-p_esnfk7mb466z.json",
         "lobby-stand-desk-led-p_a4avhj6v4pth.json"]
pytest.importorskip("cadquery")


def _build(args) -> dict:
    name, root = args
    os.environ["GS_PACKS_ENABLED"] = PACKS
    sys.path.insert(0, str(STUDIO / "engine"))
    from gstudio.cad import run
    from gstudio.context import BuildContext

    out = Path(root) / name.replace(".json", "")
    ctx = BuildContext.prepare(FIX / name, out, options={"cad_renders": False})
    run(ctx)
    return {"checks": json.loads((out / "cad" / "digital-checks.json").read_text())["checks"],
            "info": json.loads((out / "cad" / "cad-summary.json").read_text()).get("info", {})}


@pytest.fixture(scope="session")
def seam_built():
    root = tempfile.mkdtemp(prefix="gs-seam-")
    with ProcessPoolExecutor(max_workers=3) as ex:
        return dict(zip(SPECS, ex.map(_build, [(n, root) for n in SPECS])))


@pytest.mark.parametrize("name", SPECS)
def test_refused_design_has_no_shell_clash(seam_built, name):
    col = next(c for c in seam_built[name]["checks"] if c["id"] == "collisions")
    hits = col["data"]["intersections"]
    assert col["status"] == "passed", hits
    assert not any("PRINT_Shell" in h["parts"] for h in hits)


def test_the_speaker_cup_was_trimmed_not_ignored(seam_built):
    trims = seam_built["clinic-organizer-dock-p_h77zjgp6fekk.json"]["info"].get("kit_adds_trimmed_at_plate") or []
    assert [t["feature"] for t in trims if t["trimmed"]] == ["speaker"]
