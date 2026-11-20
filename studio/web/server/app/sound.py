"""Every gadget gets its own sound: Lyria on Vertex AI composes a short theme from the design, and the strongest
musical onset in that theme becomes the gadget's signature chime.

Flow (SoundComposer.start / run):
  1. prompt_for(spec, project): a music prompt from the design's name, tagline, behaviour template, audience and
     colours. Deterministic, stored and shown in the UI exactly as it was sent.
  2. Lyria (env LYRIA_MODEL, default lyria-002, Vertex AI :predict in LYRIA_LOCATION, default us-central1) answers
     with about 32.8 s of 48 kHz stereo 16-bit WAV, base64 in predictions[0].bytesBase64Encoded. The WAV arrives
     double-wrapped: the outer RIFF's data chunk holds a second complete RIFF/WAVE file (parse_wav unwraps it).
  3. compose(): the theme (at most 30 s, leading silence trimmed, faded out), the chime (CHIME_S seconds from the
     strongest onset, faded), a device chime (16 kHz mono 16-bit WAV under 100 KB, for the gadget's speaker) and 64
     waveform peaks for the UI. Theme and chime are MP3 when lameenc is installed, else WAV.
  4. Files go to projects/<pid>/sound/v<n>/<sid>/ in the store (beside the version folders, so the copies that seed
     a new version never carry them into its export); project.json["sound"] carries the metadata, and each Lyria
     call is one usage record (kind "music") in project.json["usage"] for the AI receipts.

It costs money, so it needs the studio login (routes/sound.py), runs one job per project at a time, has a
per-session hourly limit and a store-backed daily cap for the whole service, and every failure ends closed
(no model call, status "error"). Google lists Lyria 2 at USD 0.06 per 30 s of generated music, so one generation
(one ~32.8 s clip) costs about USD 0.06-0.07.

It runs by itself once, after a design's first successful build (maybe_auto, called from builds.finalize), and again
whenever the owner presses the button.
"""
from __future__ import annotations

import asyncio
import base64
import colorsys
import io
import json
import logging
import os
import random
import re
import struct
import threading
import time
import wave
from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone

from .projects import NotFound, find_concept, find_version, vkey
from .usage import apply_turn, sum_records
from .util import new_id, now_iso, parse_iso

log = logging.getLogger("gstudio.sound")

PROVIDER = "Lyria on Vertex AI"
COST_USD_EST = 0.06
COST_BASIS = ("Google lists Lyria 2 on Vertex AI at USD 0.06 per 30 s of generated music; one generation is one "
              "clip of about 32.8 s.")
THEME_MAX_S = 30.0
DEVICE_RATE = 16000
DEVICE_MAX_S = 3.0
DEVICE_MAX_BYTES = 100_000             # "under 100 KB": 3.0 s at 16 kHz mono 16-bit is 96,044 bytes
STALE_S = 300.0                        # a job older than this is dead (instance restarted mid-job)
HISTORY = 5
PEAKS = 64
MIME = {"theme.mp3": "audio/mpeg", "theme.wav": "audio/wav", "chime.mp3": "audio/mpeg", "chime.wav": "audio/wav",
        "device-chime.wav": "audio/wav", "lyria.wav": "audio/wav"}
SID_RE = re.compile(r"^snd_[a-z0-9]{6,24}$")


# ------------------------------------------------------------------ config
def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _num(name: str, default: float, lo: float, hi: float) -> float:
    try:
        v = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, v))


@dataclass
class SoundConfig:
    enabled: bool = False
    model: str = "lyria-002"
    location: str = "us-central1"
    timeout_s: float = 120.0
    daily_cap: int = 40                  # generations per UTC day for the whole service (store-backed)
    session_hourly: int = 5              # generations per signed-in browser session per hour (per instance)
    chime_s: float = 2.4

    @classmethod
    def from_env(cls, settings) -> "SoundConfig":
        return cls(
            enabled=_flag("SOUND", getattr(settings, "agent", "") == "gemini"),
            model=(os.environ.get("LYRIA_MODEL") or "lyria-002").strip(),
            location=(os.environ.get("LYRIA_LOCATION") or "us-central1").strip(),
            timeout_s=_num("LYRIA_TIMEOUT_S", 120.0, 20.0, 300.0),
            daily_cap=int(_num("SOUND_DAILY_CAP", 40, 0, 1000)),
            session_hourly=int(_num("SOUND_SESSION_HOURLY", 5, 0, 100)),
            chime_s=_num("SOUND_CHIME_S", 2.4, 1.0, 3.0),
        )


# ------------------------------------------------------------------ errors
class SoundError(RuntimeError):
    def __init__(self, message: str, status: int = 502, code: str = "unavailable"):
        super().__init__(message)
        self.status = status
        self.code = code


class LyriaRejected(SoundError):
    """Lyria refused the prompt (HTTP 400, or no audio back): retried once with the prompt that carries no user text."""

    def __init__(self, message: str):
        super().__init__(message, 422, "invalid")


class _CapReached(Exception):
    pass


class _Busy(Exception):
    pass


# ------------------------------------------------------------------ prompt
STYLES = {
    "story_player": ("dreamy, cosy and tender, like a soft lullaby", "music box, celesta, soft felt piano and warm "
                     "string pads", 72),
    "desk_companion": ("playful, friendly and curious", "marimba, pizzicato strings, glockenspiel and light hand "
                       "percussion", 100),
    "ambient_light": ("calm, airy and glowing", "soft synth pads, wind chimes and warm electric piano", 70),
    "notifier_clock": ("bright, clear and cheerful", "vibraphone, kalimba and a crisp woodblock", 96),
    "sensor_display": ("focused, clean and curious", "gentle analog synth arpeggios and soft bells", 90),
}
DEFAULT_STYLE = ("friendly, optimistic and warm", "kalimba, soft piano and light bells", 92)
NEGATIVE = "vocals, singing, lyrics, speech, harsh noise, distortion, heavy drums, aggressive, scary, dissonant"
NEGATIVE_CHILD = NEGATIVE + ", loud crashes, sudden bangs"


def _clean(s, n: int) -> str:
    """Plain, single-line text for a music prompt: printable characters only, no quotes or brackets, at most n chars."""
    t = re.sub(r"[^\w\s,.;:'&/+-]", " ", str(s or ""), flags=re.UNICODE)
    t = " ".join(t.replace("_", " ").split())
    if len(t) > n:
        t = t[:n].rsplit(" ", 1)[0].rstrip(",;:- ")
    return t


def _hex_rgb(h) -> tuple[float, float, float] | None:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(h or "").strip())
    if not m:
        return None
    v = int(m.group(1), 16)
    return ((v >> 16) & 255) / 255, ((v >> 8) & 255) / 255, (v & 255) / 255


def colour_words(hex_colour) -> list[str]:
    """Timbre words for a colour: hue -> warm / cool / fresh / dreamy, lightness -> soft / deep, saturation ->
    vivid / muted. The body colour drives the theme's tone colour."""
    rgb = _hex_rgb(hex_colour)
    if rgb is None:
        return []
    h, l, s = colorsys.rgb_to_hls(*rgb)
    deg = h * 360
    out: list[str] = []
    if s < 0.12:
        out.append("neutral, understated")
    elif deg < 70 or deg >= 330:
        out.append("warm")
    elif deg < 160:
        out.append("fresh, organic")
    elif deg < 255:
        out.append("cool, airy, shimmering")
    else:
        out.append("dreamy")
    if l >= 0.75:
        out.append("light and soft")
    elif l <= 0.3:
        out.append("deep and velvety")
    if s >= 0.6 and 0.3 < l < 0.75:
        out.append("vivid")
    return out


def prompt_for(spec: dict, project: dict | None = None, *, safe: bool = False) -> dict:
    """The Lyria prompt for a design. safe=True leaves out every user-written text (name, tagline, concept), for the
    retry after Lyria refuses a prompt."""
    spec = spec or {}
    project = project or {}
    beh = spec.get("behaviour") or {}
    template = str(beh.get("template") or "")
    mood, instruments, bpm = STYLES.get(template, DEFAULT_STYLE)
    aud = spec.get("audience") or {}
    child = bool(aud.get("child_directed"))
    age = aud.get("age_years")
    colors = spec.get("colors") or {}
    tone = colour_words(colors.get("body"))
    names = [_clean(colors.get(k), 24) for k in ("body_name", "detail_name") if colors.get(k)]
    has_light = any("LED" in str((f or {}).get("module") or "") for f in spec.get("features") or [])
    parts: list[str] = []
    if safe:
        parts.append("Instrumental signature theme for a small, friendly handmade gadget.")
    else:
        name = _clean(spec.get("name") or project.get("name") or "a small gadget", 48)
        tagline = _clean(spec.get("tagline") or "", 140)
        concept = find_concept(project, project.get("selected_concept")) if project else None
        blurb = _clean((concept or {}).get("blurb") or "", 120)
        about = tagline or blurb
        parts.append(f"Instrumental signature theme for a small handmade gadget called {name}"
                     + (f": {about[0].lower() + about[1:]}." if about else "."))
    who = ""
    if child:
        who = (f", gentle and soothing for a {int(age)}-year-old child" if isinstance(age, (int, float)) and 0 < age < 18
               else ", gentle and soothing for a young child")
    parts.append(f"Mood: {mood}{who}.")
    parts.append(f"Instruments: {instruments}.")
    if tone:
        parts.append(f"Tone colour: {', '.join(tone)}"
                     + (f", like its {' and '.join(names)} colours." if names else "."))
    if has_light:
        parts.append("A soft glowing shimmer runs underneath.")
    parts.append(f"Around {bpm} BPM. Open with a clear, bright two-second motif that works on its own as the "
                 "gadget's chime, then let the theme grow gently and end softly. Clean, modern, high-quality product "
                 "sound design.")
    prompt = " ".join(parts)
    return {"prompt": prompt[:900], "negative_prompt": NEGATIVE_CHILD if child else NEGATIVE,
            "source": "safe_template" if safe else "template", "bpm": bpm, "template": template or None}


# ------------------------------------------------------------------ Lyria client
class Lyria:
    """Vertex AI :predict for Lyria models (lyria-002). Blocking; call through asyncio.to_thread."""

    def __init__(self, settings, config: SoundConfig):
        self.settings = settings
        self.config = config
        self._token: tuple[str, float] | None = None
        self._lock = threading.Lock()

    def url(self) -> str:
        loc = self.config.location
        host = "aiplatform.googleapis.com" if loc == "global" else f"{loc}-aiplatform.googleapis.com"
        return (f"https://{host}/v1/projects/{self.settings.gcp_project}/locations/{loc}/publishers/google/models/"
                f"{self.config.model}:predict")

    def _auth(self) -> str:
        with self._lock:
            if self._token and time.time() < self._token[1]:
                return self._token[0]
            import google.auth
            import google.auth.transport.requests

            creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            creds.refresh(google.auth.transport.requests.Request())
            self._token = (creds.token, time.time() + 1800)
            return creds.token

    def generate(self, prompt: str, negative_prompt: str, seed: int) -> bytes:
        import urllib.error
        import urllib.request

        body = json.dumps({"instances": [{"prompt": prompt, "negative_prompt": negative_prompt, "seed": int(seed)}],
                           "parameters": {}}).encode()
        last = ""
        for attempt in range(3):
            req = urllib.request.Request(self.url(), data=body, method="POST", headers={
                "Authorization": "Bearer " + self._auth(), "Content-Type": "application/json",
                "x-goog-user-project": self.settings.gcp_project})
            try:
                with urllib.request.urlopen(req, timeout=self.config.timeout_s) as r:
                    j = json.load(r)
                preds = j.get("predictions") or []
                b64 = (preds[0] or {}).get("bytesBase64Encoded") or (preds[0] or {}).get("audioContent") if preds else None
                if not b64:
                    raise LyriaRejected("Lyria returned no audio for this prompt.")
                return base64.b64decode(b64)
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:240]
                if e.code == 400:
                    raise LyriaRejected(f"Lyria refused the prompt: {detail}") from None
                if 400 <= e.code < 500 and e.code != 429:
                    raise SoundError(f"Lyria HTTP {e.code}: {detail}") from None
                last = f"HTTP {e.code} {detail}"
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last = str(e)
            if attempt < 2:
                time.sleep(3.0 * (attempt + 1))
        raise SoundError(f"Lyria did not answer: {last}"[:400])


# ------------------------------------------------------------------ audio
def parse_wav(raw: bytes):
    """PCM WAV -> (float32 array [frames, channels] in [-1, 1], rate). Unwraps Lyria's nested RIFF and clamps chunk
    sizes that claim more bytes than exist."""
    import numpy as np

    buf = raw
    fmt = data = None
    for _ in range(4):
        if len(buf) < 12 or buf[:4] != b"RIFF" or buf[8:12] != b"WAVE":
            raise SoundError("Lyria did not return a WAV file.")
        fmt = data = None
        off = 12
        while off + 8 <= len(buf):
            cid = buf[off:off + 4]
            size = struct.unpack("<I", buf[off + 4:off + 8])[0]
            chunk = buf[off + 8:off + 8 + size]
            if cid == b"fmt ":
                fmt = chunk
            elif cid == b"data":
                data = chunk
                break
            off += 8 + size + (size & 1)
        if fmt is None or data is None or len(fmt) < 16:
            raise SoundError("The WAV from Lyria has no audio data.")
        if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
            buf = data
            continue
        break
    tag, ch, rate, _, block, bits = struct.unpack("<HHIIHH", fmt[:16])
    if tag == 0xFFFE and len(fmt) >= 26:
        tag = struct.unpack("<H", fmt[24:26])[0]
    if ch < 1 or rate < 8000 or block < 1:
        raise SoundError("The WAV from Lyria has a bad header.")
    n = len(data) // block
    data = data[:n * block]
    if tag == 1 and bits == 16:
        x = np.frombuffer(data, "<i2").astype(np.float32) / 32768.0
    elif tag == 1 and bits == 24:
        b = np.frombuffer(data, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = (np.where(v & 0x800000, v - 0x1000000, v) / 8388608.0).astype(np.float32)
    elif tag == 1 and bits == 32:
        x = (np.frombuffer(data, "<i4") / 2147483648.0).astype(np.float32)
    elif tag == 3 and bits == 32:
        x = np.frombuffer(data, "<f4").astype(np.float32)
    else:
        raise SoundError(f"Unsupported WAV encoding ({tag}, {bits} bit).")
    return x.reshape(-1, ch), int(rate)


def wav_bytes(x, rate: int) -> bytes:
    import numpy as np

    if x.ndim == 1:
        x = x[:, None]
    pcm = np.clip(np.round(x * 32767.0), -32768, 32767).astype("<i2")
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(int(x.shape[1]))
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm.tobytes())
    return out.getvalue()


def mp3_bytes(x, rate: int, kbps: int = 128) -> bytes | None:
    """MP3 via lameenc when it is installed (requirements.txt); None otherwise (callers fall back to WAV)."""
    try:
        import lameenc  # type: ignore
    except ImportError:
        return None
    import numpy as np

    if x.ndim == 1:
        x = x[:, None]
    pcm = np.clip(np.round(x * 32767.0), -32768, 32767).astype("<i2")
    enc = lameenc.Encoder()
    enc.set_bit_rate(kbps)
    enc.set_in_sample_rate(int(rate))
    enc.set_channels(int(x.shape[1]))
    enc.set_quality(2)
    return bytes(enc.encode(pcm.tobytes()) + enc.flush())


def lowpass(x, rate: int, cutoff: float, taps: int = 101):
    import numpy as np

    n = np.arange(taps) - (taps - 1) / 2
    fc = cutoff / rate
    h = 2 * fc * np.sinc(2 * fc * n) * np.blackman(taps)
    h /= h.sum()
    if x.ndim == 1:
        return np.convolve(x, h, mode="same").astype(np.float32)
    return np.stack([np.convolve(x[:, c], h, mode="same") for c in range(x.shape[1])], axis=1).astype(np.float32)


def resample(x, rate: int, new_rate: int):
    import numpy as np

    if rate == new_rate:
        return x.astype(np.float32)
    y = lowpass(x, rate, 0.45 * new_rate) if new_rate < rate else x
    if rate % new_rate == 0:
        return y[::rate // new_rate].astype(np.float32)
    t = np.arange(int(len(x) * new_rate / rate)) * (rate / new_rate)
    if y.ndim == 1:
        return np.interp(t, np.arange(len(y)), y).astype(np.float32)
    return np.stack([np.interp(t, np.arange(len(y)), y[:, c]) for c in range(y.shape[1])], axis=1).astype(np.float32)


def fade(y, rate: int, fade_in_s: float, fade_out_s: float, out_shape: str = "cos"):
    """In-place linear fade-in and a cosine (or exponential-looking cos^2) fade-out."""
    import numpy as np

    n = len(y)
    a = min(n, int(rate * fade_in_s))
    if a > 1:
        w = np.linspace(0.0, 1.0, a, dtype=np.float32)
        y[:a] *= w[:, None] if y.ndim == 2 else w
    b = min(n, int(rate * fade_out_s))
    if b > 1:
        u = np.linspace(0.0, 1.0, b, dtype=np.float32)
        w = 0.5 * (1 + np.cos(np.pi * u)) if out_shape == "cos" else np.cos(0.5 * np.pi * u) ** 2
        w = w.astype(np.float32)
        y[n - b:] *= w[:, None] if y.ndim == 2 else w
    return y


def normalize(y, peak_db: float = -1.0, rms_max_db: float | None = None):
    import numpy as np

    peak = float(np.abs(y).max()) if len(y) else 0.0
    if peak < 1e-4:
        raise SoundError("Lyria returned silence.")
    g = 10 ** (peak_db / 20) / peak
    if rms_max_db is not None:
        rms = float(np.sqrt(np.mean(np.square(y, dtype=np.float64))))
        if rms * g > 10 ** (rms_max_db / 20):
            g = 10 ** (rms_max_db / 20) / rms
    return (y * g).astype(np.float32), round(20 * np.log10(g), 2)


def onset_curve(mono, rate: int):
    """Spectral flux (log-magnitude increase summed over bins) on ~24 kHz audio, 1024-sample frames, 256 hop.
    Returns (times in seconds, flux)."""
    import numpy as np

    step = max(1, int(round(rate / 24000)))
    if step > 1:
        mono = lowpass(mono, rate, 0.45 * rate / step)[::step]
    r = rate / step
    n_fft, hop = 1024, 256
    if len(mono) < 2 * n_fft:
        return np.zeros(0), np.zeros(0)
    frames = np.lib.stride_tricks.sliding_window_view(mono, n_fft)[::hop] * np.hanning(n_fft).astype(np.float32)
    mag = np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32)
    lm = np.log1p(100.0 * mag)
    flux = np.maximum(0.0, np.diff(lm, axis=0)).sum(axis=1)
    times = (np.arange(1, len(lm)) * hop + n_fft / 2) / r
    return times, flux


def strongest_onset(x, rate: int, chime_s: float) -> float:
    """The start time of the chime: the onset with the highest spectral-flux peak weighted by how loud the next
    0.8 s is (a hit into silence makes a poor chime), with room for a full chime after it."""
    import numpy as np

    mono = x.mean(axis=1) if x.ndim == 2 else x
    dur = len(mono) / rate
    times, flux = onset_curve(mono, rate)
    if len(flux) < 3:
        return 0.0
    hop = max(1, int(rate * 0.01))
    nfr = len(mono) // hop
    env = np.sqrt(np.mean(np.square(mono[:nfr * hop].reshape(nfr, hop), dtype=np.float64), axis=1) + 1e-12)
    peaks = np.nonzero((flux[1:-1] >= flux[:-2]) & (flux[1:-1] > flux[2:]))[0] + 1
    best, best_t = -1.0, 0.0
    for k in peaks:
        t = float(times[k])
        if t < 0.15 or t > dur - chime_s - 0.1:
            continue
        a, b = int(t / 0.01), int((t + 0.8) / 0.01)
        post = float(env[a:b].mean()) if b > a and a < len(env) else 0.0
        score = float(flux[k]) * post
        if score > best:
            best, best_t = score, t
    if best < 0:
        return 0.0
    return refine_onset(mono, rate, best_t)


def refine_onset(mono, rate: int, t: float) -> float:
    """Moves a frame-resolution onset to the first 2 ms block, in [t - 80 ms, t + 40 ms], whose level reaches half
    of the local maximum: the attack starts there."""
    import numpy as np

    blk = max(1, int(rate * 0.002))
    a = max(0, int((t - 0.08) * rate))
    b = min(len(mono), int((t + 0.04) * rate))
    seg = mono[a:b]
    n = len(seg) // blk
    if n < 3:
        return t
    e = np.sqrt(np.mean(np.square(seg[:n * blk].reshape(n, blk), dtype=np.float64), axis=1))
    hit = np.nonzero(e >= 0.5 * e.max())[0]
    return (a + int(hit[0]) * blk) / rate if len(hit) else t


def peaks_of(y, n: int = PEAKS) -> list[float]:
    import numpy as np

    mono = np.abs(y.mean(axis=1) if y.ndim == 2 else y)
    if len(mono) < n:
        return [0.0] * n
    seg = len(mono) // n
    v = mono[:seg * n].reshape(n, seg).max(axis=1)
    m = float(v.max()) or 1.0
    return [round(float(x) / m, 3) for x in v]


def compose(raw: bytes, chime_s: float = 2.4) -> dict:
    """Lyria WAV -> theme, chime, device chime. Returns {files: {name: bytes}, theme: {...}, chime: {...},
    device_chime: {...}, source: {...}}."""
    import numpy as np

    x, rate = parse_wav(raw)
    if x.shape[1] > 2:
        x = x[:, :2]
    src_s = len(x) / rate
    if src_s < 3.0:
        raise SoundError("Lyria returned a clip that is too short.")
    files: dict[str, bytes] = {"lyria.wav": wav_bytes(x, rate)}
    # theme: trim leading silence (below -50 dBFS), at most THEME_MAX_S, short fade-in, cosine fade-out
    lvl = np.abs(x).max(axis=1)
    loud = np.nonzero(lvl > 10 ** (-50 / 20))[0]
    if not len(loud):
        raise SoundError("Lyria returned silence.")
    start = max(0, int(loud[0]) - int(0.02 * rate))
    theme = x[start:start + int(THEME_MAX_S * rate)].copy()
    theme_s = len(theme) / rate
    fade(theme, rate, 0.01, min(3.0, 0.12 * theme_s))
    theme, theme_gain = normalize(theme, -1.0, -14.0)
    # chime: CHIME_S from the strongest onset (15 ms pre-roll), 5 ms fade-in, the last 55 % fades out
    chime_s = min(3.0, max(1.0, float(chime_s)))
    onset = strongest_onset(x, rate, chime_s)
    c0 = max(0, int((onset - 0.015) * rate))
    chime = x[c0:c0 + int(chime_s * rate)].copy()
    fade(chime, rate, 0.005, 0.55 * len(chime) / rate, out_shape="cos2")
    chime, _ = normalize(chime, -1.0)
    # device chime: mono, 16 kHz, 16-bit, at most DEVICE_MAX_S, peak -3 dBFS (the firmware scales volume itself)
    dev = resample(chime.mean(axis=1), rate, DEVICE_RATE)[:int(DEVICE_MAX_S * DEVICE_RATE)].copy()
    fade(dev, DEVICE_RATE, 0.003, 0.02)
    dev, _ = normalize(dev, -3.0)
    files["device-chime.wav"] = wav_bytes(dev, DEVICE_RATE)
    if len(files["device-chime.wav"]) >= DEVICE_MAX_BYTES:
        raise SoundError("The device chime came out too large.")
    tm = mp3_bytes(theme, rate)
    if tm is not None:
        files["theme.mp3"] = tm
        files["chime.mp3"] = mp3_bytes(chime, rate) or wav_bytes(chime, rate)
        theme_name, chime_name = "theme.mp3", "chime.mp3"
    else:                           # no encoder: a 24 kHz theme keeps the download near 3 MB
        files["theme.wav"] = wav_bytes(resample(theme, rate, rate // 2) if rate >= 44100 else theme,
                                       rate // 2 if rate >= 44100 else rate)
        files["chime.wav"] = wav_bytes(chime, rate)
        theme_name, chime_name = "theme.wav", "chime.wav"
    return {
        "files": files,
        "theme": {"file": theme_name, "mime": MIME[theme_name], "seconds": round(theme_s, 2),
                  "bytes": len(files[theme_name]), "peaks": peaks_of(theme), "gain_db": theme_gain},
        "chime": {"file": chime_name, "mime": MIME[chime_name], "seconds": round(len(chime) / rate, 2),
                  "bytes": len(files[chime_name]), "onset_s": round(onset, 3)},
        "device_chime": {"file": "device-chime.wav", "mime": "audio/wav", "seconds": round(len(dev) / DEVICE_RATE, 2),
                         "sample_rate": DEVICE_RATE, "channels": 1, "bits": 16,
                         "bytes": len(files["device-chime.wav"])},
        "source": {"file": "lyria.wav", "seconds": round(src_s, 2), "sample_rate": rate, "channels": int(x.shape[1])},
    }


# ------------------------------------------------------------------ composer
def _busy(s: dict | None, now: float | None = None) -> bool:
    s = s or {}
    job = s.get("job") or {}
    if s.get("status") != "generating" or not job:
        return False
    started = parse_iso(job.get("started"))
    return bool(started) and (now or time.time()) - started.timestamp() < STALE_S


def records(s: dict | None) -> list[dict]:
    s = s or {}
    return [r for r in [s.get("current")] + list(s.get("history") or []) if isinstance(r, dict) and r.get("id")]


def skey(pid: str, n: int, sid: str, name: str = "") -> str:
    """Store key of a composition's folder (or one file in it): projects/<pid>/sound/v<n>/<sid>/<name>."""
    return f"projects/{pid}/sound/v{int(n)}/{sid}" + (f"/{name}" if name else "")


def file_key(p: dict, sid: str, name: str) -> str | None:
    """Store key of one sound file of a project, or None when the id or name is not one of its compositions."""
    if not SID_RE.match(sid or "") or name not in MIME:
        return None
    rec = next((r for r in records(p.get("sound")) if r.get("id") == sid), None)
    if not rec or name not in (rec.get("files") or []):
        return None
    return skey(p["id"], int(rec["version"]), sid, name)


def effective_status(s: dict | None, now: float | None = None) -> str:
    """'none' | 'generating' | 'ready' | 'error'. A 'generating' job older than STALE_S died with its instance, so it
    reads as 'error' (and a new one may start)."""
    s = s or {}
    st = s.get("status") or ("ready" if s.get("current") else "none")
    if st == "generating" and not _busy(s, now):
        return "error"
    return st


def usage_record(meta: dict, ok: bool = True) -> dict:
    """One Lyria call as a usage turn (kind "music"), the shape agent turns use (usage.apply_turn)."""
    call = {"model": meta.get("model"), "kind": "music", "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0,
            "cached_tokens": 0, "images": 0, "seconds": meta.get("model_seconds"), "cost_usd_est": COST_USD_EST,
            "audio_seconds": (meta.get("source") or {}).get("seconds"), "ok": ok}
    return {"turn_id": meta.get("id"), "ts": now_iso(), "action": "sound", "label": "Gadget sound (Lyria)",
            "mode": None, "calls": [call], **sum_records([call])}


def _apply_usage(pp: dict, meta: dict, ok: bool = True) -> None:
    try:
        apply_turn(pp, usage_record(meta, ok))
    except Exception:  # noqa: BLE001 - usage logging must never lose the sound
        log.exception("sound usage logging failed")


class SoundComposer:
    def __init__(self, svc, config: SoundConfig | None = None, lyria=None):
        self.svc = svc
        self.config = config or SoundConfig.from_env(svc.settings)
        self.lyria = lyria or Lyria(svc.settings, self.config)
        self.tasks: set[asyncio.Task] = set()
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, bytes] = OrderedDict()

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled)

    # ---- limits
    def _session_window(self, key: str) -> deque:
        q = self._hits[key]
        now = time.time()
        while q and now - q[0] > 3600:
            q.popleft()
        return q

    def session_left(self, key: str) -> int:
        with self._lock:
            return max(0, self.config.session_hourly - len(self._session_window(key)))

    def session_take(self, key: str) -> None:
        with self._lock:
            self._session_window(key).append(time.time())

    def charge_budget(self) -> int:
        """Counts one generation against today's cap (UTC day) in the store. Fails closed: cap reached -> 429,
        store unavailable -> 503, and no Lyria call is made in either case."""
        cap = int(self.config.daily_cap)
        key = f"sound/budget/{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"

        def fn(cur):
            cur = cur if isinstance(cur, dict) else {"count": 0}
            if int(cur.get("count") or 0) >= cap:
                raise _CapReached()
            cur["count"] = int(cur.get("count") or 0) + 1
            cur["cap"] = cap
            cur["updated"] = now_iso()
            return cur
        try:
            doc = self.svc.store.update_json(key, fn, default={"count": 0})
        except _CapReached:
            raise SoundError("Today's sound budget is used up. Try again tomorrow.", 429, "rate_limited") from None
        except Exception:  # noqa: BLE001 - fail closed
            log.exception("sound budget unavailable")
            raise SoundError("Sound generation is unavailable right now.", 503, "unavailable") from None
        return int(doc.get("count") or 0)

    # ---- start (the owner's button; the automatic run after the first build is maybe_auto below)
    async def start(self, pid: str, n: int | None = None, *, trigger: str = "user", session: str | None = None) -> dict:
        if not self.enabled:
            raise SoundError("Sound generation is switched off on this server.", 503, "unavailable")
        p = await self.svc.repo.aget(pid)
        if n is not None:
            v = find_version(p, n)
        else:
            cur = find_version(p, p.get("current_version"))
            v = cur if cur and cur.get("state") == "done" else next(
                (x for x in sorted(p.get("versions") or [], key=lambda x: -x["n"]) if x.get("state") == "done"), None)
        if not v or v.get("state") != "done":
            raise SoundError("Build the design first: its sound is composed from a finished version.", 409,
                             "conflict")
        if _busy(p.get("sound")):
            raise SoundError("A sound is already being composed for this design.", 409, "conflict")
        if session is not None and self.session_left(session) <= 0:
            raise SoundError(f"That is {self.config.session_hourly} sounds this hour from this browser. "
                             "Try again later.", 429, "rate_limited")
        await asyncio.to_thread(self.charge_budget)
        job = {"id": new_id("snd", 12), "version": v["n"], "trigger": trigger, "started": now_iso()}

        def mut(pp):
            s = pp.get("sound") or {}
            if _busy(s):
                raise _Busy()
            pp["sound"] = {**s, "status": "generating", "job": job, "error": None}
        try:
            p = await self.svc.repo.aupdate(pid, mut)
        except _Busy:
            raise SoundError("A sound is already being composed for this design.", 409, "conflict") from None
        if session is not None:
            self.session_take(session)
        self.spawn(pid, v["n"], job["id"], charge=False)
        return p["sound"]

    def spawn(self, pid: str, n: int, sid: str, *, charge: bool) -> asyncio.Task:
        task = asyncio.create_task(self.run(pid, n, sid, charge=charge))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def _spec(self, pid: str, n: int) -> dict:
        st = self.svc.store
        inp = await asyncio.to_thread(st.read_json, f"projects/{pid}/inputs/v{n}.json") or {}
        return inp.get("spec") or await asyncio.to_thread(st.read_json, vkey(pid, n, "spec.json")) or {}

    async def run(self, pid: str, n: int, sid: str, *, charge: bool = False) -> None:
        t0 = time.time()
        billed: dict | None = None             # set once Lyria has answered with audio (that call is paid for)
        try:
            if charge:
                await asyncio.to_thread(self.charge_budget)
            spec = await self._spec(pid, n)
            p = await self.svc.repo.aget(pid)
            pr = prompt_for(spec, p)
            seed = random.randint(1, 2 ** 31 - 1)
            budget = self.config.timeout_s * 2 + 30
            try:
                raw = await asyncio.wait_for(asyncio.to_thread(
                    self.lyria.generate, pr["prompt"], pr["negative_prompt"], seed), budget)
            except LyriaRejected as e:
                log.warning("lyria refused the prompt for %s (%s); retrying without user text", pid, e)
                pr = prompt_for(spec, p, safe=True)
                raw = await asyncio.wait_for(asyncio.to_thread(
                    self.lyria.generate, pr["prompt"], pr["negative_prompt"], seed), budget)
            t_model = time.time() - t0
            billed = {"id": sid, "model": self.config.model, "model_seconds": round(t_model, 1)}
            out = await asyncio.to_thread(compose, raw, self.config.chime_s)
            base = skey(pid, n, sid)
            for name, data in out["files"].items():
                await asyncio.to_thread(self.svc.store.write_bytes, f"{base}/{name}", data, MIME[name])
            root = f"/api/projects/{pid}/sound/{sid}"
            meta = {
                "id": sid, "version": n, "created": now_iso(), "provider": PROVIDER, "model": self.config.model,
                "location": self.config.location, "prompt": pr["prompt"], "negative_prompt": pr["negative_prompt"],
                "prompt_source": pr["source"], "seed": seed, "bpm": pr["bpm"],
                "theme": {**out["theme"], "url": f"{root}/{out['theme']['file']}"},
                "chime": {**out["chime"], "url": f"{root}/{out['chime']['file']}"},
                "device_chime": {**out["device_chime"], "url": f"{root}/device-chime.wav"},
                "source": out["source"], "files": sorted(out["files"]),
                "model_seconds": round(t_model, 1), "elapsed_s": round(time.time() - t0, 1),
                "cost_usd_est": COST_USD_EST, "cost_basis": COST_BASIS,
            }
            await asyncio.to_thread(self.svc.store.write_json, f"{base}/sound.json", meta)

            def done(pp):
                s = pp.get("sound") or {}
                mine = (s.get("job") or {}).get("id") == sid
                prev = [r for r in records(s) if r.get("id") != sid]
                if mine or s.get("status") != "generating":
                    meta["trigger"] = (s.get("job") or {}).get("trigger") if mine else None
                    pp["sound"] = {"status": "ready", "job": None, "error": None, "current": meta,
                                   "count": int(s.get("count") or 0) + 1, "history": prev[:HISTORY]}
                else:                   # a newer job owns the status; keep this one in the history only
                    s["history"] = ([meta] + list(s.get("history") or []))[:HISTORY]
                    pp["sound"] = s
                _apply_usage(pp, meta)
            await self.svc.repo.aupdate(pid, done)
            billed = None
            log.info("sound %s for %s v%s: %s s (model %s s), theme %s s, chime at %s s", sid, pid, n,
                     meta["elapsed_s"], meta["model_seconds"], meta["theme"]["seconds"], meta["chime"]["onset_s"])
        except Exception as exc:  # noqa: BLE001 - any failure becomes status "error", never a crash
            if isinstance(exc, SoundError):
                msg = str(exc)
            elif isinstance(exc, asyncio.TimeoutError):
                msg = "Lyria took too long to answer."
            else:
                msg = "The sound could not be composed."
            log.exception("sound %s for %s v%s failed", sid, pid, n) if not isinstance(exc, SoundError) else \
                log.warning("sound %s for %s v%s failed: %s", sid, pid, n, exc)

            def fail(pp):
                s = pp.get("sound") or {}
                if (s.get("job") or {}).get("id") == sid:
                    pp["sound"] = {**s, "status": "error", "job": None, "error": msg[:300], "error_at": now_iso()}
                if billed:              # Lyria answered (and billed) but the sound could not be finished
                    _apply_usage(pp, billed, ok=False)
            try:
                await self.svc.repo.aupdate(pid, fail)
            except NotFound:
                pass
            except Exception:  # noqa: BLE001
                log.exception("could not record the sound failure for %s", pid)

    # ---- files
    def read(self, key: str) -> bytes | None:
        """Sound files never change once written (each composition has its own id), so a small LRU is safe."""
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                return hit
        data = self.svc.store.read_bytes(key)
        if data is not None and len(data) <= 8 << 20:
            with self._lock:
                self._cache[key] = data
                while sum(len(v) for v in self._cache.values()) > 32 << 20 and len(self._cache) > 1:
                    self._cache.popitem(last=False)
        return data


# ------------------------------------------------------------------ wiring
def sound_composer(svc) -> SoundComposer:
    """The service's one SoundComposer, made on first use (Services stays untouched)."""
    c = getattr(svc, "_sound_composer", None)
    if c is None:
        c = SoundComposer(svc)
        svc._sound_composer = c
    return c


class _Skip(Exception):
    pass


async def maybe_auto(svc, pid: str, n: int) -> bool:
    """Called when version n finished. Composes the design's sound once, after its FIRST successful build: only when
    the project has no sound state yet and no other version is done. The daily cap is charged inside the job (an
    exhausted budget ends as status "error", no Lyria call). Never raises: a build must not fail because of music."""
    try:
        c = sound_composer(svc)
        if not c.enabled:
            return False
        job = {"id": new_id("snd", 12), "version": n, "trigger": "auto", "started": now_iso()}

        def mut(p):
            v = find_version(p, n)
            if p.get("sound") or not v or v.get("state") != "done":
                raise _Skip()
            if any(x.get("state") == "done" and x.get("n") != n for x in p.get("versions") or []):
                raise _Skip()
            p["sound"] = {"status": "generating", "job": job, "error": None}
        try:
            await svc.repo.aupdate(pid, mut)
        except (_Skip, NotFound):
            return False
        c.spawn(pid, n, job["id"], charge=True)
        log.info("sound for %s: composing automatically after the first successful build (v%s)", pid, n)
        return True
    except Exception:  # noqa: BLE001
        log.exception("automatic sound for %s v%s could not start", pid, n)
        return False


def public_state(p: dict, c: SoundComposer, session: str | None = None) -> dict:
    """What GET /api/projects/{pid}/sound answers: the project's sound plus the switches and limits the UI shows."""
    s = p.get("sound") or None
    st = effective_status(s)
    out = dict(s) if s else None
    if out is not None and st != out.get("status"):
        out.update(status=st, job=None, error=out.get("error") or "The last composition stopped before it finished.")
    return {"enabled": c.enabled, "status": st, "sound": out, "provider": PROVIDER, "model": c.config.model,
            "cost_usd_est": COST_USD_EST, "cost_basis": COST_BASIS, "session_hourly": c.config.session_hourly,
            "session_left": c.session_left(session) if session else None}
