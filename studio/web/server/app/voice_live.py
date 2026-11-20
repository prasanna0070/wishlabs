"""Talk to make a wish: one Gemini Live session on Vertex AI behind one browser WebSocket (/api/voice/live).

Browser <-> this server <-> Gemini Live. The browser streams the microphone as 16 kHz 16-bit mono PCM (binary
frames); the server forwards it to Live and streams Live's spoken replies back (24 kHz 16-bit mono PCM, binary
frames) with JSON text frames for captions (Live's input and output transcription), turn markers and the result.
Live asks at most three short follow-up questions, confirms, then calls submit_wish(wish, details). The server turns
that into a normal project through the existing create path (ProjectRepo.create; or the still-empty project the person
started talking from) and its first chat turn through the existing chat code (the browser posts it to /chat like a typed wish; if the browser does not claim it within a few
seconds, the server runs the same Turn itself). The spoken session is stored on the project as `voice` (transcript
turns with roles and times, plus the speaker's recording when they opt in) and shown by the SPA's VoiceWishCard,
also on read-only share links.

Credentials stay on the server (the service account). Limits, all failing closed: studio login required, same-origin
browsers only, one live session per login, LIVE_MAX_SECONDS per session (default 180), LIVE_SESSIONS_PER_HOUR per
login (default 12), LIVE_MAX_CONCURRENT per instance (default 4). LIVE_ENABLED=0 turns the feature off.
"""
from __future__ import annotations

import array
import asyncio
import contextlib
import hashlib
import io
import json
import logging
import os
import threading
import time
import wave
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

from .usage import apply_turn, prices, sum_records
from .util import new_id, now_iso

log = logging.getLogger("gstudio.voice")

IN_RATE = 16000                 # browser -> Live: 16-bit mono PCM
OUT_RATE = 24000                # Live -> browser
IN_MIME = f"audio/pcm;rate={IN_RATE}"
MAX_FRAME_BYTES = 64 * 1024     # one browser audio frame (about 2 s); larger frames are refused
AUDIO_KEY = "voice/wish.wav"    # under projects/<pid>/
CLAIM_WAIT_S = 8.0              # how long the browser has to claim the first chat turn
END_WAIT_S = 15.0               # after End: how long Live has to call submit_wish
GOODBYE_WAIT_S = 8.0            # after submit_wish: how long to let Live say its closing line
NUDGE_BEFORE_END_S = 25.0
# USD per 1M tokens for the AI receipts. Placeholder rates like usage.DEFAULT_PRICES (unverified; MODEL_PRICES_JSON
# overrides): Live bills audio tokens, so these follow the audio-in / audio-out list prices.
LIVE_PRICES = {"gemini-live-2.5-flash-native-audio": {"in": 3.0, "out": 12.0},
               "gemini-3.8-live": {"in": 3.0, "out": 12.0}}

SYSTEM = """You are the voice of Wish Labs, a studio that turns a spoken wish into a real gadget: a 3D-printed shell,
a circuit board with an ESP32-S3, firmware, a build manual and a price in India. Wish Labs makes small indoor,
desk-sized gadgets powered by USB-C: lights, companions, story players, sensors with lights, sounds or small screens.
You are talking with someone who wants to make a gadget. Your only job is to understand their wish well enough for
our design studio, then hand it over by calling submit_wish.

How you talk: short, warm, plain everyday words; one or two short sentences per reply; no lists, no jargon, no
technical terms; never more than one question at a time. Speak English unless the person speaks another language.

Steps:
1. If they have not said what they want yet, ask what they would like to make.
2. Ask at most three short follow-up questions in the whole conversation, one at a time, and only about what changes
   the design: who it is for, what it should do, where it will live. Skip anything they already told you. Never ask
   about materials, electronics, price or other technical details. Ask nothing more when the wish is already clear.
3. Confirm in one sentence: say what you will make and ask if that is right.
4. When they agree (yes, right, go ahead, make it), call submit_wish straight away with the wish in their own words
   and every follow-up question you asked with their answer. Call it once. After it returns, say one short line
   that the design studio is starting, and stop.
If they ask you to just make it, or say they are done, stop asking and call submit_wish with what you have.
If a wish is impossible for us (a car, a drone, anything dangerous or outdoors-only), say so kindly in one sentence
and offer the nearest small gadget we could make.
If they ask about price: first units are estimated at nineteen to twenty-eight thousand rupees, and under ten thousand
rupees at volume, which is modelled; the exact quote comes after the design is built. Never promise anything else
about price, delivery dates or features."""

GREETING = ("(The person just opened the voice wish in the Wish Labs studio. Greet them in one short sentence and "
            "ask what they would like to make.)")
GREETING_WITH_TEXT = ("(The person just opened the voice wish in the Wish Labs studio. They had already typed this "
                      "wish: \"{text}\". In one short sentence, say what you understood and ask your first "
                      "follow-up question, or confirm it if it is already clear.)")
END_NUDGE = ("(The person pressed End. Do not ask anything more: call submit_wish now with the wish and the answers "
             "you have.)")
TIME_NUDGE = ("(About twenty seconds of this call are left. Do not ask anything more: confirm the wish in one "
              "sentence and call submit_wish.)")

SUBMIT_DESCRIPTION = ("Hand the person's wish to the Wish Labs design studio. Call it once, only after they agreed to "
                      "your one-sentence confirmation (or asked you to just make it).")


# ------------------------------------------------------------------ settings and limits
def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class LiveSettings:
    enabled: bool = True
    model: str = "gemini-live-2.5-flash-native-audio"
    fallback_model: str | None = None
    location: str = "us-central1"
    voice: str = "Aoede"
    max_seconds: float = 180.0
    sessions_per_hour: int = 12
    sessions_per_hour_total: int = 60
    max_concurrent: int = 4
    allow_recording: bool = True
    allowed_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "LiveSettings":
        origins = tuple(o.strip().rstrip("/") for o in (os.environ.get("LIVE_ALLOWED_ORIGINS") or "").split(",")
                        if o.strip())
        return cls(
            enabled=_flag("LIVE_ENABLED", True),
            model=os.environ.get("LIVE_MODEL") or cls.model,
            fallback_model=(os.environ.get("LIVE_FALLBACK_MODEL", "gemini-3.8-live") or None),
            location=os.environ.get("LIVE_LOCATION") or cls.location,
            voice=os.environ.get("LIVE_VOICE") or cls.voice,
            max_seconds=float(os.environ.get("LIVE_MAX_SECONDS") or cls.max_seconds),
            sessions_per_hour=int(os.environ.get("LIVE_SESSIONS_PER_HOUR") or cls.sessions_per_hour),
            sessions_per_hour_total=int(os.environ.get("LIVE_SESSIONS_PER_HOUR_TOTAL")
                                        or cls.sessions_per_hour_total),
            max_concurrent=int(os.environ.get("LIVE_MAX_CONCURRENT") or cls.max_concurrent),
            allow_recording=_flag("LIVE_ALLOW_RECORDING", True),
            allowed_origins=origins,
        )


class VoiceHub:
    """Per-instance admission: one active session per login, an hourly cap per login and per instance (everyone
    shares one access key, so a fresh sign-in must not mean a fresh budget), and a concurrency cap."""

    def __init__(self, cfg: LiveSettings):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._active: set[str] = set()
        self._starts: dict[str, deque] = defaultdict(deque)
        self._all: deque = deque()

    def admit(self, login: str) -> tuple[str, str] | None:
        """None when admitted, else (code, message)."""
        with self._lock:
            if login in self._active:
                return ("busy", "A voice wish is already open for this sign-in. Finish or cancel it first.")
            if len(self._active) >= self.cfg.max_concurrent:
                return ("capacity", "Voice is busy right now. Type your wish, or try again in a minute.")
            q = self._starts[login]
            cut = time.time() - 3600
            for d in (q, self._all):
                while d and d[0] < cut:
                    d.popleft()
            if len(q) >= self.cfg.sessions_per_hour or len(self._all) >= self.cfg.sessions_per_hour_total:
                return ("rate_limited", "That is the voice limit for this hour. Type your wish instead.")
            now = time.time()
            q.append(now)
            self._all.append(now)
            self._active.add(login)
            return None

    def release(self, login: str) -> None:
        with self._lock:
            self._active.discard(login)


def login_id(cookie: str) -> str:
    return hashlib.sha256(cookie.encode()).hexdigest()[:16]


def origin_ok(origin: str | None, host: str | None, cfg: LiveSettings, public_url: str | None = None) -> bool:
    """Browsers always send Origin on a WebSocket handshake: it must be this site (or a listed origin), so another
    site cannot open a voice session with a signed-in visitor's cookie. Non-browser clients send none."""
    if not origin:
        return True
    o = origin.strip().rstrip("/")
    allowed = set(cfg.allowed_origins)
    if public_url:
        allowed.add(public_url.rstrip("/"))
    if o in allowed:
        return True
    hostpart = o.split("://", 1)[-1]
    return bool(host) and hostpart == host


# ------------------------------------------------------------------ the Live connection
def live_config(cfg: LiveSettings):
    from google.genai import types

    submit = types.FunctionDeclaration(
        name="submit_wish",
        description=SUBMIT_DESCRIPTION,
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "wish": types.Schema(type=types.Type.STRING, description=(
                    "The wish in the person's own words, first person, one to three sentences, as they would type "
                    "it (e.g. 'I want a night light for my daughter that glows like the moon').")),
                "details": types.Schema(
                    type=types.Type.ARRAY,
                    description="Each follow-up question you asked, with the person's answer in their words.",
                    items=types.Schema(type=types.Type.OBJECT, properties={
                        "question": types.Schema(type=types.Type.STRING),
                        "answer": types.Schema(type=types.Type.STRING)},
                        required=["question", "answer"])),
            },
            required=["wish"]),
    )
    return types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=cfg.voice))),
        system_instruction=types.Content(parts=[types.Part(text=SYSTEM)]),
        tools=[types.Tool(function_declarations=[submit])],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )


_clients: dict[tuple[str, str], Any] = {}
_clients_lock = threading.Lock()


def _client(project: str, location: str):
    key = (project, location)
    with _clients_lock:
        if key not in _clients:
            from google import genai

            _clients[key] = genai.Client(vertexai=True, project=project, location=location)
        return _clients[key]


@contextlib.asynccontextmanager
async def vertex_live(cfg: LiveSettings, gcp_project: str, model: str) -> AsyncIterator[Any]:
    """The real connector: client.aio.live.connect on Vertex AI (service-account credentials)."""
    async with _client(gcp_project, cfg.location).aio.live.connect(model=model, config=live_config(cfg)) as s:
        yield s


Connector = Callable[[LiveSettings, str, str], Any]   # (cfg, gcp_project, model) -> async context manager


class Adapter:
    """The few calls the session makes on a Live session; one place to keep the SDK's call shapes."""

    def __init__(self, s):
        self.s = s

    async def audio(self, pcm: bytes) -> None:
        from google.genai import types

        await self.s.send_realtime_input(audio=types.Blob(data=pcm, mime_type=IN_MIME))

    async def audio_end(self) -> None:
        await self.s.send_realtime_input(audio_stream_end=True)

    async def text(self, text: str) -> None:
        await self.s.send_realtime_input(text=text)

    async def tool_response(self, call_id: str | None, name: str, response: dict) -> None:
        from google.genai import types

        await self.s.send_tool_response(function_responses=[types.FunctionResponse(id=call_id, name=name,
                                                                                    response=response)])

    async def messages(self) -> AsyncIterator[Any]:
        """Every server message until the session closes (receive() stops after each model turn)."""
        while True:
            got = False
            async for m in self.s.receive():
                got = True
                yield m
            if not got:
                return


# ------------------------------------------------------------------ transcript and recording
class Transcript:
    """Caption pieces from Live's transcriptions joined into turns: [{role, text, t}] (t = seconds from start)."""

    def __init__(self, t0: float):
        self.t0 = t0
        self.turns: list[dict] = []
        self.open = False

    def add(self, role: str, piece: str) -> tuple[int, dict] | None:
        if not piece:
            return None
        last = self.turns[-1] if self.turns else None
        if last is not None and self.open and last["role"] == role:
            last["text"] = _join(last["text"], piece)
        else:
            if not piece.strip():
                return None
            last = {"role": role, "text": piece.strip(), "t": round(time.monotonic() - self.t0, 1)}
            self.turns.append(last)
            self.open = True
        return len(self.turns) - 1, last

    def close_turn(self) -> None:
        self.open = False

    def clean(self) -> list[dict]:
        out = []
        for t in self.turns:
            txt = " ".join(t["text"].split())
            if txt:
                out.append({"role": t["role"], "text": txt[:2000], "t": t["t"]})
        return out[:200]

    def user_text(self) -> str:
        return " ".join(t["text"].strip() for t in self.clean() if t["role"] == "user").strip()


def _join(a: str, b: str) -> str:
    """Live's transcription pieces carry their own leading spaces ("Hello," + " how are" + " you?")."""
    return a + b


def compact_speech(pcm: bytes, rate: int = IN_RATE, frame_ms: int = 20, keep_gap_ms: int = 450) -> bytes:
    """The speaker's recording without the long silences while Live talks: frames quieter than the noise floor
    are dropped once a silence runs longer than keep_gap_ms (a short pause between phrases is kept)."""
    if len(pcm) < 4:
        return b""
    s = array.array("h")
    s.frombytes(pcm[: len(pcm) // 2 * 2])
    n = max(1, rate * frame_ms // 1000)
    frames = [s[i:i + n] for i in range(0, len(s), n)]
    levels = [sum(map(abs, f)) / max(1, len(f)) for f in frames]
    srt = sorted(levels)
    floor = srt[len(srt) // 5] if srt else 0
    # 3x the noise floor, but never above a level any voice reaches (a clip that is all speech has a speech "floor")
    thresh = min(max(250.0, floor * 3.0), 900.0)
    gap_frames = max(1, keep_gap_ms // frame_ms)
    out = array.array("h")
    quiet = 0
    voiced_any = False
    for f, lv in zip(frames, levels):
        if lv >= thresh:
            quiet = 0
            voiced_any = True
            out.extend(f)
        else:
            quiet += 1
            if voiced_any and quiet <= gap_frames:
                out.extend(f)
    # trim the trailing kept gap
    tail = gap_frames * n
    if voiced_any and len(out) > tail and quiet > 0:
        del out[len(out) - min(quiet, gap_frames) * n:]
    return out.tobytes()


def wav_bytes(pcm: bytes, rate: int = IN_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


# ------------------------------------------------------------------ the wish -> a normal project
def clean_wish(args: dict | None) -> tuple[str, list[dict]] | None:
    a = args or {}
    wish = " ".join(str(a.get("wish") or "").split())[:1500]
    if len(wish) < 3:
        return None
    details = []
    raw = a.get("details")
    if isinstance(raw, list):
        for d in raw[:6]:
            if not isinstance(d, dict):
                continue
            q = " ".join(str(d.get("question") or "").split())[:240]
            ans = " ".join(str(d.get("answer") or "").split())[:300]
            if q and ans:
                details.append({"question": q, "answer": ans})
    return wish, details


def first_message(wish: str, details: list[dict]) -> str:
    """The first chat turn, as if typed: the wish, then the voice answers (the design agent reads them)."""
    if not details:
        return wish
    lines = [wish, "", "From my voice chat with Wish Labs:"]
    for d in details:
        q = d["question"] if d["question"].endswith("?") else d["question"] + "?"
        lines.append(f"- {q} {d['answer']}")
    return "\n".join(lines)[:3900]


def _blank(p: dict) -> bool:
    return not p.get("chat") and not p.get("turn") and not p.get("voice") and not p.get("versions")


async def create_project(svc, wish: str, details: list[dict], mode: str, voice: dict,
                         target_pid: str | None = None) -> dict:
    """The existing create path (or the still-empty project the person started talking from), then the spoken session
    and the question budget on it."""
    from .projects import NotFound

    p = None
    if target_pid:
        try:
            cur = await svc.repo.aget(target_pid)
            p = cur if _blank(cur) else None
        except NotFound:
            p = None
    if p is None:
        p = await asyncio.to_thread(svc.repo.create, None, mode)

    def mut(pp):
        pp["voice"] = voice
        # the design agent asks at most 3 questions in the whole project; the voice questions count
        pp["questions_asked"] = max(int(pp.get("questions_asked") or 0), min(3, len(details)))
    return await svc.repo.aupdate(p["id"], mut)


async def start_first_turn(app, svc, pid: str, text: str, mode: str) -> bool:
    """The existing chat turn, run by the server (no browser streaming it). False when a turn already ran."""
    from .agent.turn import Turn, TurnError

    p = await svc.repo.aget(pid)
    if p.get("chat") or p.get("turn"):
        return False

    async def quiet(_ev: str, _data: dict) -> None:
        return None

    turn = Turn(svc, pid, quiet)
    try:
        await turn.begin({"text": text, "mode": mode, "follow_build": False})
    except TurnError as e:
        log.info("voice first turn not started for %s: %s", pid, e.message)
        return False

    async def runner():
        try:
            await turn.run()
        except Exception:  # noqa: BLE001
            log.exception("voice first turn failed for %s", pid)

    task = asyncio.create_task(runner())
    bg = getattr(app.state, "bg", None)
    if isinstance(bg, set):
        bg.add(task)
        task.add_done_callback(bg.discard)
    return True


# ------------------------------------------------------------------ one session
class Ended(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class VoiceSession:
    """One browser WebSocket <-> one Live session. send_json/send_bytes/receive are the browser side."""

    def __init__(self, ws, app, svc, cfg: LiveSettings, connect: Connector, opts: dict):
        self.ws, self.app, self.svc, self.cfg, self.connect = ws, app, svc, cfg, connect
        self.mode = opts.get("mode") if opts.get("mode") in ("pro", "fast") else "fast"
        self.keep_audio = bool(opts.get("keep_audio")) and cfg.allow_recording
        self.client_turn = bool(opts.get("client_turn"))
        self.typed = " ".join(str(opts.get("text") or "").split())[:1000]
        tp = str(opts.get("project_id") or "")
        self.target_pid = tp if 3 <= len(tp) <= 40 and tp.replace("_", "").isalnum() else None
        self.t0 = time.monotonic()
        self.started = now_iso()
        self.transcript = Transcript(self.t0)
        self.recording = bytearray()
        self.max_bytes = int(cfg.max_seconds * IN_RATE * 2) + IN_RATE * 2
        self.model = cfg.model
        self.pid: str | None = None
        self.first_text = ""
        self.wish: tuple[str, list[dict]] | None = None
        self.submitted = asyncio.Event()
        self.claimed = asyncio.Event()
        self.end_requested_at: float | None = None
        self.goodbye_deadline: float | None = None
        self.live: Adapter | None = None
        self.send_lock = asyncio.Lock()
        self.closed = False
        self.usage = {"input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "cached_tokens": 0}
        self.usage_msgs = 0

    # browser side ------------------------------------------------------------
    async def send(self, obj: dict) -> None:
        if self.closed:
            return
        try:
            async with self.send_lock:
                await self.ws.send_text(json.dumps(obj))
        except Exception:  # noqa: BLE001 - the browser went away; the session winds down on its own
            self.closed = True

    async def send_audio(self, data: bytes) -> None:
        if self.closed:
            return
        try:
            async with self.send_lock:
                await self.ws.send_bytes(data)
        except Exception:  # noqa: BLE001
            self.closed = True

    # main --------------------------------------------------------------------
    async def run(self) -> str:
        reason = "closed"
        cm = None
        models = [self.cfg.model] + ([self.cfg.fallback_model] if self.cfg.fallback_model and
                                     self.cfg.fallback_model != self.cfg.model else [])
        session = None
        for m in models:
            try:
                cm = self.connect(self.cfg, self.svc.settings.gcp_project, m)
                session = await asyncio.wait_for(cm.__aenter__(), timeout=20)
                self.model = m
                break
            except Exception as e:  # noqa: BLE001 - try the fallback model, then fail closed
                log.warning("live connect failed (%s): %s", m, repr(e)[:300])
                cm = None
        if session is None or cm is None:
            await self.send({"type": "error", "code": "unavailable",
                             "message": "Voice is not available right now. Type your wish instead."})
            return "unavailable"
        self.live = Adapter(session)
        try:
            await self.send({"type": "ready", "model": self.model, "max_seconds": self.cfg.max_seconds,
                             "recording_allowed": self.cfg.allow_recording})
            await self.live.text(GREETING_WITH_TEXT.format(text=self.typed.replace('"', "'")) if self.typed
                                 else GREETING)
            tasks = {asyncio.create_task(self.up(), name="up"), asyncio.create_task(self.down(), name="down"),
                     asyncio.create_task(self.clock(), name="clock")}
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                exc = t.exception()
                if isinstance(exc, Ended):
                    reason = exc.reason
                elif exc is not None:
                    log.warning("voice session task %s failed: %r", t.get_name(), exc)
                    reason = "submitted" if self.submitted.is_set() else "error"
                else:
                    reason = t.result() or reason
            for t in pending:
                t.cancel()
            for t in pending:
                with contextlib.suppress(BaseException):
                    await t
        finally:
            with contextlib.suppress(BaseException):
                await cm.__aexit__(None, None, None)
        if self.submitted.is_set():
            reason = "submitted"
        await self.finish(reason)
        return reason

    async def up(self) -> str:
        """Browser -> Live: microphone frames and control messages."""
        while True:
            msg = await self.ws.receive()
            if msg.get("type") == "websocket.disconnect":
                raise Ended("closed")
            data = msg.get("bytes")
            if data is not None:
                if self.submitted.is_set() or not data:
                    continue
                if len(data) > MAX_FRAME_BYTES or len(data) % 2:
                    raise Ended("bad_audio")
                if len(self.recording) + len(data) > self.max_bytes:
                    raise Ended("time_limit")
                self.recording.extend(data)
                await self.live.audio(data)
                continue
            try:
                ctl = json.loads(msg.get("text") or "{}")
            except ValueError:
                continue
            kind = ctl.get("type") if isinstance(ctl, dict) else None
            if kind == "cancel":
                raise Ended("cancelled")
            if kind == "end" and not self.submitted.is_set() and self.end_requested_at is None:
                self.end_requested_at = time.monotonic()
                with contextlib.suppress(Exception):
                    await self.live.audio_end()
                await self.live.text(END_NUDGE)
                await self.send({"type": "ending"})
            elif kind == "keep_audio":
                self.keep_audio = bool(ctl.get("value")) and self.cfg.allow_recording
            elif kind == "claimed":
                self.claimed.set()

    async def down(self) -> str:
        """Live -> browser: audio, captions, turn markers; submit_wish becomes a project."""
        async for m in self.live.messages():
            sc = getattr(m, "server_content", None)
            if sc is not None:
                if sc.input_transcription and sc.input_transcription.text:
                    await self.caption("user", sc.input_transcription.text)
                if sc.model_turn and sc.model_turn.parts:
                    for part in sc.model_turn.parts:
                        blob = getattr(part, "inline_data", None)
                        if blob is not None and blob.data:
                            await self.send_audio(blob.data)
                if sc.output_transcription and sc.output_transcription.text:
                    await self.caption("assistant", sc.output_transcription.text)
                if sc.interrupted:
                    self.transcript.close_turn()
                    await self.send({"type": "interrupted"})
                if sc.turn_complete:
                    self.transcript.close_turn()
                    await self.send({"type": "turn_complete"})
                    if self.submitted.is_set():
                        return "submitted"
            um = getattr(m, "usage_metadata", None)
            if um is not None:
                self.add_usage(um)
            tc = getattr(m, "tool_call", None)
            if tc is not None and tc.function_calls:
                for fc in tc.function_calls:
                    await self.on_call(fc)
            if getattr(m, "go_away", None) is not None:
                raise Ended("submitted" if self.submitted.is_set() else "time_limit")
        raise Ended("submitted" if self.submitted.is_set() else "live_closed")

    async def caption(self, role: str, piece: str) -> None:
        got = self.transcript.add(role, piece)
        if got:
            i, turn = got
            await self.send({"type": "caption", "i": i, "role": role, "text": turn["text"], "t": turn["t"]})

    async def on_call(self, fc) -> None:
        name = getattr(fc, "name", "")
        if name != "submit_wish":
            await self.live.tool_response(fc.id, name or "unknown", {"error": "Unknown function."})
            return
        if self.submitted.is_set():
            await self.live.tool_response(fc.id, name, {"ok": True, "note": "Already sent; say goodbye."})
            return
        got = clean_wish(dict(fc.args or {}))
        if got is None:
            await self.live.tool_response(fc.id, name, {"ok": False, "error": "The wish is empty. Ask what they "
                                                                               "would like to make."})
            return
        self.wish = got
        await self.send({"type": "submitting"})
        wish, details = got
        self.first_text = first_message(wish, details)
        try:
            p = await create_project(self.svc, wish, details, self.mode, self.voice_record(final=False),
                                     self.target_pid)
        except Exception as e:  # noqa: BLE001 - tell the person, keep the call so they can type instead
            log.exception("voice: project create failed")
            await self.live.tool_response(fc.id, name, {"ok": False, "error": "The studio could not save it."})
            await self.send({"type": "error", "code": "create_failed",
                             "message": f"The project could not be created ({type(e).__name__}). Type your wish "
                                        "instead."})
            return
        self.pid = p["id"]
        self.submitted.set()
        self.goodbye_deadline = time.monotonic() + GOODBYE_WAIT_S
        await self.live.tool_response(fc.id, name, {"ok": True, "result": "The design studio has the wish. Say "
                                                                          "one short goodbye line now."})
        await self.send({"type": "submitted", "project_id": self.pid, "name": p.get("name"),
                         "text": self.first_text, "client_turn": self.client_turn})
        if not self.client_turn:
            await start_first_turn(self.app, self.svc, self.pid, self.first_text, self.mode)
        else:
            task = asyncio.create_task(self.claim_or_run())
            bg = getattr(self.app.state, "bg", None)
            if isinstance(bg, set):
                bg.add(task)
                task.add_done_callback(bg.discard)

    async def claim_or_run(self) -> None:
        """The browser posts the first chat turn itself (live streaming in the UI). If it does not claim it in time,
        the server runs the same turn, so a wish is never left without its first reply."""
        try:
            await asyncio.wait_for(self.claimed.wait(), timeout=CLAIM_WAIT_S)
        except asyncio.TimeoutError:
            if self.pid:
                await start_first_turn(self.app, self.svc, self.pid, self.first_text, self.mode)

    async def clock(self) -> str:
        nudged = False
        while True:
            await asyncio.sleep(0.25)
            now = time.monotonic()
            el = now - self.t0
            if self.goodbye_deadline is not None and now > self.goodbye_deadline:
                raise Ended("submitted")
            if self.submitted.is_set():
                continue
            if self.end_requested_at is not None and now - self.end_requested_at > END_WAIT_S:
                raise Ended("no_wish")
            if not nudged and el > self.cfg.max_seconds - NUDGE_BEFORE_END_S and self.cfg.max_seconds > 40:
                nudged = True
                await self.send({"type": "time_left", "seconds": int(self.cfg.max_seconds - el)})
                with contextlib.suppress(Exception):
                    await self.live.text(TIME_NUDGE)
            if el > self.cfg.max_seconds:
                raise Ended("time_limit")

    def add_usage(self, um) -> None:
        g = lambda a: int(getattr(um, a, 0) or 0)  # noqa: E731
        self.usage_msgs += 1
        self.usage["input_tokens"] += g("prompt_token_count")
        self.usage["output_tokens"] += g("response_token_count") or g("candidates_token_count")
        self.usage["thinking_tokens"] += g("thoughts_token_count")
        self.usage["cached_tokens"] += g("cached_content_token_count")

    def usage_turn(self) -> dict:
        """The session as one 'live' call in the project's AI receipts (usage.apply_turn shape)."""
        rec = {"model": self.model, "kind": "live", **self.usage, "images": 0,
               "seconds": round(time.monotonic() - self.t0, 2), "usage_messages": self.usage_msgs}
        rate = prices().get(self.model) or LIVE_PRICES.get(self.model) or {}
        rec["cost_usd_est"] = round(rec["input_tokens"] / 1e6 * rate.get("in", 0.0) + (
            rec["output_tokens"] + rec["thinking_tokens"]) / 1e6 * rate.get("out", 0.0), 6)
        return {"turn_id": new_id("t"), "ts": now_iso(), "action": "voice_wish", "kind": "live",
                "label": "Voice wish (Gemini Live)", "mode": self.mode, "calls": [rec], **sum_records([rec])}

    # wrap-up ---------------------------------------------------------------------
    def voice_record(self, final: bool) -> dict:
        wish, details = self.wish or ("", [])
        rec = {
            "kind": "gemini-live",
            "model": self.model,
            "location": self.cfg.location,
            "voice": self.cfg.voice,
            "started": self.started,
            "seconds": round(time.monotonic() - self.t0, 1),
            "wish": wish,
            "details": details,
            "questions": len(details),
            "turns": self.transcript.clean(),
            "audio_url": None,
            "audio_seconds": None,
        }
        if final:
            rec["ended"] = now_iso()
        return rec

    async def finish(self, reason: str) -> None:
        if self.pid:
            rec = self.voice_record(final=True)
            if self.keep_audio and self.recording:
                try:
                    speech = await asyncio.to_thread(compact_speech, bytes(self.recording))
                    if len(speech) >= IN_RATE:          # at least half a second of speech
                        key = f"projects/{self.pid}/{AUDIO_KEY}"
                        await asyncio.to_thread(self.svc.store.write_bytes, key, wav_bytes(speech), "audio/wav")
                        rec["audio_url"] = f"/api/projects/{self.pid}/{AUDIO_KEY}"
                        rec["audio_seconds"] = round(len(speech) / (IN_RATE * 2), 1)
                except Exception:  # noqa: BLE001 - the transcript still counts
                    log.exception("voice: recording not stored for %s", self.pid)

            usage = self.usage_turn()

            def mut(pp):
                pp["voice"] = rec
                try:
                    apply_turn(pp, usage)
                except Exception:  # noqa: BLE001 - receipts must never block the record
                    log.exception("voice usage logging failed")
            try:
                await self.svc.repo.aupdate(self.pid, mut)
            except Exception:  # noqa: BLE001
                log.exception("voice: final record not stored for %s", self.pid)
        await self.send({"type": "ended", "reason": reason, "project_id": self.pid,
                         "transcript": self.transcript.clean(), "user_text": self.transcript.user_text()})
