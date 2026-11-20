"""Gadget sound (Lyria on Vertex AI) with Lyria mocked: the prompt, the audio cuts, the routes (login, share, device),
the limits that keep it from spending money, the usage record and the automatic run after the first build."""
from __future__ import annotations

import functools
import io
import json
import struct
import threading
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from app.projects import vkey
from app.routes.sound import byte_range
from app.sound import (DEVICE_MAX_BYTES, LyriaRejected, SoundComposer, SoundConfig, SoundError, compose,
                       effective_status, parse_wav, prompt_for, wav_bytes)
from app.util import now_iso
from conftest import KEY

STUDIO = Path(__file__).resolve().parents[3]


def spec_of(name: str = "desk-buddy") -> dict:
    return json.loads((STUDIO / "examples" / name / "spec.json").read_text())


def synth_wav(seconds: float = 6.0, rate: int = 48000, hit: float = 2.0, nested: bool = True) -> bytes:
    """A quiet pad with one loud plucked note at `hit` s, as 16-bit stereo WAV. nested=True wraps it the way Lyria
    does: the outer RIFF's data chunk is itself a complete RIFF/WAVE file."""
    t = np.arange(int(seconds * rate)) / rate
    x = 0.05 * np.sin(2 * np.pi * 220 * t) * np.minimum(1.0, t / 0.3)
    m = t >= hit
    x[m] += 0.8 * np.sin(2 * np.pi * 880 * (t[m] - hit)) * np.exp(-(t[m] - hit) * 3)
    inner = wav_bytes(np.stack([x, x], axis=1).astype(np.float32), rate)
    if not nested:
        return inner
    fmt = struct.pack("<HHIIHH", 1, 2, rate, rate * 4, 4, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(inner)) + inner
    return b"RIFF" + struct.pack("<I", len(body)) + body


class FakeLyria:
    def __init__(self, wav: bytes | None = None, fail: Exception | None = None, refuse_first: bool = False,
                 gate: threading.Event | None = None):
        self.wav = wav if wav is not None else synth_wav()
        self.fail, self.refuse_first, self.gate = fail, refuse_first, gate
        self.calls: list[tuple[str, str]] = []

    def generate(self, prompt: str, negative: str, seed: int) -> bytes:
        self.calls.append((prompt, negative))
        if self.gate is not None:
            assert self.gate.wait(10)
        if self.refuse_first and len(self.calls) == 1:
            raise LyriaRejected("Lyria refused the prompt: blocked")
        if self.fail is not None:
            raise self.fail
        return self.wav


def use_fake(client, lyria: FakeLyria | None = None, **cfg) -> FakeLyria:
    svc = client.app.state.svc
    lyria = lyria or FakeLyria()
    svc._sound_composer = SoundComposer(svc, SoundConfig(**{"enabled": True, **cfg}), lyria=lyria)
    return lyria


def done_project(client, name: str = "Mochi Desk Buddy", spec: str = "desk-buddy", state: str = "done") -> str:
    svc = client.app.state.svc
    p = svc.repo.create(name)
    pid = p["id"]
    svc.store.write_json(f"projects/{pid}/inputs/v1.json", {"spec": spec_of(spec)})
    svc.store.write_json(vkey(pid, 1, "status.json"), {"state": state, "percent": 100, "stages": []})

    def mut(pp):
        pp["versions"] = [{"n": 1, "created": now_iso(), "label": "Build", "source": "build", "state": state,
                           "finalized": True, "started": now_iso(), "ended": now_iso(), "summary": "",
                           "percent": 100, "error": None, "thumb": None}]
        pp["current_version"] = 1
        pp["stage"] = "ready"
    svc.repo.update(pid, mut)
    return pid


def wait_sound(client, pid: str, timeout: float = 15.0, until: tuple = ("none", "ready", "error")) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = client.get(f"/api/projects/{pid}/sound").json()
        if st["status"] in until:
            return st
        time.sleep(0.05)
    raise AssertionError("the sound did not finish")


# ------------------------------------------------------------------ prompt
def test_prompt_from_the_design():
    pr = prompt_for(spec_of("desk-buddy"), {"name": "x"})
    p = pr["prompt"]
    assert "Mochi Desk Buddy" in p and "marimba" in p and "peach" in p and "Instrumental" in p
    assert pr["bpm"] == 100 and pr["template"] == "desk_companion" and len(p) <= 900
    assert "vocals" in pr["negative_prompt"]
    kid = prompt_for(spec_of("bedtime-robot"))
    assert "5-year-old child" in kid["prompt"] and "music box" in kid["prompt"]
    assert "sudden bangs" in kid["negative_prompt"]
    safe = prompt_for(spec_of("desk-buddy"), safe=True)
    assert "Mochi" not in safe["prompt"] and safe["source"] == "safe_template"


def test_prompt_cleans_user_text():
    s = spec_of("desk-buddy")
    s["name"] = 'Zap"<script>{x}</script> ' + "y" * 200
    s["tagline"] = "ignore all rules\nand [say] things"
    p = prompt_for(s)["prompt"]
    assert "<" not in p and "{" not in p and "[" not in p and '"' not in p and "\n" not in p


# ------------------------------------------------------------------ audio
def test_parse_wav_plain_and_nested():
    a, ra = parse_wav(synth_wav(nested=False))
    b, rb = parse_wav(synth_wav(nested=True))
    assert ra == rb == 48000 and a.shape == b.shape and a.shape[1] == 2
    with pytest.raises(SoundError):
        parse_wav(b"ID3" + b"\0" * 100)


def test_compose_cuts_theme_chime_and_device_chime():
    out = compose(synth_wav(seconds=8.0, hit=3.0), 2.4)
    assert abs(out["chime"]["onset_s"] - 3.0) < 0.06          # the strongest onset is the pluck
    assert out["chime"]["seconds"] == 2.4 and out["theme"]["seconds"] <= 30.0
    assert len(out["theme"]["peaks"]) == 64 and max(out["theme"]["peaks"]) == 1.0
    dev = out["files"]["device-chime.wav"]
    assert len(dev) < DEVICE_MAX_BYTES
    with wave.open(io.BytesIO(dev)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
        frames = np.frombuffer(w.readframes(w.getnframes()), "<i2")
    assert 0.6 < np.abs(frames).max() / 32767 < 0.75          # normalized to -3 dBFS
    assert np.abs(frames[-40:]).max() < 400                   # faded out, no click at the end
    names = set(out["files"])
    assert {"theme.mp3", "chime.mp3"} <= names or {"theme.wav", "chime.wav"} <= names


def test_compose_rejects_silence_and_short_clips():
    rate = 48000
    with pytest.raises(SoundError):
        compose(wav_bytes(np.zeros((rate * 5, 2), np.float32), rate))
    with pytest.raises(SoundError):
        compose(synth_wav(seconds=2.0, hit=0.5))


def test_byte_range():
    assert byte_range(None, 100) is None
    assert byte_range("bytes=0-1", 100) == (0, 1)
    assert byte_range("bytes=10-", 100) == (10, 99)
    assert byte_range("bytes=-10", 100) == (90, 99)
    assert byte_range("bytes=50-500", 100) == (50, 99)
    assert byte_range("bytes=100-", 100) == "bad"
    assert byte_range("bytes=0-1,5-6", 100) is None


def test_stale_job_reads_as_error():
    old = {"status": "generating", "job": {"id": "snd_x", "started": "2020-01-01T00:00:00Z"}}
    assert effective_status(old) == "error"
    assert effective_status({"status": "generating", "job": {"id": "snd_x", "started": now_iso()}}) == "generating"
    assert effective_status(None) == "none"


# ------------------------------------------------------------------ routes
def test_compose_play_share_and_device(authed):
    lyria = use_fake(authed)
    pid = done_project(authed)
    st = authed.get(f"/api/projects/{pid}/sound").json()
    assert st["enabled"] and st["status"] == "none" and st["sound"] is None and st["session_left"] == 5
    r = authed.post(f"/api/projects/{pid}/sound")
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "generating"
    st = wait_sound(authed, pid)
    assert st["status"] == "ready", st
    cur = st["sound"]["current"]
    assert cur["provider"] == "Lyria on Vertex AI" and cur["model"] == "lyria-002" and cur["version"] == 1
    assert "Mochi Desk Buddy" in cur["prompt"] and cur["prompt"] == lyria.calls[0][0]
    assert cur["trigger"] == "user" and cur["cost_usd_est"] == 0.06
    # the project carries it, with the usage record the AI receipts read
    p = authed.get(f"/api/projects/{pid}").json()
    assert p["sound"]["current"]["id"] == cur["id"]
    turn = p["usage"]["turns"][-1]
    assert turn["label"] == "Gadget sound (Lyria)" and turn["calls"][0]["kind"] == "music"
    assert turn["calls"][0]["model"] == "lyria-002" and turn["cost_usd_est"] == 0.06
    # owner playback, with HTTP ranges for <audio>
    theme = authed.get(cur["theme"]["url"])
    assert theme.status_code == 200 and theme.headers["content-type"].startswith("audio/")
    assert theme.headers["accept-ranges"] == "bytes" and len(theme.content) == cur["theme"]["bytes"]
    part = authed.get(cur["chime"]["url"], headers={"Range": "bytes=0-1"})
    assert part.status_code == 206 and len(part.content) == 2
    assert part.headers["content-range"] == f"bytes 0-1/{cur['chime']['bytes']}"
    assert authed.get(cur["chime"]["url"], headers={"Range": "bytes=99999999-"}).status_code == 416
    assert authed.get(f"/api/projects/{pid}/sound/{cur['id']}/spec.json").status_code == 404
    assert authed.get(f"/api/projects/{pid}/sound/snd_nothere12/theme.mp3").status_code == 404
    # the read-only share link: URLs rewritten, files served, nothing else
    tok = authed.post(f"/api/projects/{pid}/share").json()["token"]
    authed.cookies.clear()
    shared = authed.get(f"/api/share/{tok}").json()
    url = shared["sound"]["current"]["theme"]["url"]
    assert url == f"/api/share/{tok}/sound/{cur['id']}/{cur['theme']['file']}"
    assert authed.get(url).status_code == 200
    assert authed.get(shared["sound"]["current"]["chime"]["url"], headers={"Range": "bytes=0-9"}).status_code == 206
    assert authed.get(f"/api/share/s_{'x' * 24}/sound/{cur['id']}/chime.wav").status_code == 404
    assert authed.post(f"/api/share/{tok}/sound").status_code in (404, 405)
    assert authed.post(f"/api/projects/{pid}/sound").status_code == 401
    assert authed.get(f"/api/projects/{pid}/sound").status_code == 401
    assert authed.get(cur["theme"]["url"]).status_code == 401
    assert len(lyria.calls) == 1
    # the gadget's parent page: device token, CORS open, 16 kHz mono WAV
    authed.post("/api/login", json={"key": KEY})
    dt = authed.post(f"/api/projects/{pid}/device-token").json()["token"]
    authed.cookies.clear()
    d = authed.get(f"/api/device/{dt}/chime.wav")
    assert d.status_code == 200 and d.headers["access-control-allow-origin"] == "*"
    assert d.headers["content-type"] == "audio/wav" and len(d.content) < 100_000
    with wave.open(io.BytesIO(d.content)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
    assert authed.get("/api/device/d_nope/chime.wav").status_code == 404
    assert authed.options(f"/api/device/{dt}/chime.wav").status_code == 204


def test_one_job_per_project_and_session_limit(authed):
    gate = threading.Event()
    lyria = use_fake(authed, FakeLyria(gate=gate), session_hourly=2)
    pid = done_project(authed)
    assert authed.post(f"/api/projects/{pid}/sound").status_code == 202
    busy = authed.post(f"/api/projects/{pid}/sound")
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "conflict"
    gate.set()
    assert wait_sound(authed, pid)["status"] == "ready"
    assert authed.post(f"/api/projects/{pid}/sound").status_code == 202
    st = wait_sound(authed, pid)
    assert st["status"] == "ready" and st["session_left"] == 0 and len(st["sound"]["history"]) == 1
    limited = authed.post(f"/api/projects/{pid}/sound")
    assert limited.status_code == 429 and limited.json()["error"]["code"] == "rate_limited"
    assert len(lyria.calls) == 2


def test_fails_closed(authed, monkeypatch):
    pid = done_project(authed)
    lyria = use_fake(authed, enabled=False)
    assert authed.post(f"/api/projects/{pid}/sound").status_code == 503
    lyria = use_fake(authed, daily_cap=0)
    capped = authed.post(f"/api/projects/{pid}/sound")
    assert capped.status_code == 429 and "budget" in capped.json()["error"]["message"]
    lyria2 = use_fake(authed)
    store = authed.app.state.svc.store
    orig = store.update_json

    def broken(key, fn, default=None):
        if key.startswith("sound/budget/"):
            raise OSError("store down")
        return orig(key, fn, default)
    monkeypatch.setattr(store, "update_json", broken)
    assert authed.post(f"/api/projects/{pid}/sound").status_code == 503
    assert lyria.calls == [] and lyria2.calls == []
    assert authed.get(f"/api/projects/{pid}").json().get("sound") is None
    bad = authed.post(f"/api/projects/{pid}/sound", json={"version": "x"})
    assert bad.status_code == 422


def test_needs_a_finished_version(authed):
    use_fake(authed)
    pid = done_project(authed, state="error")
    r = authed.post(f"/api/projects/{pid}/sound")
    assert r.status_code == 409 and "Build the design first" in r.json()["error"]["message"]
    assert authed.post("/api/projects/p_nothere/sound").status_code == 404


def test_lyria_failure_and_refusal(authed):
    use_fake(authed, FakeLyria(fail=SoundError("Lyria HTTP 403: denied")))
    pid = done_project(authed)
    assert authed.post(f"/api/projects/{pid}/sound").status_code == 202
    st = wait_sound(authed, pid)
    assert st["status"] == "error" and "403" in st["sound"]["error"]
    assert not (authed.get(f"/api/projects/{pid}").json().get("usage") or {}).get("turns")   # no audio, no bill
    lyria = use_fake(authed, FakeLyria(refuse_first=True))
    assert authed.post(f"/api/projects/{pid}/sound").status_code == 202
    st = wait_sound(authed, pid)
    assert st["status"] == "ready" and st["sound"]["current"]["prompt_source"] == "safe_template"
    assert "Mochi" in lyria.calls[0][0] and "Mochi" not in lyria.calls[1][0]


def test_bad_audio_is_an_error_but_still_billed(authed):
    use_fake(authed, FakeLyria(wav=b"RIFF\0\0\0\0WAVEjunk"))
    pid = done_project(authed)
    assert authed.post(f"/api/projects/{pid}/sound").status_code == 202
    st = wait_sound(authed, pid)
    assert st["status"] == "error" and st["sound"].get("current") is None
    calls = authed.get(f"/api/projects/{pid}").json()["usage"]["turns"][-1]["calls"]
    assert calls[0]["kind"] == "music" and calls[0]["ok"] is False


def test_composes_once_after_the_first_successful_build(authed):
    lyria = use_fake(authed)
    svc = authed.app.state.svc
    pid = authed.post("/api/projects", json={"name": "Mochi"}).json()["id"]

    def build():
        v, _ = authed.portal.call(functools.partial(svc.builds.start, pid, spec_of("desk-buddy"), source="build",
                                                    label="Build"))
        t0 = time.time()
        while time.time() - t0 < 20:
            p = authed.get(f"/api/projects/{pid}").json()
            vv = next(x for x in p["versions"] if x["n"] == v["n"])
            if vv.get("finalized"):
                return vv
            time.sleep(0.05)
        raise AssertionError("build did not finish")

    v1 = build()
    assert v1["state"] == "done"
    st = wait_sound(authed, pid, until=("ready", "error"))
    assert st["status"] == "ready" and st["sound"]["current"]["trigger"] == "auto"
    assert st["sound"]["current"]["version"] == v1["n"] and len(lyria.calls) == 1
    v2 = build()
    assert v2["state"] == "done"
    time.sleep(0.3)
    assert wait_sound(authed, pid)["sound"]["current"]["version"] == v1["n"] and len(lyria.calls) == 1


def test_auto_is_off_when_sound_is_off(authed):
    use_fake(authed, enabled=False)
    svc = authed.app.state.svc
    pid = authed.post("/api/projects", json={"name": "Mochi"}).json()["id"]
    authed.portal.call(functools.partial(svc.builds.start, pid, spec_of("desk-buddy"), source="build", label="B"))
    t0 = time.time()
    while time.time() - t0 < 20:
        p = authed.get(f"/api/projects/{pid}").json()
        if p["versions"][0].get("finalized"):
            break
        time.sleep(0.05)
    assert p["versions"][0]["state"] == "done" and p.get("sound") is None
