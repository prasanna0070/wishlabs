"""End-to-end check of the voice wish against a running server with real Gemini Live (run it on the VM).

Synthesises the person's lines with Gemini-TTS (Cloud Text-to-Speech, 16 kHz LINEAR16), signs in, opens
/api/voice/live, streams the microphone in real time (silence between lines, like an open mic), says the next line
after each of Live's turns, and checks that submit_wish made a project with its first chat turn, the voice record,
the stored recording (owner and share link) and the 'live' AI receipt. Evidence goes to $OUT.

  BASE=http://127.0.0.1:9201 KEY_FILE=<0600 file> OUT=_out/voice-e2e python web/server/scripts/voice_e2e.py
  python web/server/scripts/voice_e2e.py --mic-wav <path>     # only write the fake-microphone WAV for voice-ui.cjs
Costs one Live session (about a minute) and a few TTS calls.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import sys
import time
import urllib.request
import wave
from http.cookiejar import CookieJar
from pathlib import Path

BASE = os.environ.get("BASE", "http://127.0.0.1:9201").rstrip("/")
OUT = Path(os.environ.get("OUT", "_out/voice-e2e"))
PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-gcp-project")
TTS_MODEL = os.environ.get("E2E_TTS_MODEL", "gemini-2.5-flash-tts")
TTS_VOICE = os.environ.get("E2E_TTS_VOICE", "Achird")
LINES = [
    "Hi! I want a night light for my daughter that glows like the moon.",
    "She is six. It sits on her bedside table, and it should glow softly while she falls asleep.",
    "Yes, that's right. Please go ahead and make it.",
    "Yes, go ahead and make it now.",
    "That is everything. Please make it.",
]
RATE = 16000
FRAME = RATE * 2 // 10            # 100 ms of 16-bit mono


def _token() -> str:
    import google.auth
    from google.auth.transport.requests import Request

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(Request())
    return creds.token


def tts(text: str) -> bytes:
    body = json.dumps({"input": {"text": text, "prompt": "Say this naturally, like a parent talking to a shop."},
                       "voice": {"languageCode": "en-IN", "name": TTS_VOICE, "model_name": TTS_MODEL},
                       "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": RATE}}).encode()
    req = urllib.request.Request("https://texttospeech.googleapis.com/v1beta1/text:synthesize", data=body, headers={
        "Authorization": "Bearer " + _token(), "x-goog-user-project": PROJECT, "Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                wav = base64.b64decode(json.loads(r.read())["audioContent"])
            with wave.open(io.BytesIO(wav)) as w:
                assert w.getframerate() == RATE and w.getsampwidth() == 2 and w.getnchannels() == 1
                return w.readframes(w.getnframes())
        except Exception:  # noqa: BLE001
            if attempt == 2:
                raise
            time.sleep(2)
    raise RuntimeError("unreachable")


def wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


class Http:
    def __init__(self):
        self.jar = CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, method: str, path: str, body: dict | None = None, raw: bool = False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        with self.op.open(req, timeout=60) as r:
            b = r.read()
            return (r.status, dict(r.headers), b) if raw else json.loads(b or b"null")

    def cookie(self) -> str:
        return "; ".join(f"{c.name}={c.value}" for c in self.jar)


async def converse(http: Http, lines: list[bytes], log: list) -> dict:
    import websockets

    url = BASE.replace("http", "ws", 1) + "/api/voice/live"
    hdr = {"Cookie": http.cookie()}
    try:
        ws = await websockets.connect(url, additional_headers=hdr, max_size=None)
    except TypeError:
        ws = await websockets.connect(url, extra_headers=hdr, max_size=None)
    out_audio = bytearray()
    state = {"next": 0, "speak_at": None, "ended": None, "submitted": None, "t0": time.monotonic()}
    queue: list[bytes] = []

    def t() -> float:
        return round(time.monotonic() - state["t0"], 2)

    async def mic():
        while state["ended"] is None:
            if not queue and state["speak_at"] is not None and time.monotonic() >= state["speak_at"]:
                state["speak_at"] = None
                if state["next"] < len(lines):
                    pcm = lines[state["next"]]
                    log.append({"t": t(), "say": LINES[state["next"]]})
                    state["next"] += 1
                    queue.extend(pcm[i:i + FRAME] for i in range(0, len(pcm), FRAME))
            frame = queue.pop(0) if queue else b"\x00\x00" * (FRAME // 2)
            if len(frame) % 2:
                frame = frame[:-1]
            try:
                await ws.send(frame)
            except Exception:  # noqa: BLE001 - the server closed the socket
                return
            await asyncio.sleep(0.1)

    async def speaker():
        async for msg in ws:
            if isinstance(msg, bytes):
                out_audio.extend(msg)
                continue
            ev = json.loads(msg)
            ev["_t"] = t()
            log.append(ev)
            kind = ev.get("type")
            if kind == "caption":
                print(f"[{ev['_t']:6.1f}] {ev['role']:9s} {ev['text']}", flush=True)
            else:
                print(f"[{ev['_t']:6.1f}] {kind} {json.dumps({k: v for k, v in ev.items() if k not in ('type', '_t', 'transcript')})[:240]}", flush=True)
            if kind == "turn_complete" and state["submitted"] is None:
                state["speak_at"] = time.monotonic() + 0.8
            elif kind == "submitted":
                state["submitted"] = ev
            elif kind in ("ended", "error"):
                state["ended"] = ev
                if kind == "ended":
                    return

    await ws.send(json.dumps({"type": "start", "client_turn": False, "keep_audio": True, "mode": "fast"}))
    tasks = [asyncio.create_task(mic()), asyncio.create_task(speaker())]
    try:
        await asyncio.wait_for(tasks[1], timeout=200)
    finally:
        state["ended"] = state["ended"] or {"type": "timeout"}
        for x in tasks:
            x.cancel()
        await ws.close()
    (OUT / "live-reply-24k.wav").write_bytes(wav(bytes(out_audio), 24000))
    return {"submitted": state["submitted"], "ended": state["ended"], "reply_audio_s": round(len(out_audio) / 48000, 1),
            "lines_said": state["next"]}


def mic_wav(path: str) -> int:
    """A WAV for Chromium's fake microphone (--use-file-for-fake-audio-capture=<wav>%noloop): the lines with pauses
    long enough for Live to connect, greet and answer in between."""
    lines = [tts(x) for x in LINES[:4]]
    gap = lambda s: b"\x00\x00" * int(RATE * s)  # noqa: E731
    pcm = gap(9) + lines[0] + gap(8) + lines[1] + gap(9) + lines[2] + gap(10) + lines[3] + gap(30)
    Path(path).write_bytes(wav(pcm, RATE))
    print(json.dumps({"mic_wav": path, "seconds": round(len(pcm) / (RATE * 2), 1)}))
    return 0


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--mic-wav":
        return mic_wav(sys.argv[2])
    OUT.mkdir(parents=True, exist_ok=True)
    key = Path(os.environ["KEY_FILE"]).read_text().strip()
    print("synthesising", len(LINES), "lines with", TTS_MODEL, flush=True)
    lines = [tts(x) for x in LINES]
    (OUT / "user-lines-16k.wav").write_bytes(wav(b"".join(x + b"\x00\x00" * RATE for x in lines), RATE))
    http = Http()
    http.call("POST", "/api/login", {"key": key})
    log: list = []
    res = asyncio.run(converse(http, lines, log))
    (OUT / "events.json").write_text(json.dumps(log, indent=1))
    checks: dict[str, object] = {"session": {k: v for k, v in res.items() if k != "ended"},
                                 "end_reason": (res["ended"] or {}).get("reason")}
    sub = res["submitted"]
    ok = bool(sub and sub.get("project_id"))
    if ok:
        pid = sub["project_id"]
        p = {}
        for _ in range(60):                       # the first chat turn runs on the server
            p = http.call("GET", f"/api/projects/{pid}")
            if not p.get("turn") and p.get("chat") and p["chat"][-1]["role"] == "assistant":
                break
            time.sleep(1)
        v = p.get("voice") or {}
        live = [x for x in (p.get("usage") or {}).get("turns") or [] if x.get("kind") == "live"]
        checks.update({
            "project_id": pid, "project_name": p.get("name"), "first_user_message": (p.get("chat") or [{}])[0].get("text"),
            "first_reply_kind": (p.get("chat") or [{}])[-1].get("kind"), "voice_wish": v.get("wish"),
            "voice_details": v.get("details"), "voice_turns": len(v.get("turns") or []), "voice_model": v.get("model"),
            "audio_url": v.get("audio_url"), "audio_seconds": v.get("audio_seconds"),
            "receipt": live[0]["calls"][0] if live else None,
        })
        ok = ok and bool(p.get("chat")) and bool(v.get("turns")) and bool(live)
        if v.get("audio_url"):
            st, h, b = http.call("GET", v["audio_url"], raw=True)
            (OUT / "stored-wish.wav").write_bytes(b)
            checks["owner_audio"] = {"status": st, "type": h.get("content-type"), "bytes": len(b)}
            tok = http.call("POST", f"/api/projects/{pid}/share")["token"]
            shared = Http().call("GET", f"/api/share/{tok}")
            st2, _, b2 = Http().call("GET", shared["voice"]["audio_url"], raw=True)
            checks["share_audio"] = {"url": shared["voice"]["audio_url"], "status": st2, "same_bytes": b2 == b}
            ok = ok and st == 200 and st2 == 200 and b2 == b
    checks["ok"] = ok
    (OUT / "result.json").write_text(json.dumps(checks, indent=1, default=str))
    print(json.dumps(checks, indent=1, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
