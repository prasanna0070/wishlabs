"""Live smoke test against Vertex AI. Skipped unless GSTUDIO_LIVE=1. Run once on the VM (its service account
can call Vertex):  GSTUDIO_LIVE=1 python -m pytest -m live -s tests/test_live_gemini.py"""
from __future__ import annotations

import json
import os

import pytest

from conftest import KEY, events_named, make_settings, sse

pytestmark = [pytest.mark.live,
              pytest.mark.skipif(os.environ.get("GSTUDIO_LIVE") != "1", reason="set GSTUDIO_LIVE=1 to call Vertex")]

PROMPT = "I want a cute robot that tells my daughter bedtime stories and remembers her favorite characters."


def test_benchmark_first_turns_live(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    mode = os.environ.get("GSTUDIO_LIVE_MODE", "pro")
    s = make_settings(tmp_path, agent="gemini", photos=os.environ.get("GSTUDIO_LIVE_PHOTOS") == "1",
                      builder=os.environ.get("BUILDER", "fake"))
    with TestClient(create_app(s)) as c:
        c.post("/api/login", json={"key": KEY})
        pid = c.post("/api/projects", json={"mode": mode}).json()["id"]
        evs = sse(c, "POST", f"/api/projects/{pid}/chat", json={"text": PROMPT})
        print("\nTURN 1 events:", [e for e, _ in evs])
        assert not events_named(evs, "error"), events_named(evs, "error")
        ch = events_named(evs, "choices")
        assert ch, "expected clarifying questions for the benchmark prompt"
        qs = ch[0]["message"]["payload"]["questions"]
        print("TURN 1 text:", ch[0]["message"]["text"])
        print(json.dumps(qs, indent=1))
        assert 1 <= len(qs) <= 3 and all(q["consequence"] for q in qs)
        answers = [{"question_id": q["id"], "value": q["options"][min(1, len(q["options"]) - 1)]} for q in qs]
        evs = sse(c, "POST", f"/api/projects/{pid}/chat", json={"answers": answers})
        print("TURN 2 events:", [e for e, _ in evs])
        for d in events_named(evs, "repair"):
            print("REPAIR", d["round"], d["errors"][:6])
        cons = events_named(evs, "concepts")
        assert cons, [d for e, d in evs if e in ("error", "message.done")]
        for cc in cons[0]["concepts"]:
            print("CONCEPT", cc["title"], "|", cc["blurb"], "|", cc["state"], cc["photo_note"])
        print("TURN 2 text:", cons[0]["message"]["text"])
        assert 2 <= len(cons[0]["concepts"]) <= 6
        if os.environ.get("GSTUDIO_LIVE_BRIEF") == "1":
            first = cons[0]["concepts"][0]
            evs = sse(c, "POST", f"/api/projects/{pid}/chat", json={"action": "select_concept", "concept_id": first["id"]})
            print("TURN 3 events:", [e for e, _ in evs])
            for d in events_named(evs, "repair"):
                print("REPAIR", d["round"], d["errors"][:6])
            br = events_named(evs, "brief")
            assert br, [d for e, d in evs if e in ("error", "message.done")]
            print("TURN 3 text:", br[0]["message"]["text"])
            print(json.dumps({k: v for k, v in br[0]["brief"].items() if k not in ("spec", "parts")}, indent=1)[:5000])
            evs = sse(c, "POST", f"/api/projects/{pid}/chat", json={"action": "approve_brief"})
            res = events_named(evs, "result")
            print("BUILD:", [e for e, _ in evs][-6:], res[0]["message"]["text"][:600] if res else None)
            usage = c.get(f"/api/projects/{pid}").json()["usage"]
            print("USAGE totals:", usage["totals"])
        out = os.environ.get("GSTUDIO_LIVE_OUT")
        if out:
            p = c.get(f"/api/projects/{pid}").json()
            os.makedirs(out, exist_ok=True)
            with open(os.path.join(out, "project.json"), "w") as f:
                json.dump(p, f, indent=1)
            for cc in p["concepts"]:
                for k in ("render_url", "photo_url"):
                    if cc.get(k):
                        r = c.get(cc[k])
                        with open(os.path.join(out, f"{cc['title'].replace(' ', '_')}-{k[:-4]}.{cc[k].rsplit('.', 1)[-1]}"),
                                  "wb") as f:
                            f.write(r.content)
