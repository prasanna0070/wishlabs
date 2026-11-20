"""The agent always shows it is working: thought summaries stream as thinking.delta (never into the answer, the stored
chat or a share link), the running turn keeps a throttled progress record in projects/<pid>/turn-progress.json, and
GET /api/projects/{pid}/turn reports it (running, stale, step, steps, thoughts) for a page that reloads mid-turn."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.progress import (MAX_THOUGHTS, TURN_STALE_S, TurnProgress, append_thought, progress_key, turn_view)
from app.util import now_iso
from conftest import KEY, events_named, make_settings, sse

THOUGHT_A = "**Reading the wish**\n\nA bedside robot for a five-year-old; keep the controls simple.\n\n"
THOUGHT_B = "**Choosing questions**\n\nHer age decides the story style, so ask that first.\n\n"
ANSWER = "I will plan a small USB-powered robot. "


def _iso_ago(seconds: float) -> str:
    t = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return t.replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------- a fake Vertex AI stream
def _chunk(*parts, usage=None):
    from google.genai import types as gt

    return gt.GenerateContentResponse(candidates=[gt.Candidate(content=gt.Content(role="model", parts=list(parts)))],
                                      usage_metadata=usage)


def _ask_user_call():
    from google.genai import types as gt

    return gt.Part(function_call=gt.FunctionCall(id="fc_1", name="ask_user", args={
        "message": "I will plan a small USB-powered robot.", "project_name": "Story Bot",
        "questions": [{"id": "age", "question": "How old is she?", "consequence": "Her age guides the controls.",
                       "options": ["3 yo", "5 yo"]}]}))


class FakeModels:
    def __init__(self, script):
        self.script = script
        self.configs: list = []

    async def generate_content_stream(self, model, contents, config):
        self.configs.append(config)
        chunks = self.script(len(self.configs), config)

        async def gen():
            for c in chunks:
                await asyncio.sleep(0)
                yield c
        return gen()


def _gemini_client(monkeypatch, tmp_path, script):
    pytest.importorskip("google.genai")
    models = FakeModels(script)
    monkeypatch.setattr("app.agent.gemini.get_client", lambda _s: SimpleNamespace(aio=SimpleNamespace(models=models)))
    app = create_app(make_settings(tmp_path, agent="gemini"))
    c = TestClient(app)
    c.__enter__()
    assert c.post("/api/login", json={"key": KEY}).status_code == 200
    return c, models


def _thinking_turn(n, config):
    from google.genai import types as gt

    usage = gt.GenerateContentResponseUsageMetadata(prompt_token_count=900, candidates_token_count=120,
                                                    thoughts_token_count=300)
    return [_chunk(gt.Part(text=THOUGHT_A, thought=True)), _chunk(gt.Part(text=THOUGHT_B, thought=True)),
            _chunk(gt.Part(text=ANSWER)), _chunk(_ask_user_call(), usage=usage)]


def test_thought_parts_stream_as_thinking_and_never_enter_the_answer(monkeypatch, tmp_path):
    c, models = _gemini_client(monkeypatch, tmp_path, _thinking_turn)
    try:
        pid = c.post("/api/projects", json={"mode": "pro"}).json()["id"]
        evs = sse(c, "POST", f"/api/projects/{pid}/chat", json={"text": "A robot that tells bedtime stories"})
        # requested from Vertex: include_thoughts on the design agent's call
        tc = models.configs[0].thinking_config
        assert tc is not None and tc.include_thoughts is True
        # streamed to the browser as their own event, in order
        assert [d["text"] for d in events_named(evs, "thinking.delta")] == [THOUGHT_A, THOUGHT_B]
        deltas = "".join(d["delta"] for d in events_named(evs, "text.delta"))
        assert deltas == ANSWER
        for d in events_named(evs, "message.done"):
            assert "Reading the wish" not in json.dumps(d), d
        # the stored chat and the project snapshot hold no thought text
        p = c.get(f"/api/projects/{pid}").json()
        blob = json.dumps(p)
        assert "Reading the wish" not in blob and "Choosing questions" not in blob
        assert any(m["kind"] == "choices" for m in p["chat"])
        assert p["turn"] is None
        # the progress record kept them, and the turn is marked done
        rec = c.app.state.svc.store.read_json(progress_key(pid))
        assert rec["state"] == "done" and rec["ended"] and "Reading the wish" in rec["thoughts"]
        view = c.get(f"/api/projects/{pid}/turn").json()
        assert view["running"] is False and view["stale"] is False and view["state"] == "done"
        assert "Choosing questions" in view["thoughts"]
        assert [s["label"] for s in view["steps"]][:3] == ["Reading your message", "Thinking", "Preparing questions"]
        # a share link never shows thoughts or the turn record
        tok = c.post(f"/api/projects/{pid}/share").json()["token"]
        shared = c.get(f"/api/share/{tok}")
        assert shared.status_code == 200
        assert "Reading the wish" not in shared.text and "turn-progress" not in shared.text
        assert "thoughts" not in shared.json() and "turn" not in shared.json()
        assert c.get(f"/api/share/{tok}/turn").status_code == 404
        assert c.get(f"/api/share/{tok}/v/1/files/../turn-progress.json").status_code == 404
    finally:
        c.__exit__(None, None, None)


def test_a_model_that_rejects_include_thoughts_falls_back_to_step_labels(monkeypatch, tmp_path):
    class Rejected(Exception):
        code = 400

    def script(n, config):
        tc = config.thinking_config
        if tc is not None and tc.include_thoughts:
            raise Rejected("include_thoughts is not supported for this model")
        return [_chunk(_ask_user_call())]

    c, models = _gemini_client(monkeypatch, tmp_path, script)
    try:
        pid = c.post("/api/projects", json={"mode": "fast"}).json()["id"]
        evs = sse(c, "POST", f"/api/projects/{pid}/chat", json={"text": "A lamp"})
        assert not events_named(evs, "error")
        assert events_named(evs, "choices")
        assert not events_named(evs, "thinking.delta")
        assert len(models.configs) == 2 and models.configs[1].thinking_config is None
        brain = c.app.state.svc.brain
        assert brain.no_thoughts == {c.app.state.svc.settings.model_fast}
        # the next turn asks without thoughts straight away (no wasted call)
        c.post(f"/api/projects/{pid}/chat", json={"answers": [], "skipped": True})
        assert all(cfg.thinking_config is None for cfg in models.configs[2:])
    finally:
        c.__exit__(None, None, None)


def test_an_empty_reply_is_asked_again(monkeypatch, tmp_path):
    """Vertex now and then replies with no tool call and no words (2 of 6 replays of one state, thoughts on or off).
    The turn asks again instead of failing with "I could not finish that step"; thoughts stay on."""
    from google.genai import types as gt

    monkeypatch.setattr("app.agent.gemini.EMPTY_RETRY_DELAY_S", 0)

    def script(n, config):
        if n <= 2:
            return [_chunk(gt.Part(text=THOUGHT_A, thought=True)), _chunk(gt.Part(text=""))] if n == 1 else \
                [_chunk(gt.Part(text=""))]
        return [_chunk(gt.Part(text=THOUGHT_B, thought=True)), _chunk(_ask_user_call())]

    c, models = _gemini_client(monkeypatch, tmp_path, script)
    try:
        pid = c.post("/api/projects", json={"mode": "pro"}).json()["id"]
        evs = sse(c, "POST", f"/api/projects/{pid}/chat", json={"text": "A robot"})
        assert not events_named(evs, "error"), events_named(evs, "error")
        assert events_named(evs, "choices")
        assert len(models.configs) == 3
        assert all(cfg.thinking_config.include_thoughts for cfg in models.configs)
        assert [d["text"] for d in events_named(evs, "status")] == ["Thinking", "Thinking again", "Thinking again"]
        assert [d["text"] for d in events_named(evs, "thinking.delta")] == [THOUGHT_A, THOUGHT_B]
        p = c.get(f"/api/projects/{pid}").json()
        assert not [m for m in p["chat"] if m["kind"] == "error"]
        # a model that keeps replying with nothing still ends the turn with an error after the retries
        models.script = lambda n, config: [_chunk(gt.Part(text=""))]
        evs = sse(c, "POST", f"/api/projects/{pid}/chat", json={"answers": [], "skipped": True})
        assert [d["code"] for d in events_named(evs, "error")] == ["no_result"]
    finally:
        c.__exit__(None, None, None)


def test_thoughts_can_be_switched_off(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_THOUGHTS", "0")
    from app.config import Settings

    assert Settings.from_env().agent_thoughts is False
    monkeypatch.delenv("AGENT_THOUGHTS")
    assert Settings.from_env().agent_thoughts is True


# ---------------------------------------------------------------------- the progress record
class RecordingStore:
    """write_json records (time, snapshot); optional delay simulates a slow GCS write."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.writes: list[tuple[float, dict]] = []
        self.lock = threading.Lock()

    def write_json(self, key, obj):
        if self.delay:
            time.sleep(self.delay)
        with self.lock:
            self.writes.append((time.monotonic(), json.loads(json.dumps(obj))))


def test_progress_writes_are_throttled_and_end_marked_done():
    async def go():
        st = RecordingStore()
        pr = TurnProgress(st, "p_x", "t_1", action="text", mode="pro", user_message_id="m_1",
                          min_interval=0.4, heartbeat=30)
        pr.start()
        await asyncio.sleep(0.05)
        assert len(st.writes) == 1                      # the first record goes out at once
        for i in range(60):                             # a burst of events within ~0.3 s
            pr.observe("thinking.delta", {"text": f"**Thought {i}**\n\n" + "x" * 80 + "\n\n"})
            pr.observe("status", {"text": "Thinking"})
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.6)
        burst = len(st.writes)
        assert 2 <= burst <= 3, burst                   # throttled: not one write per event
        gaps = [b[0] - a[0] for a, b in zip(st.writes, st.writes[1:])]
        assert all(g >= 0.38 for g in gaps), gaps
        await pr.finish("done")
        last = st.writes[-1][1]
        assert last["state"] == "done" and last["ended"] and last["turn_id"] == "t_1"
        assert last["user_message_id"] == "m_1"
        assert len(last["thoughts"]) <= MAX_THOUGHTS and last["thoughts"].startswith("**Thought")
        assert "Thought 59" in last["thoughts"]
        n = len(st.writes)
        pr.observe("status", {"text": "after the end"})  # nothing is written after finish
        await asyncio.sleep(0.5)
        assert len(st.writes) == n
    asyncio.run(go())


def test_progress_heartbeat_and_final_write_ordering():
    async def go():
        st = RecordingStore(delay=0.15)                  # slow writes
        pr = TurnProgress(st, "p_x", "t_2", min_interval=0.05, heartbeat=0.25)
        pr.start()
        await asyncio.sleep(1.4)
        assert len(st.writes) >= 3                       # heartbeats keep `updated` fresh with no events
        pr.observe("status", {"text": "Rendering 6 concepts"})
        await asyncio.sleep(0.07)                        # a write is now in flight
        await pr.finish("error")
        assert st.writes[-1][1]["state"] == "error"      # the final record is the last one written
        assert all(w[1]["state"] == "running" for w in st.writes[:-1])
    asyncio.run(go())


def test_progress_steps_from_turn_events():
    async def go():
        st = RecordingStore()
        pr = TurnProgress(st, "p_x", "t_3", min_interval=0.01)
        for ev, d in [("status", {"text": "Thinking"}), ("tool.start", {"label": "Designing concepts"}),
                      ("status", {"text": "Checking the concepts against the parts catalog"}),
                      ("repair", {"round": 1, "errors": ["Moon Bot: features[2] is outside the body by 3 mm",
                                                         "Owl Bot: wall too thin"]}),
                      ("status", {"text": "Thinking"}), ("tool.start", {"label": "Designing concepts"}),
                      ("status", {"text": "Rendering 6 concepts"})]:
            pr.observe(ev, d)
        for i in range(6):
            pr.observe("concepts.progress", {"concept_id": f"c_{i}", "index": i, "title": f"C{i}", "state": "rendering"})
        for i in range(2):
            pr.observe("concepts.progress", {"concept_id": f"c_{i}", "index": i, "title": f"C{i}", "state": "ready"})
        pr.observe("concepts.progress", {"concept_id": "c_5", "index": 5, "title": "C5", "state": "photo"})
        labels = [s["label"] for s in pr.rec["steps"]]
        assert labels == ["Reading your message", "Thinking", "Designing concepts",
                          "Checking the concepts against the parts catalog", "Fixing the design (round 1)",
                          "Thinking", "Designing concepts", "Rendering concept photos: 2 of 6 done"]
        rep = pr.rec["steps"][4]
        assert rep["detail"].startswith("Moon Bot: features[2]") and rep["detail"].endswith("(+1 more)")
        assert pr.rec["step"] == "Rendering concept photos: 2 of 6 done"
        assert all(s["ts"] for s in pr.rec["steps"])
        pr.observe("repair", {"round": 1, "errors": ["x"], "kind": "concept_rules"})
        assert pr.rec["step"] == "Fixing the concept rules (round 1)"
        pr.observe("concepts.progress", None)            # junk never raises
        await pr.finish()
    asyncio.run(go())


def test_append_thought_keeps_the_newest_paragraphs():
    buf = ""
    for i in range(200):
        buf = append_thought(buf, f"**Step {i}**\n\nI am weighing option {i} against the parts list.\n\n")
    assert len(buf) <= MAX_THOUGHTS
    assert buf.startswith("**Step") and "Step 199" in buf
    assert append_thought("**A**\n\nfirst.", "**B**\n\nsecond.") == "**A**\n\nfirst.\n\n**B**\n\nsecond."
    assert append_thought("x", "   ") == "x"


# ---------------------------------------------------------------------- GET /api/projects/{pid}/turn
def test_turn_view_states():
    assert turn_view(None, None)["state"] == "idle"
    lock = {"id": "t_a", "started": _iso_ago(30)}
    rec = {"schema": "gstudio-turn-progress/1", "turn_id": "t_a", "state": "running", "step": "Thinking",
           "steps": [{"label": "Thinking", "ts": now_iso()}], "thoughts": "**Plan**", "updated": now_iso(),
           "user_message_id": "m_1"}
    v = turn_view(lock, rec)
    assert v["running"] and not v["stale"] and v["step"] == "Thinking" and v["thoughts"] == "**Plan**"
    assert 25 <= v["age_s"] <= 60 and v["user_message_id"] == "m_1" and v["quiet_s"] is not None
    v = turn_view({"id": "t_b", "started": _iso_ago(5)}, rec)            # a record from an older turn
    assert v["running"] and v["step"] == "Reading your message" and v["thoughts"] == ""
    v = turn_view({"id": "t_a", "started": _iso_ago(TURN_STALE_S + 60)}, rec)
    assert v["stale"] and not v["running"] and v["state"] == "stale"
    v = turn_view(None, {**rec, "state": "done"})
    assert not v["running"] and v["state"] == "done" and v["turn_id"] == "t_a"


def test_turn_endpoint(authed):
    c = authed
    pid = c.post("/api/projects", json={}).json()["id"]
    assert c.get(f"/api/projects/{pid}/turn").json()["state"] == "idle"
    store = c.app.state.svc.store
    store.update_json(f"projects/{pid}/project.json", lambda p: p.update(turn={"id": "t_x", "started": _iso_ago(70)}))
    store.write_json(progress_key(pid), {"schema": "gstudio-turn-progress/1", "turn_id": "t_x", "state": "running",
                                         "step": "Designing concepts", "steps": [{"label": "Designing concepts",
                                                                                  "ts": now_iso()}],
                                         "thoughts": "**Sketching**", "updated": now_iso()})
    r = c.get(f"/api/projects/{pid}/turn")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    v = r.json()
    assert v["running"] and v["step"] == "Designing concepts" and v["thoughts"] == "**Sketching**"
    assert 60 <= v["age_s"] < 120 and v["started"]
    # a send now is refused (the composer should not even try)
    assert c.post(f"/api/projects/{pid}/chat", json={"text": "hello?"}).status_code == 409
    # older than 15 minutes: stale, and a new message is accepted again
    store.update_json(f"projects/{pid}/project.json",
                      lambda p: p.update(turn={"id": "t_x", "started": _iso_ago(TURN_STALE_S + 5)}))
    v = c.get(f"/api/projects/{pid}/turn").json()
    assert v["stale"] and not v["running"]
    evs = sse(c, "POST", f"/api/projects/{pid}/chat", json={"text": "A night light for my son"})
    assert events_named(evs, "done")
    assert c.get(f"/api/projects/{pid}/turn").json()["state"] == "done"
    assert c.get("/api/projects/p_nope/turn").status_code == 404


def test_turn_endpoint_needs_sign_in(client):
    assert client.get("/api/projects/p_x/turn").status_code == 401


def test_reload_mid_turn_sees_the_working_turn(tmp_path):
    """A second request during a running turn (a reloaded page) gets running, the step and the thoughts."""
    app = create_app(make_settings(tmp_path, scripted_think_s=8))   # thoughts at 4 s and 8 s; writes every 2 s
    with TestClient(app) as c:
        assert c.post("/api/login", json={"key": KEY}).status_code == 200
        pid = c.post("/api/projects", json={"mode": "pro"}).json()["id"]
        seen = []
        with c.stream("POST", f"/api/projects/{pid}/chat", json={"text": "A robot that tells bedtime stories"}) as r:
            ev = None
            lines = r.iter_lines()
            for line in lines:
                if line.startswith("event: "):
                    ev = line[7:]
                elif line.startswith("data: "):
                    seen.append((ev, json.loads(line[6:])))
                    if ev == "thinking.delta":
                        break
            v = c.get(f"/api/projects/{pid}/turn").json()      # the stream is still open: mid-turn
            assert v["running"] and not v["stale"]
            um = events_named(seen, "turn.start")[0]["user_message"]
            assert v["user_message_id"] == um["id"] and v["turn_id"] == events_named(seen, "turn.start")[0]["turn_id"]
            assert v["started"] == events_named(seen, "turn.start")[0]["started"]
            deadline = time.time() + 3.5
            while "Reading the wish" not in v["thoughts"] and time.time() < deadline:
                time.sleep(0.2)
                v = c.get(f"/api/projects/{pid}/turn").json()
            assert "Reading the wish" in v["thoughts"] and v["step"] == "Thinking"
            assert c.get(f"/api/projects/{pid}").json()["turn"]["id"] == v["turn_id"]
            for line in lines:                                  # let the turn finish
                pass
        v = c.get(f"/api/projects/{pid}/turn").json()
        assert not v["running"] and v["state"] == "done" and "Preparing questions" in [s["label"] for s in v["steps"]]
        assert "Reading the wish" not in json.dumps(c.get(f"/api/projects/{pid}").json()["chat"])
