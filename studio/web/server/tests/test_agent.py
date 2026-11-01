"""Agent: tool-schema validity, stage gating, prompt contents, history rendering, and the repair loop driven by a
mocked model brain through the real tool executor."""
from __future__ import annotations

import copy

import pytest

from app.agent.history import render_message
from app.agent.prompt import PromptBuilder
from app.agent.scripted import ScriptedBrain
from app.agent.tools import TERMINAL, TOOLS, allowed_tools
from app.engine_bridge import EngineBridge

from conftest import events_named, make_settings, sse

ALLOWED_SCHEMA_KEYS = {"type", "properties", "required", "items", "description", "minItems", "maxItems", "enum"}


def _walk(schema, path="$"):
    assert isinstance(schema, dict), path
    assert set(schema) <= ALLOWED_SCHEMA_KEYS, (path, set(schema) - ALLOWED_SCHEMA_KEYS)
    t = schema.get("type")
    assert t in ("object", "array", "string", "boolean", "number", "integer"), (path, t)
    if t == "object":
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            assert k in props, (path, k)
        for k, v in props.items():
            _walk(v, f"{path}.{k}")
    if t == "array":
        _walk(schema["items"], f"{path}[]")


def test_tool_schemas_are_valid():
    assert set(TOOLS) == {"ask_user", "propose_concepts", "write_brief", "build_design", "update_design", "say"}
    for name, t in TOOLS.items():
        assert t["description"]
        _walk(t["parameters"], name)
    assert TERMINAL == set(TOOLS) - {"say"}


def test_tool_declarations_build():
    pytest.importorskip("google.genai")
    from app.agent.tools import declarations

    decls = declarations(list(TOOLS))
    assert [d.name for d in decls] == list(TOOLS)
    assert decls[0].parameters_json_schema["properties"]["questions"]["maxItems"] == 3


def test_stage_gating():
    assert "ask_user" in allowed_tools("clarify", 3, False, False)
    assert "ask_user" not in allowed_tools("clarify", 0, False, False)
    assert "write_brief" not in allowed_tools("concepts", 1, False, False)
    assert "write_brief" in allowed_tools("concepts", 1, True, False)
    assert allowed_tools("building", 3, True, True) == ["say"]
    assert "update_design" in allowed_tools("ready", 0, True, True)
    assert "build_design" not in allowed_tools("ready", 0, True, True)
    assert allowed_tools("concepts", 3, True, False, forced="write_brief") == ["write_brief", "say"]


def test_system_prompt_has_rules(tmp_path):
    s = make_settings(tmp_path)
    text = PromptBuilder(EngineBridge(s), s).system_prompt()
    for needle in ("at most 3 questions", "design consequence", "USB-C 5 V", "CHILD_DIRECTED", "Wall at least 2.2",
                   "Never invent", "gadget-spec/1", "Parts catalog", "Worked example", "no cloud audio"):
        assert needle.lower() in text.lower(), needle


def test_history_rendering():
    p = {"chat": [], "concepts": [{"id": "c_1", "title": "Moon Hug", "blurb": "b", "state": "ready"}]}
    assert render_message({"role": "user", "kind": "text", "text": "hi"}, p) == ("user", "hi")
    r = render_message({"role": "assistant", "kind": "choices", "text": "t",
                        "payload": {"questions": [{"id": "age", "question": "Age?", "options": ["3", "5"],
                                                   "consequence": "c"}]}}, p)
    assert r[0] == "model" and "Age?" in r[1]
    r = render_message({"role": "assistant", "kind": "concepts", "text": "Pick",
                        "payload": {"concept_ids": ["c_1"]}}, p)
    assert "Moon Hug" in r[1]


class RepairBrain:
    """Pretends to be the model: first an invalid spec, then the valid one (one repair round)."""

    kind = "mock"

    def __init__(self, svc):
        self.svc = svc
        self.calls = []

    async def run(self, turn):
        base = ScriptedBrain(self.svc).base_spec()
        bad = copy.deepcopy(base)
        bad["body"]["family"] = "pyramid"
        bad["sparkles"] = True
        r1 = await turn.call_tool("propose_concepts", {"message": "Pick one", "concepts": [
            {"title": "A", "blurb": "a", "spec": bad}, {"title": "B", "tier": "expected", "blurb": "b",
                                                         "spec": base}]})
        self.calls.append(r1.response)
        assert not r1.terminal and r1.response["errors"]
        assert r1.response["kept"] == ["B"] and list(r1.response["errors_by_concept"]) == ["A"]
        good = copy.deepcopy(base)
        good["name"] = "A"
        r2 = await turn.call_tool("propose_concepts", {"concepts": [  # resend only the failed one
            {"title": "A", "tier": "wildcard", "blurb": "a", "spec": good}]})
        self.calls.append(r2.response)


class AlwaysBadBrain:
    kind = "mock"

    def __init__(self, svc):
        self.svc = svc

    async def run(self, turn):
        for _ in range(5):
            r = await turn.call_tool("propose_concepts", {"message": "x", "concepts": [
                {"title": "A", "blurb": "a", "spec": {"schema": "nope"}},
                {"title": "B", "blurb": "b", "spec": {"schema": "nope"}}]})
            if r.terminal:
                return


def _client_with_brain(tmp_path, brain_cls):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.services import Services

    s = make_settings(tmp_path)
    svc = Services(s)
    svc.brain = brain_cls(svc)
    return TestClient(create_app(s, svc)), svc


def test_repair_round_then_success(tmp_path):
    from conftest import KEY

    c, svc = _client_with_brain(tmp_path, RepairBrain)
    with c:
        c.post("/api/login", json={"key": KEY})
        pid = c.post("/api/projects", json={}).json()["id"]
        evs = sse(c, "POST", f"/api/projects/{pid}/chat", json={"text": "a robot"})
        assert events_named(evs, "repair")
        cons = events_named(evs, "concepts")[0]
        assert [c["title"] for c in cons["concepts"]] == ["B", "A"]   # sorted by tier: B expected, A wildcard
        assert cons["message"]["text"].startswith("Pick one")
        assert svc.brain.calls[0]["round"] == 1


def test_repair_rounds_exhausted(tmp_path):
    from conftest import KEY

    c, svc = _client_with_brain(tmp_path, AlwaysBadBrain)
    with c:
        c.post("/api/login", json={"key": KEY})
        pid = c.post("/api/projects", json={}).json()["id"]
        evs = sse(c, "POST", f"/api/projects/{pid}/chat", json={"text": "a robot"})
        assert len(events_named(evs, "repair")) == svc.settings.max_repair_rounds + 1
        assert events_named(evs, "error")
        assert not events_named(evs, "concepts")
        p = c.get(f"/api/projects/{pid}").json()
        assert p["turn"] is None and p["chat"][-1]["kind"] == "error"


def test_out_of_scope_diff():
    from app.agent.turn import changed_paths, out_of_scope

    a = {"body": {"depth": 100}, "colors": {"body": "#fff"}, "features": [{"id": "x", "at": [0, 1]},
                                                                          {"id": "y", "at": [0, 2]}],
         "decor": [{"type": "eye"}]}
    b = copy.deepcopy(a)
    b["features"][1]["at"] = [0, 3]
    b["colors"]["body"] = "#000"
    assert changed_paths(a, b) == ["colors.body", "features[1].at"]
    assert out_of_scope(a, b, "features[1]") == []
    assert out_of_scope(a, b, "features[0]") == ["features[1].at"]
    b["body"]["depth"] = 120
    assert out_of_scope(a, b, "body") == []           # body edits may move features and decor
    assert out_of_scope(a, b, "decor[0]") == ["body.depth", "features[1].at"]


def test_usage_accounting():
    from types import SimpleNamespace

    from app.usage import apply_turn, record_from_response, sum_records

    resp = SimpleNamespace(usage_metadata=SimpleNamespace(prompt_token_count=1000, candidates_token_count=200,
                                                          thoughts_token_count=300, cached_content_token_count=0))
    rec = record_from_response("gemini-3.1-pro-preview", resp, seconds=1.234)
    assert rec["input_tokens"] == 1000 and rec["thinking_tokens"] == 300 and rec["cost_usd_est"] > 0
    p = {"usage": None}        # a fresh project stores usage: null
    apply_turn(p, {"turn_id": "t_1", **sum_records([rec, rec])})
    apply_turn(p, {"turn_id": "t_2", **sum_records([rec])})
    assert p["usage"]["totals"]["turns"] == 2 and p["usage"]["totals"]["input_tokens"] == 3000
    assert "not billing data" in p["usage"]["basis"]
