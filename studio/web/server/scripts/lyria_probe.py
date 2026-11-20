"""Lyria probe, run on the VM or a laptop with application-default credentials.

  python scripts/lyria_probe.py models                 # which Lyria models Vertex AI knows (no generation, no cost)
  python scripts/lyria_probe.py generate --out DIR     # ONE real composition through app.sound (about USD 0.06)

`generate` uses the same code the server runs (prompt_for -> Lyria.generate -> compose) on an example design's spec
and writes theme, chime, device chime and a meta.json into DIR. LYRIA_MODEL / LYRIA_LOCATION pick the model."""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
STUDIO = SERVER.parents[1]
sys.path.insert(0, str(SERVER))

from app.config import GCP_PROJECT  # noqa: E402
from app.sound import Lyria, SoundConfig, compose, prompt_for  # noqa: E402

CANDIDATES = ("lyria-002", "lyria-003", "lyria-3", "lyria-2", "lyria-realtime-exp", "lyria-002-preview")


def _token() -> str:
    import google.auth
    import google.auth.transport.requests

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def _get(url: str, tok: str) -> tuple[int, dict | str]:
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok, "x-goog-user-project": GCP_PROJECT})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:200]


def models(locations: list[str]) -> None:
    tok = _token()
    for loc in locations:
        host = "aiplatform.googleapis.com" if loc == "global" else f"{loc}-aiplatform.googleapis.com"
        for name in CANDIDATES:
            code, body = _get(f"https://{host}/v1/publishers/google/models/{name}", tok)
            stage = body.get("launchStage") if isinstance(body, dict) else ""
            ver = body.get("versionId") if isinstance(body, dict) else ""
            print(f"{loc:12s} {name:22s} HTTP {code} {stage or ''} {ver or ''}"
                  + ("" if code == 200 else f" {str(body)[:90]!r}"))
        code, body = _get(f"https://{host}/v1beta1/publishers/google/models?pageSize=300&listAllVersions=true", tok)
        if code == 200 and isinstance(body, dict):
            names = sorted({m.get("name", "").rsplit("/", 1)[-1] for m in body.get("publisherModels") or []})
            hits = [n for n in names if "lyria" in n.lower()]
            print(f"{loc:12s} model garden list: {len(names)} models, lyria: {hits}")
        else:
            print(f"{loc:12s} model garden list: HTTP {code} {str(body)[:90]!r}")


class _S:
    gcp_project = GCP_PROJECT
    agent = "gemini"


def generate(out: Path, example: str) -> None:
    cfg = SoundConfig.from_env(_S())
    spec = json.loads((STUDIO / "examples" / example / "spec.json").read_text())
    pr = prompt_for(spec, {"name": spec.get("name")})
    print("model", cfg.model, "location", cfg.location)
    print("prompt:", pr["prompt"])
    t0 = time.time()
    raw = Lyria(_S(), cfg).generate(pr["prompt"], pr["negative_prompt"], 12345)
    t_model = time.time() - t0
    print(f"lyria answered in {t_model:.1f} s, {len(raw)} bytes, head {raw[:4]!r} {raw[8:12]!r}")
    res = compose(raw, cfg.chime_s)
    out.mkdir(parents=True, exist_ok=True)
    for name, data in res["files"].items():
        (out / name).write_bytes(data)
    meta = {k: v for k, v in res.items() if k != "files"}
    meta.update(model=cfg.model, location=cfg.location, prompt=pr["prompt"], model_seconds=round(t_model, 1),
                compose_seconds=round(time.time() - t0 - t_model, 2),
                files={k: len(v) for k, v in res["files"].items()})
    meta["theme"] = {k: v for k, v in meta["theme"].items() if k != "peaks"}
    (out / "meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("models", "generate"))
    ap.add_argument("--loc", action="append", default=None)
    ap.add_argument("--out", default="/tmp/lyria-probe")
    ap.add_argument("--example", default="desk-buddy")
    a = ap.parse_args()
    if a.cmd == "models":
        models(a.loc or ["us-central1", "global"])
    else:
        generate(Path(a.out), a.example)


if __name__ == "__main__":
    main()
