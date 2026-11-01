"""MIN-41 (user facts quote the user), MAJ-28 (subject fact), MAJ-36 (size label, brief revisions)."""
from __future__ import annotations

import json
from pathlib import Path

from app.brief import (assemble_brief, enforce_user_quote, glb_bbox_mm, revise_brief, same_geometry, size_from_spec,
                       user_texts)
from app.concept_rules import subject_from_text

FX = Path(__file__).parent / "fixtures" / "e2e"
PROMPT = "I want a cute robot that tells my daughter bedtime stories and remembers her favorite characters."


def chat_project():
    c = json.loads((FX / "chat.json").read_text())
    return {"chat": c["chat"] if isinstance(c, dict) else c}


def test_user_texts_are_the_stored_words():
    t = user_texts(chat_project())
    assert t[0] == PROMPT and "5 years old" in t and "She tells the robot herself (a microphone is OK)" in t
    assert not any(x.startswith("My choices") for x in t)


def test_invented_quote_is_rewritten_to_the_stored_answer():
    texts = user_texts(chat_project())
    f = enforce_user_quote({"value": "A bedside companion for a 5-year-old", "source": "user",
                            "evidence": "You chose 5 yo old and bedtime stories"}, texts)
    assert f["evidence"] == 'You said: "5 years old"' and f["source"] == "user"
    ok = {"value": "x", "source": "user", "evidence": 'You said: "5 years old"'}
    assert enforce_user_quote(ok, texts) == ok
    other = enforce_user_quote({"value": "Glows in pastel rainbow colours", "source": "user",
                                "evidence": "You loved rainbows"}, texts)
    assert other["source"] == "suggested"                                 # nothing the user said supports it


def test_brief_gets_the_subject_fact_and_quotes():
    spec = json.loads((FX / "concept-spec.json").read_text())
    raw = {"title": "T", "summary": {"value": "s", "source": "suggested", "evidence": "e"},
           "purpose": {"value": "Stories for a 5-year-old", "source": "user", "evidence": "You chose 5 yo old"},
           "functions": [{"title": "Voice input", "value": "She says new favourites", "source": "user",
                          "evidence": "You chose to let her tell the robot"}],
           "interaction": "i", "privacy_safety": "p", "power": "USB-C", "limits": []}
    b = assemble_brief(raw, spec, "c_1", 1, {}, texts=user_texts(chat_project()), subject=subject_from_text(PROMPT))
    subj = b["details"][0]
    assert subj == {"topic": "Subject", "value": "A robot", "source": "user", "evidence": 'You wrote: "a cute robot"'}
    assert b["purpose"]["evidence"] == 'You said: "5 years old"'
    assert b["functions"][0]["evidence"] == 'You said: "She tells the robot herself (a microphone is OK)"'
    assert b["spec_hash"].startswith("sha256:")


def test_size_from_the_concept_preview_geometry():
    glb = (FX / "concept-preview-json-only.glb").read_bytes()
    m = glb_bbox_mm(glb)
    assert m == {"w_mm": 142, "d_mm": 110, "h_mm": 179}
    spec = json.loads((FX / "concept-spec.json").read_text())
    s = size_from_spec(spec, m, "the CAD preview of this concept")
    assert s["scope"] == "shell" and s["value"] == "About 142 W x 110 D x 179 H mm (shell with ears, without feet)"
    assert "before the feet are added" in s["evidence"]
    cad = size_from_spec(spec, {"w_mm": 141.9, "d_mm": 110, "h_mm": 181}, "the CAD of v1")
    assert cad["scope"] == "overall" and cad["h_mm"] == 181 and "standing on its feet" in cad["evidence"]
    body = size_from_spec({**spec, "derived": {"size_mm": [141, 110, 168]}})
    assert body["scope"] == "body" and "body only" in body["value"]
    assert same_geometry(spec, json.loads(json.dumps(spec)))


def test_revise_brief_records_the_change():
    v1 = json.loads((FX / "input-v1.json").read_text())["spec"]
    v2 = json.loads((FX / "input-v2.json").read_text())["spec"]
    prev = assemble_brief({"title": "T", "summary": "s", "purpose": "p", "functions": [], "interaction": "i",
                           "privacy_safety": "x", "power": "y", "limits": []}, v1, "c_1", 1, {})
    b = revise_brief(prev, v2, version=2, for_version=2, source="update", change="ears 25→32 mm", names={})
    assert b["version"] == 2 and b["revised_from"] == 1 and b["auto_for_version"] == 2 and b["approved"]
    ch = b["details"][-1]
    assert ch == {"topic": "Change in v2", "value": "ears 25→32 mm", "source": "user", "evidence": "Edit v2: ears 25→32 mm"}
    assert b["spec"] is v2 and b["spec_hash"] != prev["spec_hash"] and b["concept_id"] == "c_1"
