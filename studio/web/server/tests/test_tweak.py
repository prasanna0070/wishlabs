from __future__ import annotations

import pytest

from app.tweak import TweakError, apply_tweak, describe_tweak, merge_patch, scale_spec

SPEC = {
    "schema": "gadget-spec/1", "name": "Moon Hug",
    "colors": {"body": "#8CC9C0", "body_name": "pale teal"},
    "body": {"family": "silhouette", "outline": [[-60, 0], [60, 0], [0, 180]], "depth": 120, "edge_round": 12},
    "decor": [{"type": "eye", "at": [-16, 128], "size": [9, 9]}, {"type": "blush", "at": [-26, 116], "size": [8, 5]}],
    "features": [{"id": "speaker", "module": "spk", "at": [0, 40], "grille": {"w": 70, "h": 34}},
                 {"id": "btn", "module": "b", "at": [0, 80]}],
}


def test_rfc7386_basics():
    assert merge_patch({"a": "b"}, {"a": "c"}) == {"a": "c"}
    assert merge_patch({"a": "b"}, {"b": "c"}) == {"a": "b", "b": "c"}
    assert merge_patch({"a": "b"}, {"a": None}) == {}
    assert merge_patch({"a": [1, 2]}, {"a": [3]}) == {"a": [3]}
    assert merge_patch({"a": {"b": "c"}}, {"a": {"b": "d", "c": None}}) == {"a": {"b": "d"}}
    assert merge_patch([1], {"a": 1}) == {"a": 1}


def test_feature_and_decor_keyed_merges():
    out = apply_tweak(SPEC, {"features": {"speaker": {"at": [0, 44]}, "btn": None}, "decor": {"1": None}})
    assert out["features"] == [{"id": "speaker", "module": "spk", "at": [0, 44], "grille": {"w": 70, "h": 34}}]
    assert len(out["decor"]) == 1 and out["decor"][0]["type"] == "eye"
    assert SPEC["features"][0]["at"] == [0, 40]  # input untouched


def test_tweak_errors():
    with pytest.raises(TweakError):
        apply_tweak(SPEC, {"features": {"nope": {"at": [0, 1]}}})
    with pytest.raises(TweakError):
        apply_tweak(SPEC, {"decor": {"9": None}})
    with pytest.raises(TweakError):
        apply_tweak(SPEC, {"schema": "x"})
    with pytest.raises(TweakError):
        apply_tweak(SPEC, {}, scale=2.0)


def test_scale():
    out = scale_spec(SPEC, 1.1)
    assert out["body"]["outline"][2] == [0, 198.0]
    assert out["body"]["depth"] == 132.0 and out["body"]["edge_round"] == 13.2
    assert out["features"][0]["at"] == [0, 44.0] and out["features"][0]["grille"]["w"] == 77.0
    assert out["decor"][0]["size"] == [9.9, 9.9]


def test_describe():
    assert "sky blue" in describe_tweak({"colors": {"body_name": "sky blue"}}, None)
    assert "x1.1" in describe_tweak({}, 1.1)
    assert "moved speaker" in describe_tweak({"features": {"speaker": {"at": [0, 1]}}}, None)
