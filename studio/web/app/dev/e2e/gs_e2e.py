"""E2E benchmark client for Gadget Studio. Runs ON THE VM against the dev server.
   python gs_e2e.py <cmd> [args]   (state + logs in $E2E_OUT, default ./_out next to this file)

Extended for REVIEW-1 MIN-46 (web-frontend) with assertion commands; each writes a PASS/FAIL record to
$E2E_OUT/log/assertions.json and exits non-zero on FAIL:
   edit "<text>"        free-text chat edit; asserts only features[<night light>].at changed (spec.json, derived ignored)
   restore <n>          POST /versions/<n>/restore; asserts the new version's spec_hash equals v<n>'s
   export <n>           HEAD /v/<n>/export answers 200 with a size; GET agrees
   view <n>             reading v<n> (detail, manifest, files) leaves current_version unchanged
   more_concepts        after a build, {action: more_concepts} returns a new concept set
   checks               export + view + restore on v1 (no model call)
PID=p_... seeds the state for a project made elsewhere (cookie from KEY).
"""
import json
import os
import sys
import time
from pathlib import Path

import httpx

BASE = os.environ.get("BASE", "http://127.0.0.1:8095")
KEY = os.environ.get("KEY", "gstudio-dev")
HERE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("E2E_OUT") or (HERE / "_out"))
LOG = OUT / "log"
LOG.mkdir(parents=True, exist_ok=True)
STATE = OUT / "state.json"


def state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(s):
    STATE.write_text(json.dumps(s, indent=1))


def client(s=None):
    s = s or state()
    c = httpx.Client(base_url=BASE, timeout=httpx.Timeout(30.0, read=900.0))
    if s.get("cookie"):
        c.cookies.set("gs_session", s["cookie"])
    return c


def sse(c, path, body, tag):
    """POST an SSE turn; log every event with its arrival time; return (events, wall)."""
    t0 = time.time()
    events = []
    out = (LOG / f"{tag}.jsonl").open("w")
    with c.stream("POST", path, json=body) as r:
        if r.status_code >= 400:
            r.read()
            print("HTTP", r.status_code, r.text[:2000])
            out.write(json.dumps({"http": r.status_code, "body": r.text}) + "\n")
            return [], time.time() - t0
        buf = ""
        for chunk in r.iter_text():
            buf += chunk
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                ev, data = None, None
                for line in block.split("\n"):
                    if line.startswith(":"):
                        continue
                    if line.startswith("event: "):
                        ev = line[7:]
                    elif line.startswith("data: "):
                        data = (data or "") + line[6:]
                if ev is None:
                    continue
                try:
                    d = json.loads(data) if data else None
                except ValueError:
                    d = data
                rec = {"t": round(time.time() - t0, 2), "event": ev, "data": d}
                events.append(rec)
                out.write(json.dumps(rec) + "\n")
                out.flush()
                if ev in ("status", "tool.start", "tool.done", "repair", "build.message", "error", "result",
                          "build.started", "concepts.progress", "project.renamed"):
                    brief = {k: v for k, v in (d or {}).items() if k not in ("message", "project", "seq")} \
                        if isinstance(d, dict) else d
                    print(f"[{rec['t']:7.1f}s] {ev}: {json.dumps(brief)[:300]}", flush=True)
    wall = time.time() - t0
    print(f"-- {tag}: {len(events)} events, wall {wall:.1f}s", flush=True)
    return events, wall


def last(events, ev):
    for e in reversed(events):
        if e["event"] == ev:
            return e["data"]
    return None


def turn_cost(project, turn_id):
    for t in (project.get("usage") or {}).get("turns", []):
        if t.get("turn_id") == turn_id:
            return t
    return None


def record_phase(tag, events, wall, extra=None):
    s = state()
    proj = last(events, "project", ) if events else None
    proj = (proj or {}).get("project") if isinstance(proj, dict) else None
    ts = last(events, "turn.start") or {}
    tid = ts.get("turn_id")
    t = turn_cost(proj or {}, tid) if tid else None
    ph = {"tag": tag, "wall_s": round(wall, 1), "turn_id": tid, "events": len(events),
          "cost_usd_est": t.get("cost_usd_est") if t else None,
          "tokens": {k: t.get(k) for k in ("input_tokens", "output_tokens", "thinking_tokens", "images")} if t else None,
          "calls": [{k: x.get(k) for k in ("model", "kind", "input_tokens", "output_tokens", "thinking_tokens",
                                           "images", "seconds", "cost_usd_est")} for x in (t or {}).get("calls", [])]}
    if extra:
        ph.update(extra)
    s.setdefault("phases", {})[tag] = ph
    save_state(s)
    if proj:
        (LOG / f"{tag}-project.json").write_text(json.dumps(proj, indent=1))
    print("PHASE", json.dumps(ph, indent=1))


def cmd_start(prompt):
    c = client({})
    r = c.post("/api/login", json={"key": KEY})
    print("login", r.status_code, r.text)
    cookie = r.cookies.get("gs_session")
    s = {"cookie": cookie}
    save_state(s)
    c = client(s)
    print("health", c.get("/api/health").json())
    r = c.post("/api/projects", json={})
    print("create", r.status_code)
    pid = r.json()["id"]
    s["pid"] = pid
    save_state(s)
    print("pid", pid)
    ev, wall = sse(c, f"/api/projects/{pid}/chat", {"text": prompt}, "01-prompt")
    record_phase("01-prompt", ev, wall)
    ch = last(ev, "choices")
    if ch:
        m = ch["message"]
        print("\nTEXT:\n" + m["text"])
        print("DEFAULTS:", m["payload"].get("defaults"))
        for q in m["payload"]["questions"]:
            print(" Q", json.dumps(q))
    else:
        for e in ev:
            if e["event"] == "message.done":
                print("MSG", json.dumps(e["data"]["message"])[:1500])


def cmd_answer(answers_json):
    s = state()
    c = client(s)
    body = json.loads(answers_json)
    ev, wall = sse(c, f"/api/projects/{s['pid']}/chat", body, "02-answers")
    record_phase("02-answers", ev, wall)
    cc = last(ev, "concepts")
    if cc:
        print("\nTEXT:\n" + cc["message"]["text"])
        print("PAYLOAD:", json.dumps(cc["message"]["payload"]))
        for x in cc["concepts"]:
            print(" C", json.dumps({k: x.get(k) for k in ("id", "title", "tier", "blurb", "state", "render_url",
                                                         "photo_url", "glb_url", "warnings", "photo_note")}))
            sp = x.get("spec") or {}
            print("   family:", sp.get("family") or sp.get("body", {}).get("family"), "| spec keys:", list(sp)[:20])
    else:
        for e in ev:
            if e["event"] in ("message.done", "error"):
                print("MSG", json.dumps(e["data"])[:2500])


def cmd_select(cid):
    s = state()
    c = client(s)
    ev, wall = sse(c, f"/api/projects/{s['pid']}/chat", {"action": "select_concept", "concept_id": cid}, "03-select")
    record_phase("03-select", ev, wall, {"concept_id": cid})
    b = last(ev, "brief")
    if b:
        print("\nTEXT:\n" + b["message"]["text"])
        br = dict(b["brief"])
        br.pop("spec", None)
        print("BRIEF:", json.dumps(br, indent=1))
    else:
        for e in ev:
            if e["event"] in ("message.done", "error"):
                print("MSG", json.dumps(e["data"])[:2500])


def cmd_sse(tag, path_tpl, body_json):
    s = state()
    c = client(s)
    path = path_tpl.replace("{pid}", s["pid"])
    ev, wall = sse(c, path, json.loads(body_json), tag)
    res = last(ev, "result")
    record_phase(tag, ev, wall, {"result_version": (res or {}).get("version", {}).get("n") if res else None})
    if res:
        print("\nRESULT TEXT:\n" + res["message"]["text"])
        print("VERSION:", json.dumps(res["version"])[:1500])
    for e in ev:
        if e["event"] in ("error",) or (e["event"] == "message.done" and e["data"]["message"]["role"] == "assistant"
                                         and e["data"]["message"]["kind"] in ("text", "error")):
            print("MSG", json.dumps(e["data"])[:2000])


def cmd_tweak(body_json):
    s = state()
    c = client(s)
    t0 = time.time()
    r = c.post(f"/api/projects/{s['pid']}/tweak", json=json.loads(body_json))
    print("tweak", r.status_code, r.text[:1500])
    (LOG / "06-tweak-response.json").write_text(r.text)
    if r.status_code != 202:
        return
    ev, wall = [], 0
    # follow the project stream until the new version is finished
    for attempt in range(6):
        e2, w2 = sse_get(c, f"/api/projects/{s['pid']}/stream", f"06-tweak-stream-{attempt}")
        ev += e2
        p = (last(e2, "project") or {}).get("project") or {}
        vs = p.get("versions") or []
        if vs and vs[-1].get("state") in ("done", "error"):
            break
    wall = time.time() - t0
    s = state()
    s.setdefault("phases", {})["06-tweak"] = {"tag": "06-tweak", "wall_s": round(wall, 1), "cost_usd_est": 0.0,
                                              "note": "tweak makes no model call"}
    save_state(s)
    res = last(ev, "result")
    if res:
        print("\nRESULT TEXT:\n" + res["message"]["text"])


def sse_get(c, path, tag):
    t0 = time.time()
    events = []
    out = (LOG / f"{tag}.jsonl").open("w")
    with c.stream("GET", path) as r:
        buf = ""
        for chunk in r.iter_text():
            buf += chunk
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                ev, data = None, None
                for line in block.split("\n"):
                    if line.startswith("event: "):
                        ev = line[7:]
                    elif line.startswith("data: "):
                        data = (data or "") + line[6:]
                if ev is None:
                    continue
                try:
                    d = json.loads(data) if data else None
                except ValueError:
                    d = data
                rec = {"t": round(time.time() - t0, 2), "event": ev, "data": d}
                events.append(rec)
                out.write(json.dumps(rec) + "\n")
                out.flush()
                if ev in ("build.message", "result", "error"):
                    print(f"[{rec['t']:7.1f}s] {ev}: {json.dumps({k: v for k, v in d.items() if k not in ('message', 'seq')})[:300]}",
                          flush=True)
    return events, time.time() - t0


def cmd_project():
    s = state()
    c = client(s)
    p = c.get(f"/api/projects/{s['pid']}").json()
    (LOG / "project-final.json").write_text(json.dumps(p, indent=1))
    print(json.dumps({"stage": p["stage"], "current_version": p["current_version"],
                      "versions": [{k: v.get(k) for k in ("n", "label", "source", "state", "started", "ended", "error")}
                                   for v in p["versions"]],
                      "usage_totals": (p.get("usage") or {}).get("totals")}, indent=1))


# ============================================================================ REVIEW-1 MIN-46 assertions
def _assert(name, ok, detail=None):
    p = LOG / "assertions.json"
    rows = json.loads(p.read_text()) if p.exists() else []
    rows.append({"name": name, "ok": bool(ok), "detail": detail, "t": time.strftime("%H:%M:%S")})
    p.write_text(json.dumps(rows, indent=1))
    print(("PASS " if ok else "FAIL ") + name, json.dumps(detail)[:600] if detail is not None else "", flush=True)
    return bool(ok)


def _seeded():
    s = state()
    if os.environ.get("PID") and s.get("pid") != os.environ["PID"]:
        c = httpx.Client(base_url=BASE, timeout=30)
        r = c.post("/api/login", json={"key": KEY})
        s = {"cookie": r.cookies.get("gs_session"), "pid": os.environ["PID"]}
        save_state(s)
    return s


def _project(c, pid):
    return c.get(f"/api/projects/{pid}").json()


def _wait_version(c, pid, n, timeout=1800):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = next((x for x in _project(c, pid)["versions"] if x["n"] == n), None)
        if v and v.get("state") in ("done", "error") and v.get("finalized", True):
            return v
        time.sleep(3)
    return None


def diff_paths(a, b, path=""):
    """Paths whose values differ between two specs; `derived` (engine-only) is ignored."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k == "derived":
                continue
            out += diff_paths(a.get(k), b.get(k), f"{path}.{k}" if path else k)
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += diff_paths(x, y, f"{path}[{i}]")
        return out
    return [] if a == b else [path or "."]


def cmd_edit(text="move the night light up 10 mm"):
    s = _seeded()
    c = client(s)
    pid = s["pid"]
    p0 = _project(c, pid)
    base = p0["current_version"]
    spec0 = c.get(f"/api/projects/{pid}/v/{base}/files/spec.json").json()
    ev, wall = sse(c, f"/api/projects/{pid}/chat", {"text": text}, "10-edit")
    record_phase("10-edit", ev, wall)
    started = last(ev, "build.started") or {}
    n = (started.get("version") or {}).get("n")
    if not n:
        return _assert("MIN-46 free-text edit makes a new version", False, {"events": [e["event"] for e in ev][-12:]})
    v = _wait_version(c, pid, n)
    if not v or v["state"] != "done":
        return _assert("MIN-46 free-text edit version builds", False, v)
    spec1 = c.get(f"/api/projects/{pid}/v/{n}/files/spec.json").json()
    feats = spec0.get("features") or []
    k = next((i for i, f in enumerate(feats) if any(w in f"{f.get('id', '')} {f.get('label', '')}".lower() for w in ("night", "light", "led"))), None)
    changed = diff_paths(spec0, spec1)
    ok = k is not None and bool(changed) and all(pth.startswith(f"features[{k}].at") for pth in changed)
    dz = None
    if k is not None and ok:
        dz = round(spec1["features"][k]["at"][1] - spec0["features"][k]["at"][1], 2)
    return _assert("MIN-46 free-text edit changes only features[night].at", ok,
            {"base": base, "new": n, "feature": k, "changed": changed[:20], "dz_mm": dz})


def cmd_restore(n="1"):
    s = _seeded()
    c = client(s)
    pid, n = s["pid"], int(n)
    p0 = _project(c, pid)
    src = next(v for v in p0["versions"] if v["n"] == n)
    r = c.post(f"/api/projects/{pid}/versions/{n}/restore")
    if r.status_code != 201:
        return _assert(f"MIN-46 restore v{n} accepted", False, {"status": r.status_code, "body": r.text[:300]})
    nv = r.json()
    done = _wait_version(c, pid, nv["n"])
    # the server rehashes a version from its built spec.json when it finalizes; compare the finalized hashes
    return _assert(f"MIN-46 restore of v{n} makes v{nv['n']} with v{n}'s spec_hash",
            bool(done) and done["state"] == "done" and done.get("spec_hash") == src["spec_hash"],
            {"new": nv["n"], "hash": (done or {}).get("spec_hash"), f"v{n}": src["spec_hash"], "state": (done or {}).get("state"),
             "hash_at_create": nv["spec_hash"]})


def cmd_export(n="1"):
    s = _seeded()
    c = client(s)
    pid, n = s["pid"], int(n)
    h = c.head(f"/api/projects/{pid}/v/{n}/export")
    size = int(h.headers.get("content-length") or 0)
    with c.stream("GET", f"/api/projects/{pid}/v/{n}/export") as g:
        got = sum(len(b) for b in g.iter_bytes())
        st = g.status_code
    return _assert(f"MIN-46 HEAD /v/{n}/export answers 200 with the zip size", h.status_code == 200 and size > 0 and st == 200 and got == size,
            {"head": h.status_code, "content_length": size, "get": st, "bytes": got})


def cmd_view(n="1"):
    s = _seeded()
    c = client(s)
    pid, n = s["pid"], int(n)
    before = _project(c, pid)["current_version"]
    d = c.get(f"/api/projects/{pid}/v/{n}").json()
    c.get(f"/api/projects/{pid}/v/{n}/files/manifest.json")
    c.head(f"/api/projects/{pid}/v/{n}/files/cad/assembly.glb")
    after = _project(c, pid)["current_version"]
    return _assert(f"MIN-46 viewing v{n} leaves current at v{before}", before == after and len(d.get("files") or []) > 0,
            {"before": before, "after": after, "files": len(d.get("files") or [])})


def cmd_more_concepts():
    s = _seeded()
    c = client(s)
    pid = s["pid"]
    old = {x["id"] for x in _project(c, pid)["concepts"]}
    ev, wall = sse(c, f"/api/projects/{pid}/chat", {"action": "more_concepts"}, "11-more-concepts")
    record_phase("11-more-concepts", ev, wall)
    cc = last(ev, "concepts") or {}
    new = [x["id"] for x in cc.get("concepts") or [] if x["id"] not in old]
    err = next((e["data"] for e in ev if e["event"] == "error"), None) or (ev[0]["data"] if ev and "http" in (ev[0] or {}) else None)
    return _assert("MIN-43 'Show new concepts' after a build returns a new set", len(new) >= 3, {"new": new, "error": err})


def cmd_checks():
    results = []
    for f, a in ((cmd_export, "1"), (cmd_view, "1"), (cmd_restore, "1")):
        results.append(f(a))
    rows = json.loads((LOG / "assertions.json").read_text())
    bad = [r for r in rows[-3:] if not r["ok"]]
    print(f"checks: {3 - len(bad)}/3 passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    r = {"start": cmd_start, "answer": cmd_answer, "select": cmd_select, "sse": cmd_sse, "tweak": cmd_tweak,
         "project": cmd_project, "edit": cmd_edit, "restore": cmd_restore, "export": cmd_export, "view": cmd_view,
         "more_concepts": cmd_more_concepts, "checks": cmd_checks}[cmd](*args)
    if r is False:
        sys.exit(1)
