"""Catalog A: the brief trace of a printed-only object (no electronics). BENCH shark-teapot (20 Nov) listed "Character
details: an engraved mouth, embossed eyes ..." as NOT MET because "eyes" names the display role and a printed object
has no display module; "playful" names the speaker role ("play") the same way."""
from __future__ import annotations

from app.trace import requirements

BRIEF = {
    "functions": [{"title": "Key loop", "value": "A playful tab with a hole for a key ring"}],
    "details": [{"topic": "Character details", "value": "An engraved mouth, embossed eyes and side blush"},
                {"topic": "Spout hole", "value": "A horizontal hole through the spout"}],
}
NO_ELECTRONICS = {"instances": [], "power": {"ok": True}}


def test_printed_decor_words_are_unverified_not_missing_modules():
    rows = {r["item"].split(":")[0]: r for r in requirements(BRIEF, electrical=NO_ELECTRONICS, printed=True)}
    for k in ("Key loop", "Character details"):
        assert rows[k]["status"] == "unverified", rows[k]
        assert "printed object has no electronics" in rows[k]["evidence"]
    assert rows["Spout hole"]["status"] == "unverified"
    assert not [r for r in rows.values() if r["status"] == "not_met"]


def test_a_gadget_still_reports_a_missing_module():
    rows = {r["item"].split(":")[0]: r for r in requirements(BRIEF, electrical=NO_ELECTRONICS)}
    assert rows["Character details"]["status"] == "not_met"  # unchanged for gadgets: eyes with no display module
