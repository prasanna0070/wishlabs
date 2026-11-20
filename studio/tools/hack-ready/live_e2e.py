#!/usr/bin/env python3
"""Live end-to-end timing client for the Wish Labs studio on the run.app URL (stdlib only, judge-style flow).

Run:  GS_KEY="$(gcloud secrets versions access latest --secret=gstudio-access-key --project=...)" \
        python3 -I live_e2e.py [out_dir]
The access key and the session cookie live only in memory: never printed, never written.
Writes <out>/timing.json (rewritten after every stage), <out>/events/<nn>-<stage>.jsonl (every SSE event with its
arrival time), and a few small images under <out>/img/ for eyeballing.

Flow: health (cold) -> login -> create project -> wish -> answer questions (first chip each) -> concepts ->
select one -> brief -> Build this (follow the build on the stream, fall back to polling status) -> project
reconcile, version files (exports list), order/quote.json, HEAD export.zip -> if the first build took < 6 min,
"make it light blue" -> rebuild -> verify colours. At most 2 builds.
"""
import http.client
import json
import os
import sys
import time
from datetime import datetime, timezone

HOST = "gstudio-web-xxxxxxxxxx-el.a.run.app"
OUT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
EV_DIR = os.path.join(OUT, "events")
IMG_DIR = os.path.join(OUT, "img")
os.makedirs(EV_DIR, exist_ok=True)
os.makedirs(IMG_DIR, exist_ok=True)

WISH = "a bedtime story robot that tells my daughter stories and remembers her favourite characters"
CHANGE = "make it light blue"
BUILD_LIMIT_S = 15 * 60
CHANGE_IF_BUILD_UNDER_S = 6 * 60
SOCK_TIMEOUT = 180
MAX_IMG_BYTES = 4_000_000

KEY = os.environ.get("GS_KEY", "")
COOKIE = None  # "gs_session=..." kept in memory only
T0 = time.time()
LOG = {"base": f"https://{HOST}", "wish": WISH, "started_utc": None, "ended_utc": None, "project_id": None,
       "stages": [], "summary": {}, "notes": [], "builds_started": 0}
_stage_no = [0]


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"


TIMING = os.environ.get("TIMING", "timing.json")   # resume runs write timing-resume.json
RESUME_PID = os.environ.get("PID", "")             # resume an existing project (skip create + wish)
EV_PREFIX = os.environ.get("EV_PREFIX", "")


def save():
    tmp = os.path.join(OUT, TIMING + ".tmp")
    with open(tmp, "w") as f:
        json.dump(LOG, f, indent=1)
    os.replace(tmp, os.path.join(OUT, TIMING))


def say(*a):
    print(f"[{time.time() - T0:7.1f}s]", *a, flush=True)


def _headers(extra=None, body=False):
    h = {"Accept": "*/*", "User-Agent": "studio-e2e-timing/1"}
    if body:
        h["Content-Type"] = "application/json"
    if COOKIE:
        h["Cookie"] = COOKIE
    if extra:
        h.update(extra)
    return h


def req(method, path, body=None, timeout=120, keep_body=True, max_body=None):
    """GET/HEAD retry network errors (laptop network flaps) up to 3 times; POSTs are never retried here."""
    tries = 3 if method in ("GET", "HEAD") else 1
    for i in range(tries):
        r = _req(method, path, body, timeout, keep_body, max_body)
        if r.get("status") is not None or i == tries - 1:
            if i:
                r["network_retries"] = i
            return r
        say(f"  network error on {method} {path}: {r.get('error')}; retry in 6 s")
        time.sleep(6)
    return r


def _req(method, path, body=None, timeout=120, keep_body=True, max_body=None):
    """One HTTP call on a fresh connection. Returns dict(status, ttfb, wall, bytes, body, headers, error)."""
    t = time.time()
    r = {"method": method, "path": path, "status": None, "ttfb_s": None, "wall_s": None, "bytes": 0,
         "error": None, "start_utc": now()}
    data = json.dumps(body).encode() if body is not None else None
    try:
        c = http.client.HTTPSConnection(HOST, timeout=timeout)
        c.request(method, path, body=data, headers=_headers(body=data is not None))
        resp = c.getresponse()
        r["ttfb_s"] = round(time.time() - t, 3)
        r["status"] = resp.status
        r["resp_headers"] = {k.lower(): v for k, v in resp.getheaders()
                             if k.lower() in ("content-type", "content-length", "cache-control", "location",
                                              "content-disposition", "etag")}
        raw = b""
        if method != "HEAD":
            if max_body is not None:
                raw = resp.read(max_body + 1)
                if len(raw) > max_body:
                    r["truncated"] = True
                    raw = raw[:max_body]
                    c.close()
            else:
                raw = resp.read()
        r["bytes"] = len(raw)
        r["_set_cookie"] = [v for k, v in resp.getheaders() if k.lower() == "set-cookie"]
        r["_raw"] = raw if keep_body else None
        c.close()
    except Exception as e:  # noqa: BLE001
        r["error"] = f"{type(e).__name__}: {e}"
    r["wall_s"] = round(time.time() - t, 3)
    r["end_utc"] = now()
    return r


def body_json(r):
    try:
        return json.loads(r.get("_raw") or b"null")
    except ValueError:
        return None


def err_text(r):
    if r.get("error"):
        return r["error"]
    if r.get("status") and r["status"] >= 400:
        raw = r.get("_raw") or b""
        return f"HTTP {r['status']}: {raw[:600].decode('utf-8', 'replace')}"
    return None


def record(stage, r, ok=None, **extra):
    _stage_no[0] += 1
    rec = {"n": _stage_no[0], "stage": stage}
    for k in ("method", "path", "status", "ttfb_s", "wall_s", "bytes", "start_utc", "end_utc", "resp_headers",
              "truncated"):
        if k in r:
            rec[k] = r[k]
    rec["error"] = err_text(r)
    rec["ok"] = (rec["error"] is None) if ok is None else ok
    rec.update(extra)
    LOG["stages"].append(rec)
    save()
    say(f"{stage}: http={rec.get('status')} wall={rec.get('wall_s')}s bytes={rec.get('bytes')} ok={rec['ok']}"
        + (f" err={rec['error'][:200]}" if rec["error"] else ""))
    return rec


def sse(stage, path, body, deadline_s=None):
    """POST an SSE turn and read it to the end (or until deadline_s since start). Returns (rec, events, project)."""
    t = time.time()
    _stage_no[0] += 1
    n = _stage_no[0]
    fn = os.path.join(EV_DIR, f"{EV_PREFIX}{n:02d}-{stage}.jsonl")
    out = open(fn, "w")
    rec = {"n": n, "stage": stage, "method": "POST", "path": path, "request": body, "status": None, "ttfb_s": None,
           "wall_s": None, "bytes": 0, "start_utc": now(), "error": None, "events_file": fn}
    events, project, errors = [], None, []
    timeline = {}
    statuses, build_lines, concept_prog, tool_calls = [], [], [], []
    stage_trans = {}
    try:
        c = http.client.HTTPSConnection(HOST, timeout=SOCK_TIMEOUT)
        data = json.dumps(body).encode()
        c.request("POST", path, body=data, headers=_headers({"Accept": "text/event-stream"}, body=True))
        resp = c.getresponse()
        rec["ttfb_s"] = round(time.time() - t, 3)
        rec["status"] = resp.status
        if resp.status >= 400:
            raw = resp.read()
            rec["bytes"] = len(raw)
            rec["error"] = f"HTTP {resp.status}: {raw[:800].decode('utf-8', 'replace')}"
        else:
            buf = b""
            while True:
                if deadline_s and time.time() - t > deadline_s:
                    rec["error"] = f"client deadline {deadline_s}s reached; stream abandoned (server keeps going)"
                    break
                chunk = resp.read1(65536)
                if not chunk:
                    break
                rec["bytes"] += len(chunk)
                buf += chunk
                while b"\n\n" in buf:
                    block, buf = buf.split(b"\n\n", 1)
                    ev, dat = None, None
                    for line in block.decode("utf-8", "replace").split("\n"):
                        if line.startswith(":"):
                            continue
                        if line.startswith("event: "):
                            ev = line[7:]
                        elif line.startswith("data: "):
                            dat = (dat or "") + line[6:]
                    if ev is None:
                        continue
                    try:
                        d = json.loads(dat) if dat else None
                    except ValueError:
                        d = dat
                    at = round(time.time() - t, 2)
                    out.write(json.dumps({"t": at, "event": ev, "data": d}) + "\n")
                    out.flush()
                    events.append((at, ev))
                    timeline.setdefault("first_event", at)
                    timeline.setdefault(f"first_{ev}", at)
                    timeline[f"last_{ev}"] = at
                    if not isinstance(d, dict):
                        continue
                    if ev == "status":
                        statuses.append([at, d.get("text")])
                    elif ev == "build.message":
                        build_lines.append([at, d.get("stage"), d.get("text")])
                    elif ev == "build.progress":
                        st = d.get("status") or {}
                        for sg in st.get("stages") or []:
                            key = (sg.get("id"), sg.get("state"))
                            if key not in stage_trans:
                                stage_trans[key] = at
                    elif ev == "concepts.progress":
                        concept_prog.append([at, d.get("index"), d.get("title"), d.get("tier"), d.get("state"),
                                             d.get("note")])
                    elif ev in ("tool.start", "tool.done"):
                        tool_calls.append([at, ev, d.get("name"), d.get("ok"), (d.get("summary") or "")[:200],
                                           d.get("errors")])
                    elif ev == "repair":
                        errors.append({"t": at, "repair_round": d.get("round"), "errors": d.get("errors")})
                    elif ev == "error":
                        errors.append({"t": at, "code": d.get("code"), "message": d.get("message"),
                                       "retryable": d.get("retryable")})
                    elif ev == "project":
                        project = d.get("project")
                    elif ev == "result":
                        m = d.get("message") or {}
                        rec["result_kind"] = m.get("kind")
                        rec["result_text"] = (m.get("text") or "")[:3000]
                        rec["result_version"] = (d.get("version") or {}).get("n") if isinstance(
                            d.get("version"), dict) else d.get("version")
                        if m.get("kind") == "error":
                            errors.append({"t": at, "result_error": m.get("text"), "payload": m.get("payload")})
        c.close()
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"{type(e).__name__}: {e}"
    out.close()
    rec["wall_s"] = round(time.time() - t, 2)
    rec["end_utc"] = now()
    rec["timeline"] = timeline
    rec["event_counts"] = {}
    for _, ev in events:
        rec["event_counts"][ev] = rec["event_counts"].get(ev, 0) + 1
    rec["status_lines"] = statuses[:40]
    if build_lines:
        rec["build_lines"] = build_lines[:80]
    if concept_prog:
        rec["concepts_progress"] = concept_prog
    if tool_calls:
        rec["tool_calls"] = tool_calls
    if stage_trans:
        rec["builder_stage_transitions"] = [[k[0], k[1], v] for k, v in sorted(stage_trans.items(), key=lambda x: x[1])]
    rec["turn_errors"] = errors
    rec["ok"] = rec["error"] is None and not any("code" in e or "result_error" in e for e in errors)
    LOG["stages"].append(rec)
    save()
    say(f"{stage}: http={rec['status']} ttfb={rec['ttfb_s']} wall={rec['wall_s']}s bytes={rec['bytes']} "
        f"events={sum(rec['event_counts'].values())} ok={rec['ok']}" + (f" err={rec['error'][:300]}" if rec['error'] else "")
        + (f" turn_errors={json.dumps(errors)[:400]}" if errors else ""))
    return rec, events, project


def wait_turn_idle(pid, max_s=900):
    """After a broken stream: poll the project until no turn is running; return (project, seconds waited)."""
    t = time.time()
    p = None
    while time.time() - t < max_s:
        r = req("GET", f"/api/projects/{pid}")
        p = body_json(r) if r.get("status") == 200 else None
        if p and not p.get("turn"):
            break
        time.sleep(8)
    return p, round(time.time() - t, 1)


def sse_retry_409(stage, path, body, deadline_s=None):
    pid = LOG.get("project_id")
    for i in range(4):
        rec, ev, p = sse(stage if i == 0 else f"{stage}-retry{i}", path, body, deadline_s)
        if rec.get("status") == 409:
            say("409 conflict: a turn is still running; waiting 8 s")
            time.sleep(8)
            continue
        if rec.get("error") and pid and not (deadline_s and "client deadline" in (rec.get("error") or "")):
            # network break: did the turn reach the server? wait until it is idle, then let the caller re-read state
            p2, waited = wait_turn_idle(pid)
            rec["after_break"] = {"waited_for_idle_s": waited, "stage_now": (p2 or {}).get("stage"),
                                  "last_kind_now": (last_assistant(p2) or {}).get("kind"),
                                  "server_got_it": bool(p2 and any(
                                      m.get("role") == "user" and m.get("ts", "") >= rec["start_utc"][:19]
                                      for m in p2.get("chat") or []))}
            save()
            say(f"  after break: {rec['after_break']}")
            if rec.get("status") is None and not rec["after_break"]["server_got_it"] and i < 3:
                time.sleep(6)
                continue
            return rec, ev, p2
        return rec, ev, p
    return rec, ev, p


def get_project(pid, stage="get-project"):
    r = req("GET", f"/api/projects/{pid}")
    p = body_json(r)
    record(stage, r)
    return p if isinstance(p, dict) and p.get("id") else None


def last_assistant(p):
    for m in reversed((p or {}).get("chat") or []):
        if m.get("role") == "assistant":
            return m
    return None


def fetch_img(pid_path, name):
    """HEAD then GET a small image for eyeballing (skip if > MAX_IMG_BYTES)."""
    h = req("HEAD", pid_path)
    size = int((h.get("resp_headers") or {}).get("content-length") or 0)
    info = {"url": pid_path, "head_status": h.get("status"), "bytes": size, "saved": None}
    if h.get("status") == 200 and 0 < size <= MAX_IMG_BYTES:
        g = req("GET", pid_path, max_body=MAX_IMG_BYTES)
        if g.get("status") == 200 and g.get("_raw"):
            ext = ".png"
            ct = (g.get("resp_headers") or {}).get("content-type", "")
            if "jpeg" in ct:
                ext = ".jpg"
            elif "webp" in ct:
                ext = ".webp"
            fn = os.path.join(IMG_DIR, name + ext)
            with open(fn, "wb") as f:
                f.write(g["_raw"])
            info["saved"] = fn
            info["get_wall_s"] = g["wall_s"]
    return info


def follow_build(pid, n, t_start, why):
    """Poll /v/{n}/status until done/error or BUILD_LIMIT_S since t_start (used when the stream ended early)."""
    last = None
    polls = 0
    while time.time() - t_start < BUILD_LIMIT_S:
        r = req("GET", f"/api/projects/{pid}/v/{n}/status")
        polls += 1
        st = body_json(r) or {}
        if st.get("state") != (last or {}).get("state") or st.get("percent") != (last or {}).get("percent"):
            say(f"  poll v{n}: {st.get('state')} {st.get('percent')}%")
        last = st
        if st.get("state") in ("done", "error"):
            break
        time.sleep(15)
    rec = {"method": "GET", "path": f"/api/projects/{pid}/v/{n}/status", "status": 200, "wall_s":
           round(time.time() - t_start, 1), "bytes": 0}
    record(f"poll-build-v{n}", rec, ok=(last or {}).get("state") == "done", why=why, polls=polls,
           final_state=(last or {}).get("state"), final_error=(last or {}).get("error"),
           since_build_start_s=round(time.time() - t_start, 1))
    return last


def stage_durations(status):
    out = []
    for sg in (status or {}).get("stages") or []:
        d = None
        try:
            if sg.get("started") and sg.get("ended"):
                a = datetime.fromisoformat(sg["started"].replace("Z", "+00:00"))
                b = datetime.fromisoformat(sg["ended"].replace("Z", "+00:00"))
                d = round((b - a).total_seconds(), 1)
        except ValueError:
            pass
        out.append({"id": sg.get("id"), "label": sg.get("label"), "state": sg.get("state"), "seconds": d,
                    "message": (sg.get("message") or "")[:300]})
    return out


def inspect_version(pid, n, tag):
    """Version detail (files = exports list), status stage durations, quote, HEAD export, small images."""
    info = {}
    r = req("GET", f"/api/projects/{pid}/v/{n}")
    v = body_json(r) or {}
    files = v.get("files") or []
    info["version_state"] = v.get("state")
    info["version_label"] = v.get("label")
    info["file_count"] = len(files)
    info["files_total_bytes"] = sum(int(f.get("bytes") or 0) for f in files)
    groups = {}
    for f in files:
        top = f["path"].split("/")[0] if "/" in f["path"] else "(root)"
        g = groups.setdefault(top, {"files": 0, "bytes": 0})
        g["files"] += 1
        g["bytes"] += int(f.get("bytes") or 0)
    info["file_groups"] = groups
    keys = ["build.json", "spec.json", "BUILD_GUIDE.md", "order/quote.json", "cad/assembly.glb", "cad/components.json",
            "manual/assembled.png"]
    info["key_files"] = {k: next((f.get("bytes") for f in files if f["path"] == k), None) for k in keys}
    info["notable_paths"] = [f["path"] for f in files if f["path"].endswith(
        (".step", ".stl", ".3mf", ".gcode", ".kicad_pcb", ".kicad_sch", ".zip", ".pdf", ".bin", ".gbr", ".glb"))][:60]
    info["urls"] = v.get("urls")
    info["summary"] = v.get("summary")
    st = v.get("status") or {}
    info["status_state"] = st.get("state")
    info["status_error"] = st.get("error")
    info["builder_stages"] = stage_durations(st)
    record(f"version-v{n}-detail{tag}", r, **info)
    # quote
    q = req("GET", f"/api/projects/{pid}/v/{n}/files/order/quote.json", max_body=400_000)
    qj = body_json(q)
    qsum = None
    if isinstance(qj, dict):
        qsum = {k: qj.get(k) for k in ("status", "list_price_inr", "list_price_incl_gst", "price_inr", "total_inr",
                                       "currency", "held_because", "reason", "lead_time_days", "valid_until")
                if k in qj}
        if isinstance(qj.get("quote"), dict):
            qsum["quote_sub"] = {k: qj["quote"].get(k) for k in list(qj["quote"].keys())[:25]}
        qsum["top_keys"] = list(qj.keys())[:40]
        with open(os.path.join(OUT, f"quote-v{n}.json"), "w") as f:
            json.dump(qj, f, indent=1)
    record(f"quote-v{n}{tag}", q, quote=qsum)
    # export zip, HEAD only
    h = req("HEAD", f"/api/projects/{pid}/v/{n}/export", timeout=300)
    record(f"export-head-v{n}{tag}", h)
    return info, qsum, v


def main():
    global COOKIE
    LOG["started_utc"] = now()
    save()
    if not KEY:
        LOG["notes"].append("GS_KEY not set: secret access refused or empty; cannot log in")
        save()
        say("no key")
        return 2
    # 1. health (cold start probe)
    r = req("GET", "/api/health")
    record("health-first-call", r, body=body_json(r))
    r2 = req("GET", "/api/health")
    record("health-second-call", r2)
    r = req("GET", "/studio/")
    record("spa-index", r)
    # 2. login
    r = req("POST", "/api/login", {"key": KEY})
    for sc in r.get("_set_cookie") or []:
        if sc.startswith("gs_session="):
            COOKIE = sc.split(";", 1)[0]
    lb = body_json(r) or {}
    record("login", r, ok=bool(COOKIE) and r.get("status") == 200, cookie_received=bool(COOKIE),
           expires=lb.get("expires"))
    if not COOKIE:
        say("login failed")
        return 3
    # 3. create project
    r = req("POST", "/api/projects", {})
    p = body_json(r) or {}
    pid = p.get("id")
    LOG["project_id"] = pid
    record("create-project", r, project_id=pid, stage_after=p.get("stage"), mode=p.get("mode"))
    if not pid:
        return 4
    wish_t0 = time.time()
    LOG["summary"]["wish_sent_utc"] = now()
    # 4. the wish
    rec, ev, proj = sse_retry_409("wish", f"/api/projects/{pid}/chat", {"text": WISH})
    proj = proj or get_project(pid)
    LOG["summary"]["first_visible_text_s"] = rec["timeline"].get("first_text.delta") or rec["timeline"].get(
        "first_choices")
    # 5. drive the conversation to the build
    build_rec = None
    nudged = 0
    selected = None
    for step in range(8):
        m = last_assistant(proj)
        kind = (m or {}).get("kind")
        stg = (proj or {}).get("stage")
        say(f"state: stage={stg} last_assistant_kind={kind}")
        if kind == "choices" and not (m.get("payload") or {}).get("answered"):
            pl = m.get("payload") or {}
            qs = pl.get("questions") or []
            answers = []
            for q in qs:
                if q.get("options"):
                    answers.append({"question_id": q["id"], "value": q["options"][0]})
            body = {"answers": answers}
            if len(answers) < len(qs):
                body["skipped"] = True
            LOG["summary"].setdefault("questions", []).append({
                "lead_text": m.get("text"), "defaults": pl.get("defaults"),
                "questions": [{"id": q.get("id"), "question": q.get("question"), "consequence": q.get("consequence"),
                               "options": q.get("options"), "allow_free_text": q.get("allow_free_text")} for q in qs],
                "sent": body})
            save()
            rec, ev, proj = sse_retry_409("answers", f"/api/projects/{pid}/chat", body)
            proj = proj or get_project(pid)
        elif kind == "concepts":
            pl = m.get("payload") or {}
            ids = pl.get("concept_ids") or []
            cmap = {c["id"]: c for c in (proj.get("concepts") or [])}
            cs = [cmap[i] for i in ids if i in cmap]
            LOG["summary"]["concepts"] = [{"id": c["id"], "title": c.get("title"), "tier": c.get("tier"),
                                           "state": c.get("state"), "has_photo": bool(c.get("photo_url")),
                                           "photo_note": c.get("photo_note"), "warnings": c.get("warnings"),
                                           "blurb": c.get("blurb"), "photo_url": c.get("photo_url"),
                                           "render_url": c.get("render_url")} for c in cs]
            LOG["summary"]["concepts_lead_text"] = m.get("text")
            pick = next((c for c in cs if c.get("tier") == "expected" and c.get("state") == "ready"
                         and c.get("photo_url")), None) or next((c for c in cs if c.get("state") == "ready"), None) \
                or (cs[0] if cs else None)
            if not pick:
                LOG["notes"].append("no concept to pick")
                break
            selected = pick
            LOG["summary"]["selected_concept"] = {"id": pick["id"], "title": pick.get("title"),
                                                  "tier": pick.get("tier")}
            save()
            # small images for eyeballing: the picked photo + one render
            imgs = []
            if pick.get("photo_url"):
                imgs.append(fetch_img(pick["photo_url"], f"concept-{pick['id']}-photo"))
            if pick.get("render_url"):
                imgs.append(fetch_img(pick["render_url"], f"concept-{pick['id']}-render"))
            for c in cs:
                if c is not pick and c.get("photo_url"):
                    h = req("HEAD", c["photo_url"])
                    imgs.append({"url": c["photo_url"], "head_status": h.get("status"),
                                 "bytes": int((h.get("resp_headers") or {}).get("content-length") or 0)})
            LOG["summary"]["concept_images"] = imgs
            save()
            rec, ev, proj = sse_retry_409("select-concept", f"/api/projects/{pid}/chat",
                                          {"action": "select_concept", "concept_id": pick["id"]})
            proj = proj or get_project(pid)
        elif kind == "brief":
            b = proj.get("brief") or {}
            LOG["summary"]["brief"] = {
                "version": b.get("version"), "title": b.get("title"),
                "summary": (b.get("summary") or {}).get("value"), "size": (b.get("size") or {}).get("value"),
                "functions": [(f.get("title"), f.get("source")) for f in b.get("functions") or []],
                "parts": [(x.get("name"), x.get("qty")) for x in b.get("parts") or []],
                "limits": [x.get("value") for x in b.get("limits") or []],
                "safety_flags": b.get("safety_flags"), "brief_text": m.get("text")}
            save()
            LOG["builds_started"] += 1
            build_t0 = time.time()
            LOG["summary"]["build1_started_utc"] = now()
            rec, ev, proj = sse_retry_409("build-this", f"/api/projects/{pid}/chat", {"action": "approve_brief"},
                                          deadline_s=BUILD_LIMIT_S)
            build_rec = rec
            build_wall = time.time() - build_t0
            proj = get_project(pid, "get-project-after-build")
            versions = (proj or {}).get("versions") or []
            vn = (proj or {}).get("current_version") or (versions[-1]["n"] if versions else 1)
            v = next((x for x in versions if x.get("n") == vn), {})
            if v.get("state") not in ("done", "error") and time.time() - build_t0 < BUILD_LIMIT_S:
                follow_build(pid, vn, build_t0, "stream ended before the build finished")
                proj = get_project(pid, "get-project-after-poll")
                versions = (proj or {}).get("versions") or []
                v = next((x for x in versions if x.get("n") == vn), {})
            build_wall = time.time() - build_t0
            LOG["summary"]["build1"] = {"version": vn, "state": v.get("state"), "error": v.get("error"),
                                        "wall_s": round(build_wall, 1), "label": v.get("label"),
                                        "stream_wall_s": rec.get("wall_s")}
            LOG["summary"]["wish_to_build_result_s"] = round(time.time() - wish_t0, 1)
            save()
            break
        elif kind in ("progress", "result", "error"):
            LOG["notes"].append(f"unexpected last kind before build: {kind}: {(m or {}).get('text', '')[:400]}")
            save()
            break
        else:
            if nudged >= 2:
                LOG["notes"].append(f"agent kept replying with kind={kind}; stopped")
                break
            nudged += 1
            LOG["notes"].append(f"agent replied kind={kind} (no tool); nudged: {(m or {}).get('text', '')[:300]}")
            rec, ev, proj = sse_retry_409(f"nudge{nudged}", f"/api/projects/{pid}/chat",
                                          {"text": "Sounds good. Please go with your suggestions."})
            proj = proj or get_project(pid)
    # 6. results of build 1
    b1 = LOG["summary"].get("build1") or {}
    if b1.get("version"):
        info, qsum, v = inspect_version(pid, b1["version"], "")
        LOG["summary"]["build1"]["price"] = (v.get("summary") or {}).get("price") if isinstance(
            v.get("summary"), dict) else None
        LOG["summary"]["build1"]["quote"] = qsum
        LOG["summary"]["wish_to_price_s"] = round(time.time() - wish_t0, 1)
        # result text, made, checks
        res = next((m for m in reversed((proj or {}).get("chat") or []) if m.get("kind") in ("result", "error")
                    and (m.get("payload") or {}).get("version") == b1["version"]), None)
        if res:
            LOG["summary"]["build1"]["result_kind"] = res.get("kind")
            LOG["summary"]["build1"]["result_text"] = res.get("text")
            pl = res.get("payload") or {}
            sm = pl.get("summary") or {}
            LOG["summary"]["build1"]["checks"] = sm.get("checks")
            LOG["summary"]["build1"]["price_from_result"] = sm.get("price")
            LOG["summary"]["build1"]["made"] = sm.get("made")
        if b1.get("state") == "done":
            LOG["summary"]["build1"]["assembled_png"] = fetch_img(
                f"/api/projects/{pid}/v/{b1['version']}/files/manual/assembled.png", f"v{b1['version']}-assembled")
        save()
    # 7. change request, only if build 1 finished in under 6 minutes
    if b1.get("state") == "done" and b1.get("wall_s", 1e9) < CHANGE_IF_BUILD_UNDER_S and LOG["builds_started"] < 2:
        ch_t0 = time.time()
        LOG["summary"]["change_sent_utc"] = now()
        LOG["builds_started"] += 1
        rec, ev, proj = sse_retry_409("change-light-blue", f"/api/projects/{pid}/chat", {"text": CHANGE},
                                      deadline_s=BUILD_LIMIT_S)
        proj = get_project(pid, "get-project-after-change")
        m = last_assistant(proj)
        versions = (proj or {}).get("versions") or []
        vn = (proj or {}).get("current_version")
        v = next((x for x in versions if x.get("n") == vn), {})
        if vn and vn != b1["version"] and v.get("state") not in ("done", "error") and time.time() - ch_t0 < BUILD_LIMIT_S:
            follow_build(pid, vn, ch_t0, "change stream ended before the rebuild finished")
            proj = get_project(pid, "get-project-after-change-poll")
            versions = (proj or {}).get("versions") or []
            v = next((x for x in versions if x.get("n") == vn), {})
        LOG["summary"]["change"] = {"version": vn, "new_version": vn != b1["version"], "state": v.get("state"),
                                    "label": v.get("label"), "error": v.get("error"),
                                    "wall_s": round(time.time() - ch_t0, 1),
                                    "last_assistant_kind": (m or {}).get("kind"),
                                    "last_assistant_text": ((m or {}).get("text") or "")[:1500]}
        if vn and vn != b1["version"]:
            s = req("GET", f"/api/projects/{pid}/v/{vn}/files/spec.json", max_body=600_000)
            sj = body_json(s) or {}
            record(f"spec-v{vn}", s, colors=sj.get("colors"))
            s1 = req("GET", f"/api/projects/{pid}/v/{b1['version']}/files/spec.json", max_body=600_000)
            sj1 = body_json(s1) or {}
            record(f"spec-v{b1['version']}", s1, colors=sj1.get("colors"))
            LOG["summary"]["change"]["colors_before"] = sj1.get("colors")
            LOG["summary"]["change"]["colors_after"] = sj.get("colors")
            if v.get("state") == "done":
                info2, qsum2, v2 = inspect_version(pid, vn, "-change")
                LOG["summary"]["change"]["price"] = (v2.get("summary") or {}).get("price") if isinstance(
                    v2.get("summary"), dict) else None
                LOG["summary"]["change"]["assembled_png"] = fetch_img(
                    f"/api/projects/{pid}/v/{vn}/files/manual/assembled.png", f"v{vn}-assembled")
        save()
    else:
        LOG["notes"].append(f"change request skipped: build1 state={b1.get('state')} wall={b1.get('wall_s')}s "
                            f"(rule: only if done in < {CHANGE_IF_BUILD_UNDER_S}s)")
    # 8. final snapshot
    p = get_project(pid, "final-project")
    if p:
        LOG["summary"]["final"] = {"stage": p.get("stage"), "name": p.get("name"), "current_version":
                                   p.get("current_version"), "questions_asked": p.get("questions_asked"),
                                   "chat_kinds": [(x.get("role"), x.get("kind")) for x in p.get("chat") or []],
                                   "versions": [(x.get("n"), x.get("source"), x.get("state"), x.get("label"))
                                                for x in p.get("versions") or []],
                                   "usage_totals": (p.get("usage") or {}).get("totals")}
        with open(os.path.join(OUT, "final-project.json"), "w") as f:
            json.dump(p, f, indent=1)
    LOG["ended_utc"] = now()
    LOG["total_wall_s"] = round(time.time() - T0, 1)
    save()
    say("finished; project", pid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
