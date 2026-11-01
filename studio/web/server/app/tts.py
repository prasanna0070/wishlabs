"""Name clips for the story card (MAJ-33): a name the parent typed, spoken in the story pack's voice.

Same call as the engine's story pack (engine/gstudio/firmware/tts.py, which the web image does not bundle): Gemini
TTS through Cloud Text-to-Speech v1beta1 text:synthesize, LINEAR16 at 24 kHz, the same name prompt as
stories.py, peak-normalised to -3 dBFS. The device accepts a 16-bit mono WAV of at most 64 KB
(POST /api/name_clip on the gadget), so leading and trailing silence is trimmed and, only if the clip is still too
big, it is resampled to 16 kHz. Kept in a small in-memory cache only (no copy is stored).
"""
from __future__ import annotations

import array
import asyncio
import base64
import hashlib
import io
import json
import logging
import re
import threading
import time
import wave
from collections import OrderedDict

log = logging.getLogger("gstudio.tts")

ENDPOINT = "https://texttospeech.googleapis.com/v1beta1/text:synthesize"
MODELS = ("gemini-2.5-flash-tts", "gemini-2.5-pro-tts")
RATE = 24000
MAX_BYTES = 64 * 1024
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z '.\-]{0,31}$")          # same rule as firmware settings._clean_name
VOICE_RE = re.compile(r"^[A-Za-z]{3,20}$")
NAME_STYLE = "Say only this name, warmly and softly, as if it is the hero of a bedtime story."
DEFAULT_VOICE = "Achernar"
LANGUAGE = "en-IN"


class TtsError(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def clean_name(name: str) -> str:
    v = " ".join(str(name or "").split())
    if not NAME_RE.match(v) or not any(c.isalpha() for c in v):
        raise TtsError("Use 1 to 32 English letters, spaces, apostrophes, hyphens or full stops.", 422)
    return v


def name_slug(name: str) -> str:
    """Same rule as gs::name_slug in the firmware and stories.name_slug (letters lower-cased, spaces and hyphens
    to one '_', everything else dropped), so the clip lands at /names/<slug>.wav."""
    out: list[str] = []
    for c in name:
        if c.isascii() and c.isalpha():
            out.append(c.lower())
        elif c in " -" and out and out[-1] != "_":
            out.append("_")
    return "".join(out).strip("_")


def _pcm(wav_bytes: bytes) -> tuple[array.array, int]:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        ch, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.readframes(w.getnframes())
    if width != 2:
        raise TtsError(f"unexpected sample width {width}")
    pcm = array.array("h", frames)
    if ch == 2:
        pcm = array.array("h", [(pcm[i] + pcm[i + 1]) // 2 for i in range(0, len(pcm) - 1, 2)])
    return pcm, rate


def _wav(pcm: array.array, rate: int) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return out.getvalue()


def shape_clip(wav_bytes: bytes, peak_dbfs: float = -3.0, pad_ms: int = 60, gate_dbfs: float = -45.0) -> tuple[bytes, dict]:
    """Normalise, trim silence, fit in MAX_BYTES. Returns (wav, info)."""
    pcm, rate = _pcm(wav_bytes)
    peak = max((abs(x) for x in pcm), default=0)
    if peak <= 0:
        raise TtsError("the voice returned silence")
    g = 32767 * (10 ** (peak_dbfs / 20)) / peak
    pcm = array.array("h", [max(-32768, min(32767, int(x * g))) for x in pcm])
    gate = 32767 * (10 ** (gate_dbfs / 20))
    loud = [i for i, x in enumerate(pcm) if abs(x) > gate]
    pad = int(rate * pad_ms / 1000)
    a, b = max(0, loud[0] - pad), min(len(pcm), loud[-1] + pad + 1)
    pcm = pcm[a:b]
    data = _wav(pcm, rate)
    if len(data) > MAX_BYTES:
        new_rate = 16000
        k = rate / new_rate
        res = array.array("h", [pcm[min(len(pcm) - 1, int(i * k))] for i in range(int(len(pcm) / k))])
        pcm, rate = res, new_rate
        data = _wav(pcm, rate)
    if len(data) > MAX_BYTES:
        raise TtsError("that name takes too long to say for a 64 KB clip; use a shorter name", 422)
    return data, {"sample_rate": rate, "bytes": len(data), "seconds": round(len(pcm) / rate, 2)}


class NameVoice:
    """Synthesises and caches (in memory, 128 clips) name clips."""

    def __init__(self, settings, synth=None):
        self.settings = settings
        self._synth = synth            # tests: callable(text, style, voice, model) -> wav bytes
        self._cache: OrderedDict[str, tuple[bytes, dict]] = OrderedDict()
        self._lock = threading.Lock()
        self._token: tuple[str, float] | None = None
        self.model: str | None = None

    def _auth(self) -> str:
        if self._token and time.time() < self._token[1]:
            return self._token[0]
        import google.auth
        import google.auth.transport.requests

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
        self._token = (creds.token, time.time() + 1800)
        return creds.token

    def _call(self, model: str, text: str, style: str, voice: str) -> bytes:
        import urllib.error
        import urllib.request

        body = json.dumps({"input": {"text": text, "prompt": style},
                           "voice": {"languageCode": LANGUAGE, "name": voice, "model_name": model},
                           "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": RATE}}).encode()
        req = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
            "Authorization": "Bearer " + self._auth(), "x-goog-user-project": self.settings.gcp_project,
            "Content-Type": "application/json"})
        last = ""
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    raw = base64.b64decode(json.load(r)["audioContent"])
                return raw if raw[:4] == b"RIFF" else _wav(array.array("h", raw), RATE)
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:200]
                if 400 <= e.code < 500 and e.code != 429:
                    raise TtsError(f"{model}: HTTP {e.code} {detail}") from None
                last = f"HTTP {e.code} {detail}"
            except (urllib.error.URLError, TimeoutError) as e:
                last = str(e)
            time.sleep(1.5 * (attempt + 1))
        raise TtsError(f"{model}: {last}")

    def _synth_blocking(self, name: str, voice: str) -> tuple[bytes, dict]:
        if self._synth is not None:
            return self._synth(name, NAME_STYLE, voice, "test"), {"model": "test"}
        errors = []
        for m in ([self.model] if self.model else []) + [x for x in MODELS if x != self.model]:
            try:
                raw = self._call(m, name, NAME_STYLE, voice)
                self.model = m
                return raw, {"model": m}
            except TtsError as e:
                errors.append(str(e))
        raise TtsError("no text-to-speech model answered: " + " | ".join(errors)[:400])

    async def clip(self, name: str, voice: str | None = None) -> tuple[bytes, dict]:
        name = clean_name(name)
        voice = voice if voice and VOICE_RE.match(voice) else DEFAULT_VOICE
        key = hashlib.sha256(f"{voice}|{LANGUAGE}|{name.lower()}".encode()).hexdigest()
        with self._lock:
            hit = self._cache.get(key)
            if hit:
                self._cache.move_to_end(key)
                return hit[0], {**hit[1], "cached": True}
        raw, meta = await asyncio.to_thread(self._synth_blocking, name, voice)
        data, info = await asyncio.to_thread(shape_clip, raw)
        info = {**info, **meta, "voice": voice, "language": LANGUAGE, "name": name, "slug": name_slug(name),
                "cached": False}
        with self._lock:
            self._cache[key] = (data, info)
            while len(self._cache) > 128:
                self._cache.popitem(last=False)
        return data, info
