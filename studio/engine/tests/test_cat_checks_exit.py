"""Hand-off c-to-a #1: a cable-exit part (soil probe, float switch) gets no harness-slack warning: its lead continues
outside the shell, so a long lead is by design. Route <= harness (QB-15) still applies."""

from __future__ import annotations

import json
from types import SimpleNamespace

from gstudio.checks import harness_length_checks


def _run(tmp_path, needs):
    (tmp_path / "cad").mkdir()
    (tmp_path / "cad" / "cable-routes.json").write_text(json.dumps([{"id": "R1", "to": "SOIL1.SIG", "route_length_mm": 160.0,
                                                                     "cable": "JST-PH 3p 300 mm"}]))
    electrical = {"instances": [{"ref": "SOIL1", "module": "SOIL_X", "slot": "J_IO1", "harness": {"length_mm": 300}}]}
    rows = {"SOIL_X": {"id": "SOIL_X", "aperture": {"needs": needs}}}
    lib = SimpleNamespace(get=lambda mid: rows.get(mid, {}), ids=lambda: [], harness_rows=lambda: [{"length_mm": L} for L in (100, 150, 200, 300)])
    return {c["id"]: c for c in harness_length_checks(tmp_path, {"purchased": []}, electrical, lib)}


def test_cable_exit_lead_is_not_slack(tmp_path):
    cs = _run(tmp_path, "cable_exit")
    assert cs["harness-slack"]["status"] == "passed" and "cable exit" in cs["harness-slack"]["detail"]
    assert cs["cable-length"]["status"] == "passed"


def test_other_parts_keep_the_slack_warning(tmp_path):
    cs = _run(tmp_path, "window")
    assert cs["harness-slack"]["status"] == "warning"
