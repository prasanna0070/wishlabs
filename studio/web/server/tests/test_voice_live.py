"""Voice wish (Gemini Live) WebSocket route with Live mocked: login and origin gates, limits, the submit_wish ->
project + first chat turn path, the stored recording (owner and share), the AI receipt, and the helpers."""
from __future__ import annotations

import array
import asyncio
import contextlib
import json
import math
import time

import pytest
from starlette.websockets import WebSocketDisconnect

from conftest import KEY

from app import voice_live
from app.voice_live import (END_NUDGE, GREETING, LiveSettings, Transcript, clean_wish, compact_speech, first_message,
                            origin_ok)

types = pytest.importorskip("google.genai.types")

WISH = {"wish": "I want a night light for my daughter that glows like the moon.",
        "details": [{"question": "How old is she", "answer": "She is six."}]}


def tone(seconds: float, rate: int = 16000, amp: int = 9000) -> bytes:
    a = array.array("h", (int(amp * math.sin(2 * math.pi * 220 * i / rate)) for i in range(int(seconds * rate))))
    return a.tobytes()


def silence(seconds: float, rate: int = 16000) -> bytes:
    return b"\x00\x00" * int(seconds * rate)


# ------------------------------------------------------------------ a scripted stand-in for one Live session
def m_caption(role: str, text: str):
    key = "input_transcription" if role == "user" else "output_transcription"
    return types.LiveServerMessage(server_content=types.LiveServerContent(**{key: types.Transcription(text=text)}))


def m_audio(nbytes: int = 4800):
    return types.LiveServerMessage(server_content=types.LiveServerContent(model_turn=types.Content(
        role="model", parts=[types.Part(inline_data=types.Blob(data=b"\x01\x00" * (nbytes // 2),
                                                               mime_type="audio/pcm;rate=24000"))])))


def m_done():
    return types.LiveServerMessage(server_content=types.LiveServerContent(turn_complete=True))


def m_call(args: dict):
    return types.LiveServerMessage(tool_call=types.LiveServerToolCall(function_calls=[
        types.FunctionCall(id="fc1", name="submit_wish", args=args)]))


def m_usage(i: int, o: int):
    return types.LiveServerMessage(usage_metadata=types.UsageMetadata(prompt_token_count=i, response_token_count=o))


class FakeLive:
    """Greets on the first text, 'hears' the wish after `after_bytes` of audio, calls submit_wish (or waits for End),
    says goodbye after the tool response."""

    def __init__(self, after_bytes: int | None = 16000, args: dict | None = None, call_on_end: bool = False):
        self.q: asyncio.Queue = asyncio.Queue()
        self.audio = 0
        self.texts: list[str] = []
        self.responses: list = []
        self.after_bytes, self.args, self.call_on_end = after_bytes, args or WISH, call_on_end
        self.heard = False

    def push(self, *ms):
        for m in ms:
            self.q.put_nowait(m)

    async def send_realtime_input(self, *, audio=None, audio_stream_end=None, text=None, **_):
        if text is not None:
            self.texts.append(text)
            if text == END_NUDGE and self.call_on_end:
                self.push(m_call(self.args))
            elif not self.heard and len(self.texts) == 1:
                self.push(m_caption("assistant", "Hi, what would"), m_caption("assistant", " you like to make?"),
                          m_audio(), m_usage(120, 40), m_done())
        if audio is not None:
            self.audio += len(audio.data)
            if self.after_bytes is not None and not self.heard and self.audio >= self.after_bytes:
                self.heard = True
                self.push(m_caption("user", "A moon night light"), m_caption("user", " for my daughter."),
                          m_call(self.args))

    async def send_tool_response(self, *, function_responses):
        self.responses.append(function_responses)
        self.push(m_caption("assistant", "Sending it to the studio now."), m_audio(), m_usage(300, 60), m_done())

    async def receive(self):
        while True:
            m = await self.q.get()
            if m is None:
                return
            yield m
            if m.server_content is not None and m.server_content.turn_complete:
                return


def connector(make=FakeLive, fail_models=(), seen=None):
    @contextlib.asynccontextmanager
    async def connect(cfg, project, model):
        if model in fail_models:
            raise RuntimeError(f"1008 model {model} not found")
        fake = make()
        if seen is not None:
            seen.append((model, fake))
        yield fake
    return connect


@pytest.fixture
def voice_app(settings):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(settings)
    app.state.voice_cfg = LiveSettings(max_seconds=30, fallback_model=None)
    app.state.voice_connect = connector()
    with TestClient(app) as c:
        r = c.post("/api/login", json={"key": KEY})
        assert r.status_code == 200
        yield app, c


def converse(ws, start: dict, chunks: int = 12, until=("ended",), send_end: bool = False):
    """Run one session: start, stream audio frames, collect every JSON event until one of `until`."""
    ws.send_text(json.dumps({"type": "start", **start}))
    events, audio_in = [], 0
    ready = json.loads(ws.receive_text())
    assert ready["type"] == "ready", ready
    events.append(ready)
    for _ in range(chunks):
        ws.send_bytes(tone(0.1))
    if send_end:
        ws.send_text(json.dumps({"type": "end"}))
    while True:
        msg = ws.receive()
        if msg.get("bytes") is not None:
            audio_in += len(msg["bytes"])
            continue
        if msg.get("text") is None:
            break
        ev = json.loads(msg["text"])
        events.append(ev)
        if ev["type"] == "submitted" and start.get("client_turn"):
            ws.send_text(json.dumps({"type": "claimed"}))
        if ev["type"] in until:
            break
    return events, audio_in


def wait_turn(c, pid: str, timeout: float = 10.0) -> dict:
    t0 = time.time()
    while True:
        p = c.get(f"/api/projects/{pid}").json()
        if not p.get("turn") and p["chat"] and p["chat"][-1]["role"] == "assistant":
            return p
        assert time.time() - t0 < timeout, p
        time.sleep(0.1)


# ------------------------------------------------------------------ gates
def test_refuses_without_login(client):
    client.app.state.voice_connect = connector()
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/api/voice/live") as ws:
            ws.receive_text()
    assert e.value.code == 4401


def test_refuses_another_site(voice_app):
    _, c = voice_app
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect("/api/voice/live", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_text()
    assert e.value.code == 4403


def test_origin_rule():
    cfg = LiveSettings(allowed_origins=("http://localhost:5173",))
    assert origin_ok(None, "x", cfg)                                       # non-browser client
    assert origin_ok("https://wish.plugbook.si", "wish.plugbook.si", cfg)
    assert origin_ok("http://localhost:5173", "127.0.0.1:9201", cfg)
    assert origin_ok("https://studio.example", "other", cfg, "https://studio.example")
    assert not origin_ok("https://evil.example", "wish.plugbook.si", cfg)


def test_disabled_fails_closed(voice_app):
    app, c = voice_app
    app.state.voice_cfg = LiveSettings(enabled=False)
    with c.websocket_connect("/api/voice/live") as ws:
        ev = json.loads(ws.receive_text())
    assert ev["type"] == "error" and ev["code"] == "disabled"


def test_live_down_fails_closed_after_fallback(voice_app):
    app, c = voice_app
    app.state.voice_cfg = LiveSettings(model="m1", fallback_model="m2")
    app.state.voice_connect = connector(fail_models=("m1", "m2"))
    with c.websocket_connect("/api/voice/live") as ws:
        ws.send_text(json.dumps({"type": "start"}))
        ev = json.loads(ws.receive_text())
    assert ev["type"] == "error" and ev["code"] == "unavailable"
    assert c.get("/api/projects").json()["projects"] == []


def test_fallback_model_is_used(voice_app):
    app, c = voice_app
    seen = []
    app.state.voice_cfg = LiveSettings(model="m1", fallback_model="m2", max_seconds=30)
    app.state.voice_connect = connector(fail_models=("m1",), seen=seen)
    with c.websocket_connect("/api/voice/live") as ws:
        events, _ = converse(ws, {"client_turn": True})
    assert events[0]["model"] == "m2" and [m for m, _ in seen] == ["m2"]


def test_one_session_per_login_and_hourly_cap(voice_app):
    app, c = voice_app
    app.state.voice_connect = connector(make=lambda: FakeLive(after_bytes=None))
    with c.websocket_connect("/api/voice/live") as ws1:
        ws1.send_text(json.dumps({"type": "start"}))
        assert json.loads(ws1.receive_text())["type"] == "ready"
        with c.websocket_connect("/api/voice/live") as ws2:
            ev = json.loads(ws2.receive_text())
        assert ev["type"] == "error" and ev["code"] == "busy"
        ws1.send_text(json.dumps({"type": "cancel"}))
        while True:
            m = ws1.receive()
            if m.get("text") and json.loads(m["text"])["type"] == "ended":
                assert json.loads(m["text"])["reason"] == "cancelled"
                break
    assert c.get("/api/projects").json()["projects"] == []          # a cancelled call creates nothing
    hub = app.state.voice_hub
    hub.cfg.sessions_per_hour = 1
    with c.websocket_connect("/api/voice/live") as ws3:
        ev = json.loads(ws3.receive_text())
    assert ev["code"] == "rate_limited"


def test_time_limit_ends_without_project(voice_app):
    app, c = voice_app
    app.state.voice_cfg = LiveSettings(max_seconds=1.0, fallback_model=None)
    app.state.voice_connect = connector(make=lambda: FakeLive(after_bytes=None))
    with c.websocket_connect("/api/voice/live") as ws:
        ws.send_text(json.dumps({"type": "start"}))
        assert json.loads(ws.receive_text())["type"] == "ready"
        reason = None
        t0 = time.time()
        while time.time() - t0 < 10:
            m = ws.receive()
            if m.get("text"):
                ev = json.loads(m["text"])
                if ev["type"] == "ended":
                    reason = ev["reason"]
                    assert ev["transcript"][0]["role"] == "assistant"
                    break
    assert reason == "time_limit"
    assert c.get("/api/projects").json()["projects"] == []


# ------------------------------------------------------------------ the happy path
def test_submit_wish_makes_project_first_turn_recording_share_and_receipt(voice_app):
    app, c = voice_app
    seen = []
    app.state.voice_connect = connector(seen=seen)
    with c.websocket_connect("/api/voice/live") as ws:
        events, audio_in = converse(ws, {"client_turn": False, "keep_audio": True, "mode": "fast"})
    kinds = [e["type"] for e in events]
    assert kinds[0] == "ready" and "submitting" in kinds and kinds[-1] == "ended", kinds
    assert audio_in > 0                                                     # Live's voice reached the browser
    caps = [e for e in events if e["type"] == "caption"]
    assert caps[0]["role"] == "assistant" and caps[1]["text"] == "Hi, what would you like to make?"
    assert any(e["role"] == "user" and e["text"] == "A moon night light for my daughter." for e in caps)
    sub = next(e for e in events if e["type"] == "submitted")
    pid = sub["project_id"]
    assert sub["text"].startswith(WISH["wish"]) and "- How old is she? She is six." in sub["text"]
    assert events[-1]["reason"] == "submitted" and events[-1]["project_id"] == pid
    fake = seen[0][1]
    assert fake.texts[0] == GREETING and fake.responses                    # greeted, then answered the tool call

    # the existing chat path ran the first turn on the server (scripted brain), with the voice answers in it
    p = wait_turn(c, pid)
    assert p["chat"][0]["role"] == "user" and p["chat"][0]["text"] == sub["text"]
    assert p["questions_asked"] >= 1 and p["mode"] == "fast"
    v = p["voice"]
    assert v["wish"] == WISH["wish"] and v["questions"] == 1 and v["ended"]
    assert [t["role"] for t in v["turns"]] == ["assistant", "user", "assistant"]
    assert v["turns"][1]["text"] == "A moon night light for my daughter."
    assert v["audio_url"] == f"/api/projects/{pid}/voice/wish.wav" and 0.5 <= v["audio_seconds"] <= 1.4

    # the recording: owner route needs the session, share route works without it
    wav = c.get(v["audio_url"])
    assert wav.status_code == 200 and wav.content[:4] == b"RIFF" and wav.headers["content-type"] == "audio/wav"
    tok = c.post(f"/api/projects/{pid}/share").json()["token"]
    shared = c.get(f"/api/share/{tok}").json()
    assert shared["voice"]["audio_url"] == f"/api/share/{tok}/voice/wish.wav"
    assert shared["voice"]["turns"] == v["turns"]
    c.post("/api/logout")
    c.cookies.clear()
    assert c.get(f"/api/projects/{pid}/voice/wish.wav").status_code == 401
    assert c.get(shared["voice"]["audio_url"]).content == wav.content
    assert c.get(f"/api/share/{tok}/voice/other.wav").status_code == 404

    # the AI receipt: one 'live' call with the token counts Live reported
    live = [t for t in p["usage"]["turns"] if t.get("kind") == "live"]
    assert len(live) == 1
    call = live[0]["calls"][0]
    assert call["kind"] == "live" and call["input_tokens"] == 420 and call["output_tokens"] == 100
    assert call["cost_usd_est"] > 0 and live[0]["input_tokens"] == 420
    assert live[0]["label"] == "Voice wish (Gemini Live)"


def test_talk_from_an_empty_project_uses_it(voice_app):
    app, c = voice_app
    empty = c.post("/api/projects", json={"mode": "fast"}).json()
    with c.websocket_connect("/api/voice/live") as ws:
        events, _ = converse(ws, {"client_turn": True, "project_id": empty["id"]})
    assert next(e for e in events if e["type"] == "submitted")["project_id"] == empty["id"]
    assert [p["id"] for p in c.get("/api/projects").json()["projects"]] == [empty["id"]]
    assert c.get(f"/api/projects/{empty['id']}").json()["voice"]["wish"] == WISH["wish"]
    # a project that already has a conversation is never taken over: the wish gets a new project
    with c.websocket_connect("/api/voice/live") as ws:
        events, _ = converse(ws, {"client_turn": True, "project_id": empty["id"]})
    assert next(e for e in events if e["type"] == "submitted")["project_id"] != empty["id"]
    assert len(c.get("/api/projects").json()["projects"]) == 2


def test_hourly_cap_per_instance(voice_app):
    app, c = voice_app
    app.state.voice_cfg = LiveSettings(sessions_per_hour_total=1, fallback_model=None)
    app.state.voice_connect = connector(make=lambda: FakeLive(after_bytes=None))
    with c.websocket_connect("/api/voice/live") as ws:
        ws.send_text(json.dumps({"type": "start"}))
        assert json.loads(ws.receive_text())["type"] == "ready"
        ws.send_text(json.dumps({"type": "cancel"}))
        while not (lambda m: m.get("text") and json.loads(m["text"])["type"] == "ended")(ws.receive()):
            pass
    c.post("/api/login", json={"key": KEY})                         # a fresh sign-in is not a fresh budget
    with c.websocket_connect("/api/voice/live") as ws:
        ev = json.loads(ws.receive_text())
    assert ev["code"] == "rate_limited"


def test_browser_claims_first_turn(voice_app, monkeypatch):
    app, c = voice_app
    with c.websocket_connect("/api/voice/live") as ws:
        events, _ = converse(ws, {"client_turn": True, "keep_audio": False})
    pid = next(e for e in events if e["type"] == "submitted")["project_id"]
    time.sleep(0.3)
    p = c.get(f"/api/projects/{pid}").json()
    assert p["chat"] == [] and p["voice"]["audio_url"] is None        # no recording without the opt-in
    # the SPA then posts the first turn through the normal chat route (gs.pending)
    text = next(e for e in events if e["type"] == "submitted")["text"]
    with c.stream("POST", f"/api/projects/{pid}/chat", json={"text": text}) as r:
        assert r.status_code == 200
        r.read()
    assert wait_turn(c, pid)["chat"][0]["role"] == "user"


def test_unclaimed_first_turn_runs_on_server(voice_app, monkeypatch):
    app, c = voice_app
    monkeypatch.setattr(voice_live, "CLAIM_WAIT_S", 0.2)
    with c.websocket_connect("/api/voice/live") as ws:
        ws.send_text(json.dumps({"type": "start", "client_turn": True}))
        assert json.loads(ws.receive_text())["type"] == "ready"
        for _ in range(12):
            ws.send_bytes(tone(0.1))
        pid = None
        while True:
            m = ws.receive()
            if m.get("text"):
                ev = json.loads(m["text"])
                if ev["type"] == "submitted":
                    pid = ev["project_id"]                # never claimed
                if ev["type"] == "ended":
                    break
    assert wait_turn(c, pid)["chat"][0]["text"].startswith(WISH["wish"])


def test_end_button_asks_live_to_submit(voice_app):
    app, c = voice_app
    seen = []
    app.state.voice_connect = connector(make=lambda: FakeLive(after_bytes=None, call_on_end=True), seen=seen)
    with c.websocket_connect("/api/voice/live") as ws:
        events, _ = converse(ws, {"client_turn": False}, chunks=3, send_end=True)
    assert "ending" in [e["type"] for e in events] and events[-1]["reason"] == "submitted"
    assert END_NUDGE in seen[0][1].texts


def test_bad_start_and_bad_audio(voice_app):
    _, c = voice_app
    with c.websocket_connect("/api/voice/live") as ws:
        ws.send_text("hello")
        assert json.loads(ws.receive_text())["code"] == "invalid"
    with c.websocket_connect("/api/voice/live") as ws:
        ws.send_text(json.dumps({"type": "start"}))
        assert json.loads(ws.receive_text())["type"] == "ready"
        ws.send_bytes(b"\x00" * 3)                                       # odd byte count: not 16-bit PCM
        while True:
            m = ws.receive()
            if m.get("text") and json.loads(m["text"])["type"] == "ended":
                assert json.loads(m["text"])["reason"] == "bad_audio"
                break


# ------------------------------------------------------------------ helpers
def test_clean_wish_and_first_message():
    assert clean_wish({"wish": " "}) is None
    w, d = clean_wish({"wish": "  a   lamp ", "details": [{"question": "For whom", "answer": "me"}, "junk",
                                                          {"question": "", "answer": "x"}]})
    assert w == "a lamp" and d == [{"question": "For whom", "answer": "me"}]
    assert first_message(w, []) == "a lamp"
    assert first_message(w, d) == "a lamp\n\nFrom my voice chat with Wish Labs:\n- For whom? me"
    assert len(first_message("x" * 1500, [{"question": "q" * 240, "answer": "a" * 300}] * 6)) <= 3900


def test_transcript_joins_pieces_into_turns():
    t = Transcript(time.monotonic())
    t.add("assistant", "Hello,")
    t.add("assistant", " what shall we make?")
    t.close_turn()
    t.add("user", "A lamp")
    t.add("user", " for my desk.")
    t.add("assistant", "Got it.")
    assert [(x["role"], x["text"]) for x in t.clean()] == [
        ("assistant", "Hello, what shall we make?"), ("user", "A lamp for my desk."), ("assistant", "Got it.")]
    assert t.user_text() == "A lamp for my desk."


def test_compact_speech_drops_long_silences():
    pcm = silence(2.0) + tone(1.0) + silence(3.0) + tone(0.5) + silence(1.0)
    out = compact_speech(pcm)
    secs = len(out) / 32000
    assert 1.5 <= secs <= 2.6, secs                    # both phrases, one short pause, no long silences
    assert compact_speech(silence(2.0)) == b""
