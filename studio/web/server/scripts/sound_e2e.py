"""End-to-end check of the gadget sound against a running server (the VM), with the scripted agent, the fake builder
and the REAL Lyria model (one call, about USD 0.06):

  BASE=http://127.0.0.1:9202 KEY_FILE=/path/to/test-key python scripts/sound_e2e.py

The benchmark conversation builds v1; the sound must then compose by itself (after the first successful build), play
for the owner with HTTP ranges, play through the read-only share link, refuse to compose without a login, and serve
the device chime. Prints a JSON summary (also written to $OUT, default /tmp/lyria-e2e.json)."""
from __future__ import annotations

import io
import json
import os
import sys
import time
import wave
from pathlib import Path

import httpx

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER / "tests"))
sys.path.insert(0, str(SERVER))

from test_api_flow import _benchmark_to_ready  # noqa: E402  (the scripted conversation up to a finished build)


def main() -> None:
    base = os.environ.get("BASE", "http://127.0.0.1:9202")
    key = Path(os.environ["KEY_FILE"]).read_text().strip()
    c = httpx.Client(base_url=base, timeout=180)
    assert c.post("/api/login", json={"key": key}).status_code == 200
    t0 = time.time()
    pid = _benchmark_to_ready(c)
    t_build = time.time() - t0
    st: dict = {}
    for _ in range(150):
        st = c.get(f"/api/projects/{pid}/sound").json()
        if st["status"] in ("ready", "error"):
            break
        time.sleep(1)
    t_sound = time.time() - t0
    assert st["status"] == "ready", st
    cur = st["sound"]["current"]
    assert cur["trigger"] == "auto" and cur["version"] == 1 and cur["model"]
    theme = c.get(cur["theme"]["url"])
    assert theme.status_code == 200 and len(theme.content) == cur["theme"]["bytes"], theme.status_code
    rng = c.get(cur["chime"]["url"], headers={"Range": "bytes=0-1"})
    assert rng.status_code == 206 and len(rng.content) == 2
    p = c.get(f"/api/projects/{pid}").json()
    music = [x for t in p["usage"]["turns"] for x in t.get("calls") or [] if x.get("kind") == "music"]
    assert len(music) == 1 and music[0]["model"] == cur["model"]
    tok = c.post(f"/api/projects/{pid}/share").json()["token"]
    dev = c.post(f"/api/projects/{pid}/device-token").json()["token"]
    anon = httpx.Client(base_url=base, timeout=60)
    shared = anon.get(f"/api/share/{tok}").json()
    s_cur = shared["sound"]["current"]
    assert s_cur["theme"]["url"].startswith(f"/api/share/{tok}/sound/")
    assert anon.get(s_cur["theme"]["url"]).status_code == 200
    assert anon.get(s_cur["chime"]["url"], headers={"Range": "bytes=0-9"}).status_code == 206
    assert anon.post(f"/api/projects/{pid}/sound").status_code == 401
    assert anon.post(f"/api/share/{tok}/sound").status_code in (404, 405)
    d = anon.get(f"/api/device/{dev}/chime.wav")
    assert d.status_code == 200 and d.headers["access-control-allow-origin"] == "*"
    with wave.open(io.BytesIO(d.content)) as w:
        dev_fmt = (w.getframerate(), w.getnchannels(), w.getsampwidth() * 8, len(d.content))
    assert dev_fmt[:3] == (16000, 1, 16) and dev_fmt[3] < 100_000
    out = {"pid": pid, "share": tok, "build_s": round(t_build, 1), "sound_ready_s": round(t_sound, 1),
           "model": cur["model"], "model_seconds": cur.get("model_seconds"), "elapsed_s": cur.get("elapsed_s"),
           "theme": {k: cur["theme"][k] for k in ("file", "seconds", "bytes")},
           "chime": {k: cur["chime"][k] for k in ("file", "seconds", "bytes", "onset_s")},
           "device_chime": dict(zip(("rate", "channels", "bits", "bytes"), dev_fmt)),
           "prompt": cur["prompt"], "usage_cost_usd_est": music[0]["cost_usd_est"]}
    Path(os.environ.get("OUT", "/tmp/lyria-e2e.json")).write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
