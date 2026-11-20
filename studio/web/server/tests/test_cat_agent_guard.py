"""a-to-e 1: a reply whose text is a tool call written out ("`propose_concepts`{...}") is replaced in the chat and the
model is asked for the real call, at most twice per turn (Gemini brain loop with a scripted model)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

genai_types = pytest.importorskip("google.genai.types")


def test_tool_written_as_text_detects_only_tool_calls():
    from app.agent.gemini import tool_written_as_text

    assert tool_written_as_text('`propose_concepts`{concepts:[{blurb:"x"}]}') == "propose_concepts"
    assert tool_written_as_text("  ```build_design``` {message: 'Building'}") == "build_design"
    assert tool_written_as_text("write_brief({...})") == "write_brief"
    assert tool_written_as_text("Here are six concepts for your cap.") is None
    assert tool_written_as_text("I will propose_concepts now") is None
    assert tool_written_as_text(None) is None


class FakeTurn:
    mode = "fast"
    instruction = None
    build_n = None

    def __init__(self):
        self.msgs, self.events, self.calls, self.failed = [], [], [], None
        self.posted = False  # like Turn.add_message: an assistant message means the turn has answered

    async def project(self):
        return {"chat": [{"role": "user", "kind": "text", "text": "A small keychain cap, no electronics"}]}

    def allowed(self, p):
        return ["propose_concepts", "say"]

    async def status(self, text):
        self.events.append(("status", text))

    async def add_message(self, m):
        self.posted = self.posted or m.get("role") == "assistant"
        self.msgs.append(m)

    async def emit(self, name, data):
        self.events.append((name, data))

    async def fail(self, *a, **k):
        self.failed = (a, k)

    async def call_tool(self, name, args):
        self.calls.append(name)
        return SimpleNamespace(response={"ok": True}, terminal=True)


def _brain(monkeypatch, script):
    from app.agent import gemini

    monkeypatch.setattr(gemini, "build_contents", lambda svc, p, allowed, instruction: [])
    brain = gemini.GeminiBrain.__new__(gemini.GeminiBrain)
    brain.svc = SimpleNamespace(settings=SimpleNamespace(model_for=lambda mode: "m", model_fast="m"))
    brain._config = lambda allowed, thoughts=False, wish=None: None
    monkeypatch.setattr(gemini, "EMPTY_RETRY_DELAY_S", 0)
    seen = []

    async def call(turn, model, contents, allowed, wish=None):  # (parts, calls, msg, finish) as in the merged loop
        seen.append([c for c in contents])
        return script.pop(0)

    brain._call_with_retry = call
    return brain, seen


def _text(t, mid="m1"):
    return [genai_types.Part(text=t)], [], {"id": mid, "role": "assistant", "kind": "text", "text": t}, "STOP"


def _call(name):
    fc = genai_types.FunctionCall(name=name, args={})
    return [genai_types.Part(function_call=fc)], [fc], None, "STOP"


def _empty():
    return [], [], None, "STOP"


def test_text_tool_call_is_replaced_and_retried(monkeypatch):
    turn = FakeTurn()
    brain, seen = _brain(monkeypatch, [_text('`propose_concepts`{concepts:[]}'), _call("propose_concepts")])
    asyncio.run(brain.run(turn))
    assert turn.calls == ["propose_concepts"] and turn.failed is None
    assert [m["text"] for m in turn.msgs] == ["Working on it."]  # the raw call never stays in the chat
    nudge = seen[1][-1].parts[0].text
    assert "Call propose_concepts now as a real function call" in nudge


def test_text_tool_call_gives_up_after_two_retries(monkeypatch):
    turn = FakeTurn()
    brain, seen = _brain(monkeypatch, [_text("`propose_concepts`{}", f"m{k}") for k in range(3)])
    asyncio.run(brain.run(turn))
    assert turn.calls == [] and len(seen) == 3
    assert [m["text"] for m in turn.msgs] == ["Working on it.", "Working on it.", "I could not finish that step. Please try again."]


def test_plain_text_reply_ends_the_turn(monkeypatch):
    turn = FakeTurn()
    brain, seen = _brain(monkeypatch, [_text("Which colour would you like?")])
    asyncio.run(brain.run(turn))
    assert [m["text"] for m in turn.msgs] == ["Which colour would you like?"] and len(seen) == 1


def test_empty_reply_after_the_nudge_is_asked_again(monkeypatch):
    # merge of hack/thinking: "Working on it." counts as an answer there, but the nudge still owes a real call
    turn = FakeTurn()
    brain, seen = _brain(monkeypatch, [_text("`propose_concepts`{}"), _empty(), _call("propose_concepts")])
    asyncio.run(brain.run(turn))
    assert turn.calls == ["propose_concepts"] and turn.failed is None and len(seen) == 3
    assert [m["text"] for m in turn.msgs] == ["Working on it."]
