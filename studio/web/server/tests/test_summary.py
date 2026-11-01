from __future__ import annotations

from app.summary import build_summary, digital_checks, price_of

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
    payload, text, one = build_summary("Moon Hug", {"state": "done"}, BUILD,
                                       {"files": [{"path": "manual/step-01.png"}]},
                                       {"checks": [{"name": "a", "passed": True}, {"name": "b", "passed": True}]},
                                       None, 2)
    assert "Physical checks still needed:" in text
    assert "Plus 2 further physical checks under Design progress below." in text
    assert "Design work is complete against the approved brief (v2)" in text
    assert "₹6,499 delivered (estimate" in text
    assert payload["checks"]["firmware_tests"] == "7/7" and len(payload["physical_checks"]) == 5
    assert "Moon Hug" in one


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
