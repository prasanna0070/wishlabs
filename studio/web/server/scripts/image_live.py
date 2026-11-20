"""Live check of "start from an image" against Vertex AI: start this server on a local port (real Gemini agent, fake
builder, local store), sign in with a throwaway key, create a project, upload an image, send it with a message and
save the chat stream, the image brief and the project under an output folder.

  cd studio/web/server && python scripts/image_live.py --image /path/to/sheet.png --out ../../_out/image/run1 \
      --port 9250 --text "my friend made this in ChatGPT, can you build it?" [--mode fast|pro] [--photos]

The image is sent only to this local server and, through it, to Vertex AI in the studio's own project. Nothing is
uploaded anywhere else. Exit code 0 when the agent recorded an image brief and went on to questions or concepts."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

SERVER = Path(__file__).resolve().parents[1]


def turn(c, pid: str, body: dict, path: Path, events: list, t0: float) -> None:
    with open(path, "w") as f, c.stream("POST", f"/api/projects/{pid}/chat", json=body) as resp:
        print("chat", resp.status_code, flush=True)
        ev = None
        for line in resp.iter_lines():
            f.write(line + "\n")
            f.flush()
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
                events.append((ev, data))
                if ev in ("tool.start", "status", "image_brief", "error", "choices", "concepts"):
                    print(f"{time.time() - t0:6.1f}s {ev}", (data.get("label") or data.get("text") or
                                                             data.get("title") or data.get("message") or ""),
                          flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", type=int, default=9250)
    ap.add_argument("--text", default="my friend made this in ChatGPT, can you build it?")
    ap.add_argument("--mode", default="fast", choices=("fast", "pro"))
    ap.add_argument("--photos", action="store_true", help="make concept photos (Nano Banana) if concepts come")
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--continue", dest="cont", action="store_true",
                    help="when the agent asks questions, skip them (use its defaults) to reach the concepts")
    a = ap.parse_args()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(16)                 # a throwaway key for this local server only
    env = {**os.environ, "ACCESS_KEY": key, "AGENT": "gemini", "BUILDER": "fake", "STORE_DIR": str(out / "store"),
           "PHOTOS": "1" if a.photos else "0", "COOKIE_SECURE": "0", "PYTHONUNBUFFERED": "1"}
    env.pop("BUCKET", None)
    env.pop("BUILDER_URL", None)
    log = open(out / "server.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.asgi:app", "--host", "127.0.0.1", "--port",
                             str(a.port)], cwd=SERVER, env=env, stdout=log, stderr=subprocess.STDOUT)
    print(f"server pid {proc.pid} on :{a.port}", flush=True)
    base = f"http://127.0.0.1:{a.port}"
    try:
        with httpx.Client(base_url=base, timeout=httpx.Timeout(a.timeout, connect=10)) as c:
            for _ in range(120):
                try:
                    if c.get("/api/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            else:
                print("server did not start", flush=True)
                return 2
            assert c.post("/api/login", json={"key": key}).status_code == 200
            pid = c.post("/api/projects", json={"mode": a.mode}).json()["id"]
            img = Path(a.image)
            t0 = time.time()
            r = c.post(f"/api/projects/{pid}/uploads", files={"file": (img.name, img.read_bytes(), "image/png")})
            print("upload", r.status_code, f"{time.time() - t0:.1f}s", flush=True)
            (out / "upload.json").write_text(json.dumps(r.json(), indent=1))
            if r.status_code != 201:
                return 3
            uid = r.json()["id"]
            events = []
            t0 = time.time()
            turn(c, pid, {"text": a.text, "attachments": [{"id": uid}]}, out / "sse.log", events, t0)
            secs = time.time() - t0
            p = c.get(f"/api/projects/{pid}").json()
            if a.cont and any(m.get("kind") == "choices" for m in p["chat"]) and \
                    not any(m.get("kind") == "concepts" for m in p["chat"]):
                turn(c, pid, {"answers": [], "skipped": True}, out / "sse2.log", events, time.time())
                p = c.get(f"/api/projects/{pid}").json()
            (out / "project.json").write_text(json.dumps(p, indent=1, ensure_ascii=False))
            brief = p.get("image_brief")
            (out / "brief.json").write_text(json.dumps(brief, indent=1, ensure_ascii=False))
            names = [e for e, _ in events]
            asked = any(m.get("kind") == "choices" for m in p["chat"])
            proposed = any(m.get("kind") == "concepts" for m in p["chat"])
            photos = [x.get("photo_url") for x in p.get("concepts") or [] if x.get("photo_url")]
            for i, url in enumerate(photos):
                (out / f"concept-photo-{i + 1}.{url.rsplit('.', 1)[-1]}").write_bytes(c.get(url).content)
            summary = {"project": pid, "mode": a.mode, "turn_seconds": round(secs, 1), "events": len(events),
                       "concept_photos": len(photos), "concepts": [x.get("title") for x in p.get("concepts") or []],
                       "image_brief_event": names.count("image_brief"), "brief_recorded": bool(brief),
                       "asked_questions": asked, "proposed_concepts": proposed,
                       "errors": [d for e, d in events if e == "error"],
                       "assistant": [m.get("text") for m in p["chat"] if m.get("role") == "assistant"],
                       "usage": (p.get("usage") or {}).get("totals")}
            (out / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
            print(json.dumps(summary, indent=1, ensure_ascii=False), flush=True)
            return 0 if brief and (asked or proposed) else 1
    finally:
        proc.send_signal(signal.SIGTERM)        # only the server this script started
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


if __name__ == "__main__":
    sys.exit(main())
