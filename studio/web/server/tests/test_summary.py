from __future__ import annotations

import json
from pathlib import Path

from app.summary import build_summary, check_counts, checks_sentence, digital_checks, image_counts, price_of

FX = Path(__file__).parent / "fixtures" / "e2e"


def fx(name):
    p = FX / name
    return p.read_text() if name.endswith(".txt") else json.loads(p.read_text())

BUILD = {
    "printed": [{"name": "PRINT_Shell"}, {"name": "PRINT_RearCover"}],
    "purchased": [{"name": "A"}, {"name": "B"}, {"name": "C"}],
    "steps": [{"id": "s1"}, {"id": "s2"}],
    "firmware": {"compiled": True, "tests_passed": 7, "tests_total": 7},
    "pcb": {"layers": 2, "routed": True, "drc_errors": 0},
    "order": {"list_price_inr": 6499, "status": "ESTIMATE_REVIEW_REQUIRED"},
    "tests": [{"title": f"Physical check {i}", "expected": "ok", "kind": "physical", "status": "not_run"}
              for i in range(5)] + [{"title": "STEP valid", "kind": "digital", "status": "passed"}],
}


def test_summary_done():
    payload, text, one = build_summary("Lullaby Pod", {"state": "done"}, BUILD,
                                       {"files": [{"path": "manual/step-01.png"}]},
                                       {"checks": [{"name": "a", "passed": True}, {"name": "b", "passed": True}]},
                                       None, 2)
    assert "Physical checks still needed:" in text
    assert "Plus 2 further physical checks under Design progress below." in text
    assert "Design work is complete against the approved brief (v2)" in text
    assert "₹6,499 delivered (estimate" in text
    assert payload["checks"]["firmware_tests"] == "7/7" and len(payload["physical_checks"]) == 5
    assert "Lullaby Pod" in one


def test_summary_failed_check_is_not_complete():
    _, text, _ = build_summary("X", {"state": "done"}, BUILD, None,
                               {"checks": [{"name": "collision", "passed": False}]}, None, 1)
    assert "complete against" not in text and "collision" in text


def test_summary_error():
    payload, text, one = build_summary("X", {"state": "error", "error": "CAD failed",
                                             "stages": [{"id": "cad", "label": "Shell and CAD", "state": "error"}]},
                                       None, None, None, None, None)
    assert payload["state"] == "error" and "Shell and CAD" in text and "CAD failed" in text
    assert one.startswith("Build failed")


def test_digital_check_shapes():
    assert digital_checks({"checks": [{"passed": True}, {"status": "failed", "title": "t"}]}) == (1, 2, ["t"])
    assert digital_checks({"watertight": {"passed": True}, "limits": ["x"]}) == (1, 1, [])
    assert digital_checks(None) == (0, 0, [])


def test_price_never_invented():
    assert price_of({}, None)["inr"] is None
    assert "not quoted" in price_of({}, None)["label"].lower()
    p = price_of({}, {"list_price_incl_gst": 16899, "status": "BINDING"})
    assert p["inr"] == 16899 and "estimate" not in p["label"]
    assert price_of({}, {"list_price_inr": 123456})["label"].startswith("₹1,23,456")


# ---------------------------------------------------------------------------------------------- MAJ-20
def test_maj20_counts_on_benchmark_files():
    v1 = check_counts(fx("v1-digital-checks.json"))
    assert (v1["passed"], len(v1["failed"]), len(v1["warned"]), v1["total"]) == (83, 4, 3, 90)
    assert any("watertight" in t for t in v1["failed"]) and any("pair matrix" in t for t in v1["failed"])
    v3 = check_counts(fx("v3-digital-checks.json"))
    assert (v3["passed"], len(v3["failed"]), len(v3["warned"])) == (85, 2, 3)
    assert digital_checks(fx("v1-digital-checks.json"))[:2] == (83, 90)       # warnings are not passes
    sent = checks_sentence(v1)
    assert sent.startswith("Digital checks: 83 passed, 4 failed (") and sent.endswith("3 warnings.")


def test_maj20_not_passed_has_no_passing_item_and_tools_line_is_separate():
    build = fx("v3-build.json")
    payload, text, _ = build_summary("Sleepy Bear", {"state": "done"}, build, None, fx("v1-digital-checks.json"),
                                     fx("v3-quote.json"), 1, {"manual_summary": fx("v3-manual-summary.json")})
    line = next(x for x in text.splitlines() if x.startswith("- Digital checks:"))
    assert "83 passed, 4 failed" in line and "3 warnings" in line
    assert "58/58" not in line and "DRC" not in line            # passing items never sit in the failed list
    assert "- Firmware unit tests 58/58. PCB DRC 0 errors, ERC 0 errors." in text
    c = payload["checks"]
    assert (c["digital_passed"], len(c["digital_failed"]), c["digital_warned"], c["digital_total"]) == (83, 4, 3, 90)
    assert "not passed" not in text


def test_maj20_image_counts_from_manual_summary():
    ic = image_counts(fx("v3-manual-summary.json"), [])
    assert (ic["png"], ic["svg"], ic["source"]) == (20, 1, "manual/manual-summary.json")
    assert image_counts({"images_png": 19, "images_svg": 1}, [])["png"] == 19          # MAJ-19 fields win
    _, text, _ = build_summary("X", {"state": "done"}, fx("v3-build.json"), None, None, None, 1,
                               {"manual_summary": fx("v3-manual-summary.json")})
    assert "18 steps, 20 images and 1 SVG drawing" in text


# ---------------------------------------------------------------------------------------------- BLK-4 wording
def test_blk4_story_audio_wording():
    spec = fx("v3-spec.json")
    stories = fx("v3-stories.json")
    assert stories["audio"] is False
    p, text, _ = build_summary("X", {"state": "done"}, BUILD, None, {"checks": []}, None, 1,
                               {"spec": spec, "stories": stories})
    assert "Stories have no audio yet" in text and p["checks"]["story_audio"]["present"] is False
    dc = {"checks": [{"id": "story-audio-present", "title": "story audio", "status": "failed"}]}
    _, text, _ = build_summary("X", {"state": "done"}, BUILD, None, dc, None, 1,
                               {"spec": spec, "stories": {**stories, "audio": True}})
    assert "Stories have no audio yet" in text                  # the check decides, not the flag
    dc["checks"][0]["status"] = "passed"
    _, text, _ = build_summary("X", {"state": "done"}, BUILD, None, dc, None, 1, {"spec": spec, "stories": stories})
    assert "Story audio is on the story card." in text and "no audio yet" not in text
    _, text, _ = build_summary("X", {"state": "done"}, BUILD, None, dc, None, 1,
                               {"spec": {"behaviour": {"template": "ambient_light"}}})
    assert "audio" not in text.lower()


def test_held_quote_and_not_buildable_verdict():
    pr = price_of({"order": {"status": "HELD", "list_price_inr": None}},
                  {"status": "HELD", "held_because": ["PRINT_SpeakerRing x PURCHASE_Led"]})
    assert pr["inr"] is None and pr["status"] == "HELD" and "held" in pr["label"].lower()
    p, text, _ = build_summary("X", {"state": "done", "buildable": False,
                                     "verdict_message": "Not buildable: PRINT_SpeakerRing touches PURCHASE_Led."},
                               BUILD, None, {"checks": [{"id": "collisions", "status": "failed", "title": "c"}]},
                               {"status": "HELD"}, 1)
    assert "Not buildable: PRINT_SpeakerRing touches PURCHASE_Led." in text and p["buildable"] is False
    assert "complete against" not in text


def test_maj31_decor_adjustments_are_said_in_the_reply():
    from app.summary import decor_adjustment_lines

    cs = {"decor_adjustments": [{"spec_path": "decor[0]", "type": "face_plate", "action": "scaled", "requested_mm": [112.0, 58.0],
                                 "built_mm": [88.3, 45.7], "why": "kept on the flat front face"}]}
    lines = decor_adjustment_lines(cs)
    assert lines == ["The CAD adjusted the design to fit: the face plate (decor[0]) was scaled from 112 x 58 mm to 88.3 x 45.7 mm, "
                     "kept on the flat front face."]
    assert decor_adjustment_lines(None) == [] and decor_adjustment_lines({}) == []
    assert "recoloured" in decor_adjustment_lines({"recolored": {"materials_changed": 3}})[0]
