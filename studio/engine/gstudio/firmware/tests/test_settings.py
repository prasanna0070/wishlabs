from __future__ import annotations

import pytest

from gstudio.firmware import TEMPLATES, validate_settings


@pytest.mark.parametrize("template", TEMPLATES)
def test_defaults_are_valid_and_child_safe(template):
    norm, errors = validate_settings(template, {})
    assert errors == []
    assert norm["volume_max"] <= 60
    assert norm["volume_default"] <= norm["volume_max"]
    assert norm["led_max_percent"] <= 60


def test_story_player_defaults():
    norm, _ = validate_settings("story_player", {})
    assert norm["capture_mode"] == "parent_web"  # MAJ-1: no voice recording by default
    assert norm["voice_clip_consent"] is False
    assert norm["clip_seconds"] == 3.0  # MIN-38
    assert norm["night_light_max_percent"] == 30 and norm["night_light_brightness"] <= 30  # MAJ-29 cap
    assert norm["keep_window_seconds"] == 10
    assert norm["night_light_brightness"] <= 20
    assert norm["language"] == "en-IN"


def test_child_voice_clip_needs_consent_and_the_flag():
    st = {"capture_mode": "child_voice_clip"}
    _, errors = validate_settings("story_player", st, child_directed=True, safety_flags=["CHILD_DIRECTED"])
    assert any("voice_clip_consent" in e for e in errors) and any("CHILD_VOICE_STORED" in e for e in errors)
    _, errors = validate_settings("story_player", dict(st, voice_clip_consent=True), child_directed=True,
                                  safety_flags=["CHILD_DIRECTED"])
    assert errors == ["safety.flags: child_voice_clip needs the CHILD_VOICE_STORED flag, else use capture_mode parent_web"]
    norm, errors = validate_settings("story_player", dict(st, voice_clip_consent=True), child_directed=True,
                                     safety_flags=["CHILD_DIRECTED", "CHILD_VOICE_STORED"])
    assert errors == [] and norm["capture_mode"] == "child_voice_clip"
    # Without the flags (callers that do not know them) only the settings themselves are checked.
    assert validate_settings("story_player", st)[1] == []


def test_network_defaults_off_for_a_child():
    # MAJ-2: a child-directed design has no Wi-Fi unless the design turns it on.
    assert validate_settings("story_player", {}, child_directed=True)[0]["network"] == "off"
    assert validate_settings("story_player", {}, child_directed=False)[0]["network"] == "parent_page"
    assert validate_settings("story_player", {"network": "parent_page"}, child_directed=True)[0]["network"] == "parent_page"
    assert validate_settings("notifier_clock", {}, child_directed=True)[0]["network"] == "parent_page"
    _, errors = validate_settings("notifier_clock", {"network": "off"})
    assert errors and "notifier_clock" in errors[0]


def test_night_brightness_cannot_exceed_its_cap():
    _, errors = validate_settings("story_player", {"night_light_brightness": 40})
    assert errors and "night_light_max_percent" in errors[0]


def test_unknown_key_is_rejected_with_its_path():
    norm, errors = validate_settings("story_player", {"volum": 30})
    assert norm is None
    assert errors == ["behaviour.settings.volum: unknown setting (remove it)"]


def test_out_of_range_says_the_bound_and_the_value():
    _, errors = validate_settings("story_player", {"volume_max": 140})
    assert len(errors) == 1
    assert errors[0].startswith("behaviour.settings.volume_max:")
    assert "100" in errors[0] and "140" in errors[0]


def test_bad_choice_lists_the_options():
    _, errors = validate_settings("story_player", {"capture_mode": "cloud_upload"})
    assert "child_voice_clip" in errors[0] and "parent_web" in errors[0]


def test_cross_field_rules():
    _, e1 = validate_settings("story_player", {"volume_default": 70, "volume_max": 50})
    assert "volume_default" in e1[0]
    _, e2 = validate_settings("ambient_light", {"default_scene": 5})
    assert "default_scene" in e2[0]
    _, e3 = validate_settings("sensor_display", {"temp_low": 20, "temp_high": 18})
    assert any("temp" in e for e in e3)


def test_child_directed_caps():
    _, errors = validate_settings("story_player", {"volume_max": 90}, child_directed=True)
    assert errors and "70" in errors[0]
    norm, errors = validate_settings("story_player", {"volume_max": 90}, child_directed=False)
    assert norm["volume_max"] == 90 and errors == []


def test_names_are_cleaned_and_deduplicated():
    norm, errors = validate_settings("story_player", {"names_for_audio": ["  Anya ", "anya", "Kabir Singh"]})
    assert errors == []
    assert norm["names_for_audio"] == ["Anya", "Kabir Singh"]
    _, errors = validate_settings("story_player", {"names_for_audio": ["<script>"]})
    assert errors and "letters" in errors[0]


def test_colours_and_times():
    norm, _ = validate_settings("story_player", {"night_light_color": "#ffb36b"})
    assert norm["night_light_color"] == "#FFB36B"
    _, errors = validate_settings("story_player", {"night_light_color": "orange"})
    assert "#FFB36B" in errors[0]
    _, errors = validate_settings("notifier_clock", {"quiet_hours_start": "25:00"})
    assert "HH:MM" in errors[0]


def test_unknown_template():
    norm, errors = validate_settings("robot_arm", {})
    assert norm is None and "story_player" in errors[0]


def test_scenes_validate():
    norm, errors = validate_settings("ambient_light", {"scenes": [{"name": "Holi", "color": "#FF3399", "effect": "rainbow"}]})
    assert errors == [] and norm["scenes"][0]["brightness"] == 60
    _, errors = validate_settings("ambient_light", {"scenes": [{"name": "Strobe", "color": "#FFFFFF", "effect": "strobe"}]})
    assert errors and "scenes.0.effect" in errors[0]
