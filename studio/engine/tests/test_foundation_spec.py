"""Foundation: gadget-spec/1 models, validation messages, schema."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _fixtures import good  # noqa: E402

from gstudio.errors import SpecError  # noqa: E402
from gstudio.spec import Spec, load_spec, polygon_area, spec_json_schema, validate_spec  # noqa: E402


def errs(d):
    with pytest.raises(SpecError) as ei:
        load_spec(d)
    return ei.value.errors


def test_good_spec_loads_and_round_trips():
    s = load_spec(good())
    assert s.body.family == "silhouette"
    d = s.to_json_dict()
    assert d["schema"] == "gadget-spec/1"
    s2 = load_spec(d)
    assert s2.to_json_dict() == d
    # json string and path inputs
    assert load_spec(json.dumps(d)).name == "Luna Owl"


def test_load_from_path(tmp_path):
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(good()))
    assert load_spec(p).slug == "luna-owl"
    assert load_spec(str(p)).slug == "luna-owl"
    with pytest.raises(SpecError):
        load_spec(tmp_path / "missing.json")


def test_outline_stored_counter_clockwise():
    d = good()
    d["body"]["outline"] = list(reversed(d["body"]["outline"]))
    s = load_spec(d)
    assert polygon_area(s.body.outline) > 0


def test_default_core():
    d = good()
    del d["electronics"]["core"]
    assert load_spec(d).electronics.core == "CORE_ESP32S3_DEVKITC1_N8R8"


def test_unknown_key_lists_allowed_keys():
    d = good()
    d["body"]["colour"] = "red"
    e = errs(d)
    assert e[0]["path"] == "body"
    assert "unknown key 'colour'" in e[0]["msg"]
    assert "wall" in e[0]["fix"] and "outline" in e[0]["fix"]


def test_bounds_and_literals_are_short_and_actionable():
    d = good()
    d["body"]["wall"] = 5
    d["decor"][1]["style"] = "wink"
    d["decor"][5]["text"] = "X" * 30
    e = errs(d)
    paths = {x["path"] for x in e}
    assert {"body.wall", "decor[1].style", "decor[5].text"} <= paths
    for x in e:
        assert len(x["msg"]) < 300


def test_self_intersecting_outline():
    d = good()
    d["body"]["outline"] = [[-60, 0], [60, 0], [-60, 100], [60, 100]]
    e = errs(d)
    assert any("crosses itself" in x["msg"] for x in e)


def test_flat_bottom_required():
    d = good()
    d["body"]["outline"] = [[-60, 5], [60, 0], [70, 100], [-70, 100]]
    e = errs(d)
    assert any("flat bottom" in x["msg"] for x in e)


def test_bad_family_and_revolve_rules():
    d = good()
    d["body"] = {"family": "cube"}
    assert any("unknown family" in x["msg"] for x in errs(d))
    d["body"] = {"family": "revolve", "profile": [[5, 0], [60, 0], [50, 100], [0, 120]]}
    assert any("start at [0, 0]" in x["msg"] for x in errs(d))
    d["body"] = {"family": "revolve", "profile": [[0, 0], [60, 0], [55, 60], [30, 110], [0, 120]], "squash_y": 0.9}
    assert load_spec(d).body.family == "revolve"


def test_blob_rules():
    d = good()
    d["body"] = {"family": "blob", "primitives": [
        {"kind": "rounded_box", "center": [0, 0, 45], "size": [130, 110, 90], "radius": 20},
        {"kind": "sphere", "center": [0, 0, 200], "size": [40, 40, 40]}]}
    assert any("do not touch" in x["msg"] for x in errs(d))
    d["body"]["primitives"][1]["center"] = [0, 0, 110]
    assert load_spec(d).body.family == "blob"


def test_cross_checks():
    d = good()
    d["features"][1]["id"] = d["features"][0]["id"]
    d["decor"][1]["depth"] = 2.0
    e = errs(d)
    assert any("duplicate feature id" in x["msg"] for x in e)
    assert any(x["path"] == "decor[1].depth" for x in e)


def test_grille_rib_rule():
    d = good()
    d["features"][2]["grille"]["pitch"] = 3.0
    assert any("rib" in x["msg"] for x in errs(d))


def test_example_nulls_from_other_families_are_accepted():
    d = good()
    assert d["body"]["profile"] is None and d["body"]["primitives"] is None
    assert load_spec(d)


def test_schema_is_compact_and_hides_derived():
    sch = spec_json_schema()
    txt = json.dumps(sch, separators=(",", ":"))
    assert "derived" not in sch.get("properties", {})
    assert len(txt) < 20000
    assert '"title"' not in txt
    full = Spec.model_json_schema()
    assert "properties" in full


def test_validate_spec_tuple():
    s, e = validate_spec(good())
    assert s is not None and e == []
    s, e = validate_spec({"schema": "gadget-spec/1"})
    assert s is None and e
