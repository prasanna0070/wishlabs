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


# ------------------------------------------------------------------------------------------------ catalog packs (E2)
CAT_FIX = Path(__file__).parent / "fixtures_cat"


def _packs(monkeypatch, *names):
    monkeypatch.delenv("GS_PACKS_OFF", raising=False)
    monkeypatch.setenv("GS_PACKS_ENABLED", ",".join(names))


def cup():
    """A printed pen-cup planter: revolve, one piece, open top with a drain hole (no electronics)."""
    return {"schema": "gadget-spec/1", "name": "Pen Cup", "slug": "pen-cup", "kind": "printed",
            "body": {"family": "revolve", "profile": [[0, 0], [32, 0], [36, 70], [0, 70]], "wall": 2.4, "split": "none",
                     "feet": 0, "edge_round": 2},
            "printed": {"opening": "top", "drain_hole": True}}


def test_cat_schema_with_packs_off_is_the_baseline_schema(monkeypatch):
    _packs(monkeypatch)
    text = (CAT_FIX / "spec_schema_baseline.json").read_text(encoding="utf-8")  # b06a131, before E2
    assert spec_json_schema(compact=True) == json.loads(text)
    # byte for byte what the agent saw before (engine_bridge.spec_schema_text uses these separators)
    assert json.dumps(spec_json_schema(compact=True), separators=(",", ":")) == text


def test_cat_schema_shows_pack_fields_only_when_on(monkeypatch):
    _packs(monkeypatch, "printed", "mount")
    sch = spec_json_schema()
    assert {"kind", "printed", "mount"} <= set(sch["properties"]) and "cuts" not in sch["properties"]
    assert "behaviour" not in sch["required"] and "Cut" not in sch["$defs"]
    assert "none" in sch["$defs"]["RevolveBody"]["properties"]["split"]["enum"]
    _packs(monkeypatch, "printed", "printed_cuts")
    sch = spec_json_schema()
    assert "cuts" in sch["properties"] and "Cut" in sch["$defs"] and "mount" not in sch["properties"]
    _packs(monkeypatch, "mount")
    sch = spec_json_schema()
    assert "mount" in sch["properties"] and "kind" not in sch["properties"] and "behaviour" in sch["required"]
    assert "none" not in sch["$defs"]["RevolveBody"]["properties"]["split"]["enum"]


def test_cat_pack_fields_are_refused_while_off(monkeypatch):
    _packs(monkeypatch)
    e = errs(cup())
    assert any(x["path"] == "kind" and x.get("code") == "not_available" and "not available yet" in x["msg"] for x in e)
    d = good()
    d["mount"] = {"kind": "wall"}
    assert [x["path"] for x in errs(d) if x.get("code") == "not_available"] == ["mount.kind"]
    d["mount"] = {"kind": "desk"}
    assert load_spec(d).mount.kind == "desk"
    d = good()
    d["body"]["split"] = "none"
    assert any(x["path"] == "body.split" and "only for kind 'printed'" in x["msg"] for x in errs(d))
    d = good()
    d["cuts"] = [{"kind": "slot", "face": "front", "at": [0, 60], "size": [30, 4], "depth": 3}]
    assert any(x["path"] == "cuts" and x.get("code") == "not_available" for x in errs(d))
    d = good()
    del d["behaviour"]
    assert {"path": "behaviour", "msg": "required field is missing"} in errs(d)
    d = good()
    d["colour"] = "red"
    fix = next(x["fix"] for x in errs(d) if x["msg"] == "unknown key 'colour'")
    assert "behaviour" in fix and not any(k in fix.split(", ") for k in ("kind", "printed", "cuts", "mount"))
    dumped = load_spec(good()).to_json_dict()  # a gadget dumps exactly as before the packs
    assert not {"kind", "printed", "cuts", "mount"} & set(dumped) and dumped["behaviour"]["template"] == "story_player"


def test_cat_printed_kind(monkeypatch):
    _packs(monkeypatch, "printed")
    s = load_spec(cup())
    assert s.kind == "printed" and s.behaviour is None and s.printed.opening == "top" and s.body.split == "none"
    again = load_spec(s.to_json_dict())
    assert again.to_json_dict() == s.to_json_dict() and again.to_json_dict()["kind"] == "printed"
    small = cup()  # a 30 x 30 x 25 mm keychain cap: printed floors (20 mm per axis, 10 mm flat bottom)
    small["body"]["profile"] = [[0, 0], [15, 0], [15, 25], [0, 25]]
    small["printed"] = None
    assert load_spec(small).body.profile[-1] == (0.0, 25.0)
    gadget = good()
    gadget["body"] = {"family": "revolve", "profile": [[0, 0], [15, 0], [15, 25], [0, 25]]}
    assert any("h >= 40" in x["msg"] for x in errs(gadget))
    flat = cup()
    flat["body"] = {"family": "silhouette", "outline": [[-20, 0], [20, 0], [20, 30], [-20, 30]], "depth": 25, "split": "none",
                    "feet": 0}
    assert load_spec(flat).body.depth == 25  # 12 cm2 outline and 25 mm depth are fine for a printed object
    g = good()
    g["body"]["depth"] = 25
    assert any(x["path"] == "body.depth" for x in errs(g))
    g = good()
    g["body"]["outline"] = [[-20, 0], [20, 0], [20, 30], [-20, 30]]
    assert any("15 cm^2" in x["msg"] for x in errs(g))
    bad = cup()
    bad["features"] = [{"id": "light", "module": "LED_WS2812B_1PX_MODULE", "face": "front", "at": [0, 30]}]
    bad["behaviour"] = {"template": "ambient_light"}
    bad["printed"]["opening"] = "none"
    paths = {x["path"] for x in errs(bad)}
    assert {"features", "behaviour", "printed.drain_hole"} <= paths
    cut = cup()
    cut["cuts"] = [{"kind": "hole", "face": "top", "at": [0, 0], "size": [6, 6], "depth": 10}]
    assert any(x["path"] == "cuts" and x.get("code") == "not_available" for x in errs(cut))
    _packs(monkeypatch, "printed", "printed_cuts")
    assert load_spec(cut).cuts[0].kind == "hole"


def test_cat_mount_rules(monkeypatch):
    _packs(monkeypatch, "mount")
    d = good()  # silhouette, rear_cover, 4 feet, child-directed
    d["mount"] = {"kind": "wall"}
    assert any(x["path"] == "body.feet" for x in errs(d))
    d["body"]["feet"] = 0
    assert load_spec(d).mount.kind == "wall"
    d["body"]["split"] = "base_plate"
    assert any(x["path"] == "mount.kind" and "rear_cover" in x["msg"] for x in errs(d))
    d = good()
    d["body"]["feet"] = 0
    d["body"]["split"] = "base_plate"
    d["mount"] = {"kind": "ceiling", "screws": 3}
    assert any(x.get("code") == "not_available" and x["path"] == "mount.kind" for x in errs(d))
    _packs(monkeypatch, "mount", "mount_ceiling")
    assert any(x.get("code") == "child_safety" for x in errs(d))
    d["audience"] = {"child_directed": False}
    d["safety"]["flags"] = ["CHILD_VOICE_STORED"]
    assert load_spec(d).mount.screws == 3


def test_cat_normalize_dispatches_printed_objects(monkeypatch):
    import types

    from gstudio.library import Library
    from gstudio.normalize import normalize

    _packs(monkeypatch, "printed")
    fake = types.ModuleType("gstudio.printed")
    fake.normalize_printed = lambda sp, lib, raise_on_error=False: (sp, {"ok": True, "errors": [], "printed": True})
    monkeypatch.setitem(sys.modules, "gstudio.printed", fake)
    sp, rep = normalize(cup(), Library())
    assert rep.get("printed") is True and sp.kind == "printed"
    monkeypatch.setitem(sys.modules, "gstudio.printed", None)  # package A's printed.py absent: refused, never a crash
    sp, rep = normalize(load_spec(cup()), Library())
    assert not rep["ok"] and any(x.get("code") == "not_available" for x in rep["errors"])
    gadget = load_spec(good())
    gadget.behaviour = None  # a Spec built in code without behaviour: reported, not a crash
    _, rep = normalize(gadget, Library())
    assert {"path": "behaviour", "msg": "required field is missing"} in rep["errors"]
