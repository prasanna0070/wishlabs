"""Hand-off b-to-a #3: a harnessed module with no CAD part (an extra module the CAD does not mount, e.g. an RTC on an I2C
port) gets its module-end instruction in the bench test, so the manual stage no longer fails MAJ-10 for it, and the
plug step says plainly that nothing holds it. Minibot fixture (CadQuery, VM)."""

from __future__ import annotations

import copy

from gstudio.manual.data import load
from gstudio.manual.model import classify
from gstudio.manual.stage import module_end_missing
from gstudio.manual.steps import plan


def test_module_without_cad_part_gets_its_cable_end(minibot_ctx):
    el = minibot_ctx.electrical
    disp = next(i for i in el["instances"] if i["ref"] == "DISP1")
    loose = copy.deepcopy(disp)
    loose.update({"ref": "RTC1", "slot": "J_I2C2", "feature": None, "name": "Loose I2C module (test)"})
    el["instances"].append(loose)
    data = load(minibot_ctx)
    assert data.comp_for_ref("RTC1") is None
    steps = plan(classify(data))
    bench = next(s for s in steps if s.id == "bench-test")
    assert "RTC1" in bench.module_end and "(RTC1)" in bench.body
    assert loose["module"] in bench.rows  # its purchased row is used in a step (MAJ-14), though it has no CAD part
    assert module_end_missing(data, steps) == []
    plug = next(s for s in steps if s.id == "plug-harnesses")
    assert "no printed mount in this design" in plug.body and "physical check" in plug.body
