"""Live concept run against Vertex AI (run on the VM): the benchmark prompt through the real agent, with every event,
repair round and concept written to files so the result can be judged afterwards.

  GSTUDIO_LIVE=1 BUILDER=local PHOTOS=0 python scripts/live_concepts.py <out_dir> [--photos] [--mode pro|fast]

Writes <out>/events-<turn>.jsonl, <out>/project.json, <out>/concepts/<title>-{render,photo}.<ext>, <out>/summary.json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

PROMPT = "I want a cute robot that tells my daughter bedtime stories and remembers her favorite characters."
KEY = "live-key-123"


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
                if ev in ("status", "repair", "tool.start", "tool.done", "error", "concepts.progress"):
                    brief = {k: v for k, v in d.items() if k not in ("seq",)}
                    print(f"[{time.strftime('%H:%M:%S')}] {ev}: {json.dumps(brief)[:700]}", flush=True)
    return out


def answer_for(q: dict) -> str:
    text = (q.get("question", "") + " " + q.get("consequence", "")).lower()
    opts = q.get("options") or []
    if "old" in text or "age" in text:
        return "5 years old"                    # what the benchmark user typed (free text)
    if any(w in text for w in ("favourite", "favorite", "character", "name")):
        return next((o for o in opts if "type" in o.lower() or "parent" in o.lower() or "app" in o.lower()),
                    opts[0] if opts else "I will type them in for her")
    return opts[0] if opts else "Use your suggestion"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--photos", action="store_true")
    ap.add_argument("--mode", default="pro")
    ap.add_argument("--brief", action="store_true", help="also pick the first Expected concept and write the brief")
    a = ap.parse_args()
    out = Path(a.out)
    (out / "concepts").mkdir(parents=True, exist_ok=True)
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    store = Path(tempfile.mkdtemp(prefix="gs-live-", dir=str(out)))
    s = Settings(store_dir=store / "store", builder=os.environ.get("BUILDER", "local"), agent="gemini",
                 access_key=KEY, photos=a.photos, static_dir=None, cookie_secure=False)
    t0 = time.time()
    summary: dict = {"prompt": PROMPT, "photos": a.photos, "mode": a.mode, "turns": []}
    with TestClient(create_app(s)) as c:
        c.post("/api/login", json={"key": KEY})
        pid = c.post("/api/projects", json={"mode": a.mode}).json()["id"]
        evs = sse(c, f"/api/projects/{pid}/chat", {"text": PROMPT}, out / "events-1-prompt.jsonl")
        summary["turns"].append({"turn": "prompt", "events": [e for e, _ in evs], "seconds": round(time.time() - t0)})
        ch = [d for e, d in evs if e == "choices"]
        if ch:
            m = ch[0]["message"]
            print("\nCLARIFY TEXT:\n" + m["text"] + "\n", flush=True)
            answers = [{"question_id": q["id"], "value": answer_for(q)} for q in m["payload"]["questions"]]
            summary["answers"] = answers
            t1 = time.time()
            evs = sse(c, f"/api/projects/{pid}/chat", {"answers": answers}, out / "events-2-answers.jsonl")
            summary["turns"].append({"turn": "answers", "events": [e for e, _ in evs], "seconds": round(time.time() - t1)})
        p = c.get(f"/api/projects/{pid}").json()
        (out / "project.json").write_text(json.dumps(p, indent=1))
        cons_msg = next((m for m in reversed(p["chat"]) if m["kind"] == "concepts"), None)
        rows = []
        for cc in p["concepts"]:
            row = {k: cc.get(k) for k in ("id", "title", "tier", "blurb", "state", "photo_note")}
            sp = cc.get("spec") or {}
            row["family"] = (sp.get("body") or {}).get("family")
            row["decor"] = [(d.get("type"), d.get("style") or d.get("shape")) for d in sp.get("decor") or []]
            row["features"] = [(f.get("module"), f.get("face"), f.get("at")) for f in sp.get("features") or []]
            for k in ("render_url", "photo_url"):
                if cc.get(k):
                    r = c.get(cc[k])
                    ext = cc[k].rsplit(".", 1)[-1]
                    fn = out / "concepts" / f"{cc['title'].replace(' ', '_')}-{k[:-4]}.{ext}"
                    fn.write_bytes(r.content)
                    row[k[:-4]] = str(fn)
            rows.append(row)
        summary["concepts"] = rows
        summary["message"] = cons_msg["text"] if cons_msg else None
        summary["set_check"] = (cons_msg or {}).get("payload", {}).get("set_check") if cons_msg else None
        summary["usage"] = (p.get("usage") or {}).get("totals")
        summary["errors"] = [m["text"] for m in p["chat"] if m["kind"] == "error"]
        if a.brief and p["concepts"]:
            first = next((cc for cc in p["concepts"] if cc.get("tier") == "expected" and cc.get("state") == "ready"),
                         p["concepts"][0])
            t1 = time.time()
            evs = sse(c, f"/api/projects/{pid}/chat", {"action": "select_concept", "concept_id": first["id"]},
                      out / "events-3-brief.jsonl")
            summary["turns"].append({"turn": "brief", "events": [e for e, _ in evs], "seconds": round(time.time() - t1)})
            br = [d for e, d in evs if e == "brief"]
            if br:
                b = {k: v for k, v in br[0]["brief"].items() if k != "spec"}
                summary["brief"] = b
                print("BRIEF:", json.dumps(b, indent=1)[:5000], flush=True)
            p = c.get(f"/api/projects/{pid}").json()
            (out / "project.json").write_text(json.dumps(p, indent=1))
        summary["seconds"] = round(time.time() - t0)
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: summary[k] for k in ("concepts", "set_check", "usage", "errors", "seconds")}, indent=1)[:6000])
    return 0 if summary.get("concepts") else 1


if __name__ == "__main__":
    sys.exit(main())
