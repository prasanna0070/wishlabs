"""Fit bug regression (orchestrator-to-d.md, 2026-11-20): the live focus-lamp spec (p_r4yikwxy497q) through the CAD stage.

At 159a94a the CAD moved the core cartridge 80 mm forward out of the shell (board origin y -117) and the build failed
9 collision pairs (PRINT_Shell / backplane 871 mm3, shell / core board 341 mm3). Now the keep-out is checked from its
own bottom and the board stays at the rear seam cap (y -37), where normalize put it. See tests/test_cat_fit.py.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
pytest.importorskip("cadquery")

SPEC = Path(__file__).resolve().parent / "fixtures" / "focus-lamp-p_r4yikwxy497q.json"


@pytest.fixture(scope="module")
def summary(tmp_path_factory):
    from gstudio.cad import run
    from gstudio.context import BuildContext

    ctx = BuildContext.prepare(json.loads(SPEC.read_text()), tmp_path_factory.mktemp("fit_focus_lamp"),
                               options={"cad_renders": False})
    return run(ctx)


def test_cartridge_stays_inside_the_shell(summary):
    cart = summary["info"]["cartridge"]
    assert cart["board_origin"][1] == pytest.approx(-37.0, abs=0.5)
    assert not [w for w in summary["warnings"] if "cartridge keep-out does not fit" in w]


def test_no_shell_collision_with_the_cartridge(summary):
    col = next(c for c in summary["checks"] if c["id"] == "collisions")
    assert "PURCHASE_Backplane" not in col["detail"] and "PURCHASE_CoreBoard" not in col["detail"]
    assert col["status"] == "passed", col["detail"]
