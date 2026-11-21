"""VM only: seed a local store for the guest walk (scripts/guest_walk.cjs): one team project built to a version (the
showcase) and one CONFIDENTIAL team project a guest must never see. Writes {"showcase": pid, "secret": pid} to $OUT.
  STORE_DIR=/tmp/ml-walk/store ACCESS_KEY=<test key> BUILDER=fake AGENT=scripted OUT=/tmp/ml-walk/seed.json \
  python scripts/guest_walk_seed.py"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

PROMPT = "A small rain cloud lamp for my desk that glows blue when rain is coming."


def sse(c, url, body):
    out, ev = [], None
    with c.stream("POST", url, json=body) as r:
        assert r.status_code == 200, r.read()
        for line in r.iter_lines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                out.append((ev, json.loads(line[6:])))
    return out


def named(evs, name):
    return [d for e, d in evs if e == name]


def main():
    app = create_app(Settings.from_env())
    with TestClient(app) as c:
        assert c.post("/api/login", json={"key": os.environ["ACCESS_KEY"]}).status_code == 200
        pid = c.post("/api/projects", json={"name": "Rain cloud lamp"}).json()["id"]
        evs = sse(c, f"/api/projects/{pid}/chat", {"text": PROMPT})
        qs = named(evs, "choices")[0]["message"]["payload"]["questions"]
        evs = sse(c, f"/api/projects/{pid}/chat", {"answers": [{"question_id": q["id"], "value": q.get("options", [{}])[0].get("value", "yes") if isinstance(q.get("options"), list) and q["options"] and isinstance(q["options"][0], dict) else "yes"} for q in qs]})
        cons = named(evs, "concepts")[0]["concepts"]
        sse(c, f"/api/projects/{pid}/chat", {"action": "select_concept", "concept_id": cons[0]["id"]})
        sse(c, f"/api/projects/{pid}/chat", {"action": "approve_brief"})
        with c.stream("GET", f"/api/projects/{pid}/stream") as r:     # until the fake build is done
            for _ in r.iter_lines():
                pass
        secret = c.post("/api/projects", json={"name": "Client concept CONFIDENTIAL"}).json()["id"]
        p = c.get(f"/api/projects/{pid}").json()
        print("seeded", pid, "versions", [v["state"] for v in p["versions"]], "secret", secret)
    with open(os.environ["OUT"], "w") as f:
        json.dump({"showcase": pid, "secret": secret}, f)


if __name__ == "__main__":
    main()
