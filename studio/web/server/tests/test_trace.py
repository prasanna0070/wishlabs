"""MAJ-37: the brief trace on the benchmark files."""
from __future__ import annotations

import json
from pathlib import Path

from app.brief import size_from_spec
from app.summary import check_counts, story_audio_state
from app.trace import cad_size, requirements

FX = Path(__file__).parent / "fixtures" / "e2e"


def fx(name):
    p = FX / name
    return p.read_text() if name.endswith(".txt") else json.loads(p.read_text())


def _rows(brief, stories=None):
    stories = stories if stories is not None else fx("v3-stories.json")
    story = story_audio_state(fx("v3-spec.json"), stories, check_counts(fx("v3-digital-checks.json")))
    return requirements(brief, electrical=fx("v3-electrical.json"), build=fx("v3-build.json"),
                        components=fx("v3-components.json"), fw_text=fx("v3-test-results.txt"), story=story,
                        stories=stories, quote=fx("v3-quote.json"))


def test_v3_size_not_met_and_every_item_has_a_row():
    brief = fx("web-project.json")["brief_history"][0]
    rows = _rows(brief)
    size = next(r for r in rows if r["kind"] == "size")
    assert size["status"] == "not_met" and "H brief 168 vs CAD 188" in size["evidence"]
    n_items = len(brief["functions"]) + 1 + 1 + len(brief["details"])      # functions, size, power, details
    assert len(rows) == n_items
    for r in rows:
        assert r["status"] in ("implemented", "not_met", "unverified") and r["evidence"]
        if r["status"] != "unverified":
            assert r["paths"], r


def test_voice_input_traces_to_the_mic_and_a_firmware_test():
    brief = fx("web-project.json")["brief_history"][0]
    voice = next(r for r in _rows(brief) if r["item"].startswith("Voice input"))
    assert voice["status"] == "implemented"
    assert "MIC1 MIC_INMP441_I2S on J_MIC" in voice["evidence"] and "PASS" in voice["evidence"]
    assert voice["paths"] == ["electrical.json", "firmware/test-results.txt"]


def test_story_function_not_met_without_audio_and_met_with_it():
    brief = fx("web-project.json")["brief_history"][0]
    audio = next(r for r in _rows(brief) if r["item"].startswith("Audio output"))
    assert audio["status"] == "not_met" and "no audio yet" in audio["evidence"]
    ok = next(r for r in _rows(brief, {**fx("v3-stories.json"), "audio": True}) if r["item"].startswith("Audio output"))
    assert ok["status"] == "implemented"


def test_size_implemented_after_maj36_measurement():
    cad = cad_size(fx("v3-components.json"))
    assert (round(cad["w_mm"]), round(cad["d_mm"]), round(cad["h_mm"])) == (142, 110, 188)
    brief = dict(fx("web-project.json")["brief_history"][0])
    brief["size"] = size_from_spec(fx("v3-spec.json"), cad, "the CAD of v3")
    size = next(r for r in _rows(brief) if r["kind"] == "size")
    assert size["status"] == "implemented"
    brief["size"] = {"w_mm": 141, "d_mm": 110, "h_mm": 168, "scope": "body", "value": "body only"}
    size = next(r for r in _rows(brief) if r["kind"] == "size")
    assert size["status"] == "implemented" and "height not compared" in size["evidence"]


def test_details_trace_through_part_roles():
    brief = fx("web-project.json")["brief_history"][0]
    rows = _rows(brief)
    off = next(r for r in rows if r["item"].startswith("Offline storage"))
    assert off["status"] == "implemented" and "SD1 MICROSD_SPI_MODULE_5V" in off["evidence"]
    src = next(r for r in rows if r["item"].startswith("Sourcing country"))
    assert src["status"] == "implemented" and src["paths"] == ["build.json", "order/quote.json"]
