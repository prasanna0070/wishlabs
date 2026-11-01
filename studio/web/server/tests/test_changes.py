"""MAJ-34: the server states exactly what an edit changed and stops edits outside the selected part."""
from __future__ import annotations

import json
from pathlib import Path

from app.changes import changes_text, claims, describe, named_paths, shape_hints, unsupported, unsupported_note

FX = Path(__file__).parent / "fixtures" / "e2e"
EAR_FIELDS = {"ear": {"type", "at", "color", "style", "side", "size", "thickness", "tilt"}}


def specs():
    """The benchmark's v1 and v2 input specs with schema defaults filled (v2 left out body.wall, which keeps its
    default 2.4: that is not a change)."""
    from app.config import Settings
    from app.engine_bridge import EngineBridge

    br = EngineBridge(Settings())
    return (br.with_defaults(json.loads((FX / "input-v1.json").read_text())["spec"]),
            br.with_defaults(json.loads((FX / "input-v2.json").read_text())["spec"]))


def test_benchmark_part_edit_is_described_exactly():
    v1, v2 = specs()
    named = named_paths("make the ears rounder and the face a bit bigger", v1)
    assert named == {"decor[0]", "decor[1]"}                       # 'body' is not named
    items = describe(v1, v2, named | {"colors"})
    texts = {i["text"]: i["requested"] for i in items}
    assert texts == {"face plate 100x50→112x58 mm": True, "ears 25→32 mm": True, "body edge rounding 12→16 mm": False}
    txt = changes_text(items)
    assert txt.endswith("; also changed: body edge rounding 12→16 mm")
    assert "face plate 100x50→112x58 mm" in txt.split(";")[0] and "ears 25→32 mm" in txt.split(";")[0]


def test_rounder_ears_has_no_parameter_and_claims_are_caught():
    v1, _ = specs()
    u = unsupported("make the ears rounder", v1, {"decor[1]"}, EAR_FIELDS)
    assert u and u[0]["property"] == "roundness" and u[0]["part"] == "ears"
    assert "no roundness setting yet" in unsupported_note(u)
    assert claims("I've made the bear's ears bigger and rounder.", u)
    assert not claims("The ears are bigger now.", u)
    fields = {"ear": EAR_FIELDS["ear"] | {"roundness"}}             # once MAJ-30 adds the parameter
    assert unsupported("make the ears rounder", v1, {"decor[1]"}, fields) == []


def test_named_paths_features_and_colours():
    v1, _ = specs()
    n = named_paths("move the button down and make it pink", v1)
    btn = next(i for i, f in enumerate(v1["features"]) if "BUTTON" in f["module"])
    assert f"features[{btn}]" in n and "colors" in n and "body" not in n


def test_rounder_maps_to_roundness_once_the_schema_has_it():
    v1, _ = specs()
    assert shape_hints("make the ears rounder", v1, {"decor[1]"}, EAR_FIELDS) == []
    h = shape_hints("make the ears rounder", v1, {"decor[1]"}, {"ear": EAR_FIELDS["ear"] | {"roundness"}})
    assert h and "decor[1].roundness" in h[0] and "raise it" in h[0]


def test_shell_click_naming_ears_does_not_license_body_changes():
    from app.agent.turn import scope_allowed

    v1, v2 = specs()
    named = named_paths("make the ears rounder and the face a bit bigger", v1)
    allowed = scope_allowed("body", named)
    assert allowed == {"colors", "decor[0]", "decor[1]"}
    from app.changes import changed_paths, in_scope
    outside = [p for p in changed_paths(v1, v2) if not in_scope(p, allowed)]
    assert outside == ["body.edge_round"]                       # the benchmark v2 edit would be stopped
    assert "body" in scope_allowed("body", named_paths("make the body taller", v1))
    assert scope_allowed("decor[1]", set()) == {"colors", "decor[1]"}
