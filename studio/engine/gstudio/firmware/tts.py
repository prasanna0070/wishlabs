"""Gemini text-to-speech through Google Cloud Text-to-Speech (v1beta1 text:synthesize, voice.model_name).

Used only when ctx.options["stories_audio"] is true. Output: WAV, 16-bit PCM mono at 24 kHz (LINEAR16),
peak-normalised to -3 dBFS so stories are not louder than the chimes. Results are cached by a hash of
(model, voice, language, style, text) so a rebuild does not pay for the same audio twice.
"""
from __future__ import annotations

import array
import base64
import hashlib
import io
import json
import os
import shutil
import urllib.error
import urllib.request
import wave
from pathlib import Path
from typing import Optional

ENDPOINT = "https://texttospeech.googleapis.com/v1beta1/text:synthesize"
# Newest first; the first that answers is used (gemini-2.5-flash-tts is the brief's default).
MODELS = ("gemini-2.5-flash-tts", "gemini-2.5-pro-tts", "gemini-2.5-flash-lite-preview-tts")
DEFAULT_PROJECT = "your-gcp-project"
RATE = 24000


class TtsError(RuntimeError):
    pass


def _token_and_project(project: Optional[str]) -> tuple[str, str]:
    import google.auth
    import google.auth.transport.requests

    creds, default_project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    proj = project or os.environ.get("GOOGLE_CLOUD_PROJECT") or default_project or DEFAULT_PROJECT
    return creds.token, proj


def _normalise(wav_bytes: bytes, peak_dbfs: float = -3.0) -> bytes:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        ch, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.readframes(w.getnframes())
    if width != 2:
        raise TtsError(f"unexpected sample width {width}")
    pcm = array.array("h", frames)
    if ch == 2:
        pcm = array.array("h", [(pcm[i] + pcm[i + 1]) // 2 for i in range(0, len(pcm) - 1, 2)])
    peak = max((abs(x) for x in pcm), default=0)
    if peak > 0:
        target = 32767 * (10 ** (peak_dbfs / 20))
        g = target / peak
        pcm = array.array("h", [max(-32768, min(32767, int(x * g))) for x in pcm])
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return out.getvalue()


class GeminiTts:
    def __init__(self, voice: str = "Achernar", language: str = "en-IN", project: Optional[str] = None,
                 cache_dir: Optional[Path] = None, model: Optional[str] = None):
        self.voice, self.language = voice, language
        self.token, self.project = _token_and_project(project)
        self.cache = Path(cache_dir) if cache_dir else None
        self.model = model or self._probe()

    def _call(self, model: str, text: str, style: str) -> bytes:
        body = json.dumps({
            "input": {"text": text, "prompt": style},
            "voice": {"languageCode": self.language, "name": self.voice, "model_name": model},
            "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": RATE},
        }).encode()
        req = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
            "Authorization": "Bearer " + self.token, "x-goog-user-project": self.project,
            "Content-Type": "application/json"})
        last = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    return base64.b64decode(json.load(r)["audioContent"])
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:300]
                if 400 <= e.code < 500 and e.code != 429:
                    raise TtsError(f"{model}: HTTP {e.code} {detail}") from None
                last = f"HTTP {e.code} {detail}"
            except (urllib.error.URLError, TimeoutError) as e:
                last = str(e)
            import time
            time.sleep(2 ** attempt)
        raise TtsError(f"{model}: failed after retries: {last}")

    def _probe(self) -> str:
        errors = []
        for m in MODELS:
            try:
                self._call(m, "Hello.", "Say it softly.")
                return m
            except TtsError as e:
                errors.append(str(e))
        raise TtsError("no Gemini TTS model answered: " + " | ".join(errors))

    def synth(self, text: str, style: str, path: Path) -> None:
        key = hashlib.sha256(json.dumps([self.model, self.voice, self.language, style, text]).encode()).hexdigest()
        if self.cache:
            hit = self.cache / f"{key}.wav"
            if hit.exists():
                shutil.copyfile(hit, path)
                return
        raw = self._call(self.model, text, style)
        if raw[:4] != b"RIFF":  # LINEAR16 normally comes with a WAV header; wrap raw PCM if not
            out = io.BytesIO()
            with wave.open(out, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                w.writeframes(raw)
            raw = out.getvalue()
        data = _normalise(raw)
        path.write_bytes(data)
        if self.cache:
            self.cache.mkdir(parents=True, exist_ok=True)
            (self.cache / f"{key}.wav").write_bytes(data)


__all__ = ["GeminiTts", "TtsError", "MODELS"]
