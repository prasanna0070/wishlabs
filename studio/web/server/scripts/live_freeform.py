"""Live freeform check (run on the VM): one wish through the real Gemini agent with pack `freeform` on, then a report
of the concept set: body family per concept, the csg tree's ops, faces, layouts, repairs and render states.

  GS_PACKS_ENABLED=sensing,schedule,freeform python scripts/live_freeform.py <out_dir> [--url http://127.0.0.1:9281]
      [--prompt "..."] [--photos] [--mode pro|fast]

With --url it talks to a running server (BUILDER/PHOTOS are that server's); without it, the app runs in-process with
BUILDER from the env (default fake). Writes <out>/events-*.jsonl, project.json, concepts/<title>-{render,photo}.<ext>,
summary.json. Exit 0 when a concept set came back.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

PROMPT = ("I want a portable health station for my desk: it shows the room temperature and humidity on a screen, "
          "has a light that tells me when to take a break, and somewhere to keep my keys and pen.")
KEY = os.environ.get("ACCESS_KEY", "live-key-123")


def sse(c, path, body, log: Path) -> list[tuple[str, dict]]:
    out = []
    with c.stream("POST", path, json=body) as r, log.open("w") as fh:
        ev = None
        for line in r.iter_lines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                d = json.loads(line[6:])
                out.append((ev, d))
                fh.write(json.dumps({"t": round(time.time(), 1), "event": ev, "data": d}) + "\n")
                fh.flush()
                if ev in ("status", "repair", "tool.start", "tool.done", "error"):
                    print(f"[{time.strftime('%H:%M:%S')}] {ev}: {json.dumps(d)[:600]}", flush=True)
    return out


def answer_for(q: dict) -> str:
    opts = q.get("options") or []
    return opts[0] if opts else "Use your suggestion"


def ops(node) -> list[str]:
    if isinstance(node, list):
        return [o for n in node for o in ops(n)]
    if not isinstance(node, dict):
        return []
    out = [str(node.get("op"))] if node.get("op") else []
    for k in ("node", "nodes", "minus"):
        if k in node:
            out += ops(node[k])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--url")
    ap.add_argument("--prompt", default=PROMPT)
    ap.add_argument("--photos", action="store_true")
    ap.add_argument("--mode", default="pro")
    ap.add_argument("--dump-calls", action="store_true",
                    help="in-process only: write every tool call's arguments to <out>/calls.jsonl (to read failed specs)")
    a = ap.parse_args()
    out = Path(a.out)
    (out / "concepts").mkdir(parents=True, exist_ok=True)
    if a.url:
        import httpx

        client = httpx.Client(base_url=a.url, timeout=httpx.Timeout(1800.0, connect=30.0))
    else:
        from fastapi.testclient import TestClient

        from app.config import Settings
        from app.main import create_app

        if a.dump_calls:
            from app.agent import turn as turn_mod

            real = turn_mod.Turn.call_tool
            calls = out / "calls.jsonl"

            async def call_tool(self, name, args):
                res = await real(self, name, args)
                with calls.open("a") as fh:
                    fh.write(json.dumps({"t": round(time.time(), 1), "tool": name, "args": args,
                                         "result": res.response}, default=str) + "\n")
                return res

            turn_mod.Turn.call_tool = call_tool
        store = Path(tempfile.mkdtemp(prefix="gs-ff-", dir=str(out)))
        s = Settings(store_dir=store / "store", builder=os.environ.get("BUILDER", "fake"), agent="gemini",
                     access_key=KEY, photos=a.photos, static_dir=None, cookie_secure=False)
        client = TestClient(create_app(s))
    t0 = time.time()
    summary: dict = {"prompt": a.prompt, "mode": a.mode, "packs": os.environ.get("GS_PACKS_ENABLED"), "turns": []}
    with client as c:
        r = c.post("/api/login", json={"key": KEY})
        assert r.status_code == 200, r.text
        pid = c.post("/api/projects", json={"mode": a.mode}).json()["id"]
        evs = sse(c, f"/api/projects/{pid}/chat", {"text": a.prompt}, out / "events-1-prompt.jsonl")
        summary["turns"].append({"turn": "prompt", "seconds": round(time.time() - t0)})
        ch = [d for e, d in evs if e == "choices"]
        if ch:
            m = ch[0]["message"]
            print("\nCLARIFY:\n" + m["text"] + "\n", flush=True)
            answers = [{"question_id": q["id"], "value": answer_for(q)} for q in m["payload"]["questions"]]
            summary["answers"] = answers
            t1 = time.time()
            evs += sse(c, f"/api/projects/{pid}/chat", {"answers": answers}, out / "events-2-answers.jsonl")
            summary["turns"].append({"turn": "answers", "seconds": round(time.time() - t1)})
        p = c.get(f"/api/projects/{pid}").json()
        (out / "project.json").write_text(json.dumps(p, indent=1))
        cons_msg = next((m for m in reversed(p["chat"]) if m["kind"] == "concepts"), None)
        rows = []
        for cc in p.get("concepts") or []:
            sp = cc.get("spec") or {}
            body = sp.get("body") or {}
            row = {k: cc.get(k) for k in ("title", "tier", "blurb", "state", "photo_note")}
            row["family"] = body.get("family")
            row["kind"] = sp.get("kind") or "gadget"
            row["csg_ops"] = dict(Counter(ops(body.get("csg")))) if body.get("family") == "csg" else None
            row["faces"] = [d.get("type") for d in sp.get("decor") or [] if d.get("type") in ("eye", "mouth", "blush")]
            row["ears"] = [d.get("style") for d in sp.get("decor") or [] if d.get("type") == "ear"]
            row["features"] = [(f.get("module"), f.get("face"), f.get("at")) for f in sp.get("features") or []]
            for k in ("render_url", "photo_url"):
                if cc.get(k):
                    rr = c.get(cc[k])
                    ext = cc[k].rsplit(".", 1)[-1]
                    fn = out / "concepts" / f"{cc['title'].replace(' ', '_').replace('/', '_')}-{k[:-4]}.{ext}"
                    fn.write_bytes(rr.content)
                    row[k[:-4]] = str(fn)
            (out / "concepts" / f"{cc['title'].replace(' ', '_').replace('/', '_')}-spec.json").write_text(
                json.dumps(sp, indent=1))
            rows.append(row)
        summary["concepts"] = rows
        summary["families"] = dict(Counter(r["family"] for r in rows))
        summary["with_face"] = [r["title"] for r in rows if r["faces"]]
        summary["repairs"] = [{"kind": d.get("kind") or "spec", "round": d.get("round"), "errors": d.get("errors")[:6]}
                              for e, d in evs if e == "repair"]
        summary["message"] = cons_msg["text"] if cons_msg else None
        summary["set_check"] = (cons_msg or {}).get("payload", {}).get("set_check") if cons_msg else None
        summary["usage"] = (p.get("usage") or {}).get("totals")
        summary["errors"] = [m["text"] for m in p["chat"] if m["kind"] == "error"]
        summary["seconds"] = round(time.time() - t0)
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    show = {k: summary.get(k) for k in ("families", "with_face", "repairs", "set_check", "errors", "seconds")}
    show["concepts"] = [{k: r[k] for k in ("title", "tier", "family", "csg_ops", "faces", "state")} for r in rows]
    print(json.dumps(show, indent=1)[:8000])
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
