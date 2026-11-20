"""Catalog A-hooks: a library row's `manual` block names its part in the guide (words.py), and the CAD params carry the
spec's mount block (cad/params.py mount_params). The first tests are pure Python; the last one runs the manual stage on
the minibot fixture without rendering (CadQuery needed for the fixture, so it runs on the VM)."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from gstudio.manual import words as W

FLOAT_WORDS = {"word": "Float switch", "noun": "float switch", "part_kind": "component", "mount_type": "cable exit",
               "face_part": "float", "selftest": "the water state changes when you lift the float",
               "care": "Keep the float switch and its lead outside the shell; water never goes inside."}


@pytest.fixture(autouse=True)
def _clean_words():
    W.activate({})
    yield
    W.activate({})


def test_block_normalizes_and_keeps_the_output_contract():
    assert W.block({"id": "X"}) == {}
    assert W.block({"manual": {"noun": ""}}) == {}
    b = W.block({"manual": {"noun": "soil probe", "part_kind": "sensor"}})
    assert b["word"] == "soil probe" and b["noun"] == "soil probe"
    assert b["part_kind"] == "board"  # "sensor" is not one of the wiring contract's four kinds
    b = W.block({"manual": {"word": "Servo  arm", "part_kind": "Component"}})
    assert b == {"word": "servo arm", "noun": "servo arm", "part_kind": "component"}
    assert W.row_role({"role": "Soil_Moisture", "manual": {"word": "soil probe"}}) == "soil_moisture"
    assert W.row_role({"role": "button"}) is None


def test_role_tables_answer_from_the_active_words_only():
    from gstudio.manual.model import ROLE_NOUN, ROLE_WORDS
    from gstudio.manual.steps import FACE_PART, SELFTEST_CHECK
    from gstudio.manual.wiring import PART_KIND

    assert ROLE_NOUN.get("level_switch", "level_switch") == "level_switch"
    assert "level_switch" not in FACE_PART
    W.activate({"level_switch": W.block({"manual": FLOAT_WORDS})})
    assert ROLE_NOUN.get("level_switch", "x") == "float switch" and ROLE_NOUN["level_switch"] == "float switch"
    assert ROLE_WORDS.get("level_switch", []) == ["float switch"]
    assert "level_switch" in FACE_PART and FACE_PART.get("level_switch", "front") == "float"
    assert SELFTEST_CHECK.get("level_switch", "") == FLOAT_WORDS["selftest"]
    assert PART_KIND.get("level_switch", "board") == "component"
    # built-in roles keep their words; iteration never sees pack roles (fuzzy matching stays built-in)
    assert ROLE_NOUN.get("display") == "display" and FACE_PART.get("speaker") == "cone"
    assert "level_switch" not in dict(ROLE_WORDS.items()) and "level_switch" not in list(ROLE_NOUN)
    W.activate({})
    assert ROLE_NOUN.get("level_switch", "level_switch") == "level_switch"


def test_learn_is_scoped_to_the_rows_a_design_uses():
    rows = {"FLOAT_X": {"id": "FLOAT_X", "role": "level_switch", "manual": FLOAT_WORDS},
            "SOIL_X": {"id": "SOIL_X", "role": "soil_moisture", "manual": {"word": "soil probe"}}}
    lib = SimpleNamespace(get=lambda mid: rows.get(mid, {}))
    data = SimpleNamespace(instances=[{"ref": "LVL1", "module": "FLOAT_X"}], comps={}, lib=lib)
    vocab = W.learn(data)
    assert set(vocab) == {"level_switch"}  # the soil row is in the library but not in this design
    assert W.word("level_switch", "noun") == "float switch" and W.word("soil_moisture", "noun") is None


def test_mount_kind_reads_the_row_words_first():
    from gstudio.manual.steps import mount_kind

    assert mount_kind({"mount": {"type": "holes"}}) == "screws"
    assert mount_kind({"mount": {"type": "holes"}, "manual": {"word": "probe", "mount_type": "cable exit"}}) == "cable_exit"
    assert mount_kind({"mount": {"type": "adhesive"}, "manual": {"word": "x", "mount_type": "panel_snap"}}) == "snap_out"
    assert mount_kind({"manual": {"word": "x", "mount_type": "screws"}}) == "screws"
    assert mount_kind({"manual": {"word": "x", "mount_type": "held by two screw bosses"}}) == "screws"


def test_role_of_uses_the_row_role_not_word_guessing():
    from gstudio.manual.data import Comp
    from gstudio.manual.model import role_of

    rows = {"FLOAT_X": {"id": "FLOAT_X", "role": "level_switch", "manual": FLOAT_WORDS},
            "BTN_X": {"id": "BTN_X", "role": "level_switch"}}
    lib = SimpleNamespace(get=lambda mid: rows.get(mid, {}))
    data = SimpleNamespace(lib=lib, instance=lambda ref: {"LVL1": {"module": "FLOAT_X"}, "BTN9": {"module": "BTN_X"}}.get(ref, {}))
    c = Comp(name="PURCHASE_FloatSwitch", kind="PURCHASE", label="Float switch", ref="LVL1", module_id="FLOAT_X")
    assert role_of(c, data) == "level_switch"
    c2 = Comp(name="PURCHASE_Thing", kind="PURCHASE", label="Thing", ref="LVL1", module_id=None)
    assert role_of(c2, data) == "level_switch"  # no module id on the CAD part: the instance's row decides
    c3 = Comp(name="PURCHASE_Other", kind="PURCHASE", label="Other", ref="BTN9", module_id="BTN_X")
    assert role_of(c3, data) == "button"  # no manual block: the old prefix/word rules apply ("switch" -> button)


def test_mount_params():
    from gstudio.cad.params import mount_params

    assert mount_params({}) == {"kind": "desk"}
    assert mount_params({"mount": None}) == {"kind": "desk"}
    assert mount_params({"mount": {"kind": "wall", "screws": 2}}) == {"kind": "wall", "screws": 2}
    assert mount_params({"mount": {"screws": 3}}) == {"kind": "desk", "screws": 3}

    class M:
        def model_dump(self, mode="python"):
            return {"kind": "ceiling", "screws": 3}

    assert mount_params({"mount": M()}) == {"kind": "ceiling", "screws": 3}


# ------------------------------------------------------------------------------------------------- fixture (VM)
def test_row_word_appears_in_the_guide(minibot_ctx):
    """The minibot's button row turned into a pack-style float switch: its manual word titles its step, names it in the
    wiring step, its self-test line and its care line reach BUILD_GUIDE.md (manual stage, no rendering)."""
    from gstudio.manual import run

    ctx = minibot_ctx
    row = dict(ctx.lib.mods["btn-16mm"])
    row.update({"role": "level_switch", "manual": dict(FLOAT_WORDS, mount_type="holes")})
    ctx.lib.mods["btn-16mm"] = row
    ctx.options["manual_no_render"] = True
    ctx.options["manual_lenient"] = True
    run(ctx)
    md = (ctx.out / "BUILD_GUIDE.md").read_text()
    build = json.loads((ctx.out / "build.json").read_text())
    titles = [s["title"] for s in build["steps"]]
    assert any(t.startswith("Fit the float switch") for t in titles), titles
    assert "float switch" in md.lower()
    assert FLOAT_WORDS["care"] in md
    assert FLOAT_WORDS["selftest"].lower() in md.lower()  # the guide capitalizes each self-test bullet
    kinds = {p["id"]: p["kind"] for p in build["wiring"]["parts"]}
    assert kinds.get("BTN1") == "component"
    wiring_text = " ".join(f"{s['title']} {s.get('text', '')}" for s in build["wiring"]["steps"])
    assert re.search(r"float switch", wiring_text, re.I), wiring_text[:400]
