"""Catalog hand-off d-to-a #3: a wall design gets a "Hang it on the wall" step whose numbers all come from the CAD's mount
record (cad-summary info.split_info.mount, written by package D's rear cover); desk designs get no such step.
Runs on the minibot fixture (CadQuery builds it, so this runs on the VM)."""

from __future__ import annotations

import json
import re

from gstudio.manual.data import load
from gstudio.manual.model import classify
from gstudio.manual.steps import plan

MOUNT = {"kind": "wall",
         "keyholes": [{"entry": [-30.0, 159.5], "hang": [-30.0, 169.5]}, {"entry": [30.0, 159.5], "hang": [30.0, 169.5]}],
         "spacing_mm": 60.0, "screw_standoff_mm": 2.8,
         "keyhole_mm": {"head_d": 9.0, "slot_w": 4.5, "slot_len": 10.0, "recess_w": 9.6, "recess_d": 3.2},
         "plug_notch": {"width_mm": 13.6, "receptacle_depth_mm": 7.89, "from_z": 12.0, "to_z": 0.0}}


def _with_mount(ctx, mount):
    p = ctx.out / "cad" / "cad-summary.json"
    d = json.loads(p.read_text()) if p.is_file() else {}
    d.setdefault("info", {}).setdefault("split_info", {})["mount"] = mount
    p.write_text(json.dumps(d))


def test_desk_design_has_no_hang_step(minibot_ctx):
    steps = plan(classify(load(minibot_ctx)))
    assert "hang-on-wall" not in [s.id for s in steps]


def test_wall_design_hangs_with_numbers_from_the_cad(minibot_ctx):
    _with_mount(minibot_ctx, MOUNT)
    steps = plan(classify(load(minibot_ctx)))
    ids = [s.id for s in steps]
    assert "hang-on-wall" in ids
    assert ids.index("hang-on-wall") > ids.index("close-cover") and ids.index("hang-on-wall") < ids.index("final-checks")
    st = steps[ids.index("hang-on-wall")]
    text = st.instruction + " " + st.expected
    for want in ("60 mm apart", "169.5 mm below the marks", "2.8 mm off the wall", "lower it 10 mm", "right-angle",
                 "not modelled", "Physical check still needed", "Plug fit in the notch is not modelled"):
        assert want in text, want
    assert not re.search(r"\bkg\b|holds? up to|rated", text)  # no load rating is ever promised
    assert st.title == "Hang it on the wall" and st.parts
