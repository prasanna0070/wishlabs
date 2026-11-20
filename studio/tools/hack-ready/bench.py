#!/usr/bin/env python3
"""Batch benchmark runner for the Wish Labs studio: many wishes, one login, one results table.

Each case: create a project -> send the wish on the chat SSE stream -> answer the follow-up questions (the case's
answers, else the agent's stated defaults) -> wait for the concepts -> pick one (rule below) -> brief -> Build this
(followed on the same stream like the web app does, then by polling the version status) -> done, error or the
per-case timeout -> quote, digital checks, read-only share link. Stdlib only (PyYAML only for .yaml case files).
It grew out of live_e2e.py (same login, SSE reading and build polling).

  GS_KEY=... python3 -I bench.py --base https://wish.plugbook.si --cases cases.json --out runs/r1
  python3 -I bench.py --base http://127.0.0.1:9231 --key-file ~/.gs-key --cases cases.yaml --jobs 3
  python3 -I bench.py --base ... --cases cases.json --out runs/r1 --resume     # finish an interrupted run
  python bench.py --server-local 9231 --cases cases.json --out ../../_out/bench/r1   # VM only, see below
  python3 -I bench.py --cases cases.json --check                               # validate the case file only

Key: env GS_KEY, or --key-file PATH (a chmod 600 file). Never on the command line, never printed or written.
One login per run; every job reuses that cookie. The live service locks an IP after 10 bad keys in 10 minutes,
so a refused login stops the run at once (retried only on a network error before any answer).

Cases (JSON or YAML; a list, or {"cases": [...]}); see bench_cases.example.json:
  {"id": "focus-lamp",                        # 1-64 of A-Z a-z 0-9 _ . -
   "wish": "a desk lamp that ...",
   "answers": [...],                          # optional; one entry per round of follow-up questions, in order:
                                              #   "text"            typed reply (the composer's free-text box)
                                              #   ["a", "b"]        chip answers in question order (rest: defaults)
                                              #   {"age": "5"}      by question id, or words from the question
                                              # no entry left -> "Use your suggestions" (the agent's defaults)
   "mode": "standard",                        # standard (API fast, the Flash model; default) | deep (API pro)
   "timeout_min": 20,                         # optional; else --timeout-min (default 20)
   "expect": {"buildable": true, "electronics": true,
              "parts_any": ["LED_RING_*"],    # at least one of these; exact module id or a prefix ending in *
              "parts_all": ["CORE_ESP32S3_DEVKITC1_N8R8"]}}   # every one of these

Rules
  concept    in the order of the latest concepts message (Expected, Different, Wildcard): the first concept that
             is ready with no warnings, else the first ready one, else one "Show me different concepts" round,
             else the case fails. The record names the rule that chose it.
  buildable  the version finished (state done) AND the engine's CAD collision gate passed (status.buildable is
             true). A held quote, a failed build, a timeout or no build at all is not buildable.
  parts      module ids of the brief that the build used (computed by the server, implied parts included).
  electronics  the design's spec (the built spec.json, else the brief's spec) has a core board or a module (a
             printed-only spec, kind "printed", has none); with no spec at all, whether the build made pcb/ or
             firmware/ files.
  turn lock  HTTP 409 while a turn runs: wait until the project is idle, then send the SAME request again. A
             network break, 429 or 5xx on a chat POST: wait until idle, then read the chat; a new user message
             means the server took it (never sent twice), otherwise back off and resend. GETs retry 429, 5xx and
             network errors with backoff (2, 4, 8, 16, 30 s; Retry-After honoured).
  resume     --resume reads <out>/results.json: finished cases are skipped; an interrupted case that has a
             project carries on from that project's server-side state (nothing is re-sent) with a fresh time
             budget, else starts over. Cases of earlier runs stay in the table and the score (also with --only).
  minutes    the runner's wall time for the case, project creation to the share link (for a resumed case: the
             project's creation to the end of the build on the server).

Output (--out; default studio/_out/bench/<UTC time>, git-ignored):
  results.json   every case record (rewritten after every step; also the resume file)
  summary.md     table (case, buildable, price, parts, failed checks, minutes, share link, expectations), the
                 one-line score ("8 of 12 buildable; median 9.5 min"), errors, stage timings
  events/<case>/<nn>-<step>.jsonl   every SSE event with its arrival time

Pre-deploy engine benchmark (--server-local PORT, on the VM): starts THIS checkout's web server on 127.0.0.1:PORT
with BUILDER=local (the engine runs on the VM toolchain: `python -m gstudio build` per version), AGENT=gemini
(real Gemini on Vertex AI through the VM's service account), PHOTOS=1 (--no-photos skips concept photos), a
local store in <out>/store and a throwaway access key held only in memory (the studio key is not used); runs the
cases; then stops exactly that server and its child processes (never by pattern). Server knobs (MODEL_FAST,
MODEL_PRO, AGENT_THINKING, VERTEX_LOCATION, ...) pass through from the environment. From the laptop:
  wt-vm.sh <worktree> sync
  wt-vm.sh <worktree> run 'mkdir -p _out/bench && cd tools/hack-ready && setsid nohup python bench.py \
      --server-local 9231 --cases bench_cases.example.json --out ../../_out/bench/r1 \
      > ../../_out/bench/r1.log 2>&1 < /dev/null & echo $!'
  wt-vm.sh <worktree> pull _out/bench/r1/summary.md      # also results.json, events, server-9231.log
<out>/store holds every project and full build (tens of MB each): pull single files, not the whole folder.
Share links of a VM-local server point at 127.0.0.1 (open them on the VM or through an SSH tunnel).
If the VM cannot reach Vertex AI, run the same cases against a deployed service with --base instead.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import secrets
import signal
import socket
import statistics
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "wishlabs-bench/1"
UA = "wishlabs-bench/1"
STUDIO = Path(__file__).resolve().parents[2]
SOCK_TIMEOUT = 180            # the server sends ": ping" every 15 s while a stream is idle
MAX_BODY = 4_000_000
DEFAULT_TIMEOUT_MIN = 20.0
POLL_S = 10.0                 # build status polling
IDLE_POLL_S = 8.0             # waiting for a running turn to finish
TURN_STALE_S = 15 * 60        # the server ignores a turn marker older than this (agent/turn.py TURN_STALE_S)
MAX_STEPS = 16                # conversation steps before a case counts as stuck
MAX_NUDGES = 2
MAX_TURN_FAILS = 6            # ambiguous failures (no answer, 429, 5xx) on one chat turn before giving up
NUDGE = "Sounds good. Please go with your suggestions."
RETRY_TEXT = "Please try again."
MODES = {"standard": "fast", "fast": "fast", "deep": "pro", "pro": "pro"}
CASE_KEYS = {"id", "wish", "answers", "mode", "expect", "timeout_min", "note"}
EXPECT_KEYS = {"buildable", "electronics", "parts_any", "parts_all"}
FINAL = ("done", "error")
PASS = ("passed", "pass", "ok", "true", "done")
WARN = ("warning", "warn", "warnings")
SKIP = ("skipped", "not_run", "n/a", "info", "pending", "not_selected")
# outcomes after which --resume must not touch the case again
FINAL_OUTCOMES = ("buildable", "not_buildable", "verdict_unknown", "build_error", "timeout", "agent_error",
                  "agent_stuck", "no_concept", "turn_rejected")


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(iso) -> float | None:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def age_s(iso) -> float:
    t = parse_iso(iso)
    return 1e9 if t is None else time.time() - t


def backoff_s(attempt: int, retry_after: float | None = None) -> float:
    """2, 4, 8, 16, 30, 30 ... seconds; a server Retry-After wins (1..60 s)."""
    if retry_after is not None:
        return max(1.0, min(60.0, retry_after))
    return min(30.0, 2.0 * (2 ** attempt))


# ================================================================================================ cases
class CaseError(ValueError):
    pass


def load_cases(path: str) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8")
    if path.lower().endswith((".yaml", ".yml")):
        try:
            import yaml  # type: ignore
        except ImportError:
            raise CaseError("YAML case files need PyYAML (the VM venv has it), or write the cases as JSON") from None
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise CaseError(f"{path}: not valid YAML: {e}") from None
    else:
        try:
            data = json.loads(text)
        except ValueError as e:
            raise CaseError(f"{path}: not valid JSON: {e}") from None
    return parse_cases(data)


def _str_list(v, where: str) -> list[str]:
    if not isinstance(v, list) or not v or not all(isinstance(x, str) and x.strip() for x in v):
        raise CaseError(f"{where} must be a non-empty list of non-empty strings")
    return [x.strip() for x in v]


def parse_cases(data) -> list[dict]:
    """Validate and normalize the case list (errors name the case and the field)."""
    if isinstance(data, dict):
        data = data.get("cases")
    if not isinstance(data, list) or not data:
        raise CaseError('expected a non-empty list of cases, or {"cases": [...]}')
    out, seen = [], set()
    for i, c in enumerate(data, 1):
        if not isinstance(c, dict):
            raise CaseError(f"case {i}: must be an object")
        cid = str(c.get("id") or "").strip()
        where = f"case {i} ({cid or 'no id'})"
        unknown = sorted(set(c) - CASE_KEYS)
        if unknown:
            raise CaseError(f"{where}: unknown keys {unknown}; allowed {sorted(CASE_KEYS)}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", cid):
            raise CaseError(f"{where}: id must be 1-64 characters of A-Z a-z 0-9 _ . - starting with a letter or digit")
        if cid in seen:
            raise CaseError(f"{where}: duplicate id")
        seen.add(cid)
        wish = c.get("wish")
        if not isinstance(wish, str) or not wish.strip():
            raise CaseError(f"{where}: wish must be a non-empty string")
        if len(wish.strip()) > 4000:
            raise CaseError(f"{where}: wish is longer than the 4000 characters the chat accepts")
        mode = str(c.get("mode") or "standard").strip().lower()
        if mode not in MODES:
            raise CaseError(f"{where}: mode must be standard or deep (or the API names fast / pro)")
        answers = c.get("answers")
        answers = [] if answers is None else answers
        if not isinstance(answers, list):
            raise CaseError(f"{where}: answers must be a list (one entry per round of follow-up questions)")
        for j, a in enumerate(answers, 1):
            w = f"{where}: answers[{j}]"
            if isinstance(a, str):
                if not a.strip():
                    raise CaseError(f"{w} is empty")
            elif isinstance(a, list):
                _str_list(a, w)
            elif isinstance(a, dict):
                if not a or not all(isinstance(k, str) and k.strip() and isinstance(v, (str, int, float))
                                    and not isinstance(v, bool) and str(v).strip() for k, v in a.items()):
                    raise CaseError(f"{w}: an object maps question ids (or words from a question) to non-empty values")
            else:
                raise CaseError(f"{w}: must be a string, a list of strings or an object")
        expect = c.get("expect")
        expect = {} if expect is None else expect
        if not isinstance(expect, dict):
            raise CaseError(f"{where}: expect must be an object")
        bad = sorted(set(expect) - EXPECT_KEYS)
        if bad:
            raise CaseError(f"{where}: unknown expect keys {bad}; allowed {sorted(EXPECT_KEYS)}")
        expect = dict(expect)
        for k in ("buildable", "electronics"):
            if k in expect and not isinstance(expect[k], bool):
                raise CaseError(f"{where}: expect.{k} must be true or false")
        for k in ("parts_any", "parts_all"):
            if k in expect:
                expect[k] = _str_list(expect[k], f"{where}: expect.{k}")
        tmin = c.get("timeout_min")
        if tmin is not None and (isinstance(tmin, bool) or not isinstance(tmin, (int, float)) or not 0 < tmin <= 240):
            raise CaseError(f"{where}: timeout_min must be a number of minutes, more than 0 and at most 240")
        out.append({"id": cid, "wish": wish.strip(), "mode": mode, "api_mode": MODES[mode], "answers": answers,
                    "expect": expect, "timeout_min": float(tmin) if tmin is not None else None,
                    "note": c.get("note")})
    return out


def answer_body(entry, questions: list) -> dict:
    """The chat body for one round of follow-up questions. entry None -> the agent's stated defaults."""
    if entry is None:
        return {"answers": [], "skipped": True}
    if isinstance(entry, str):
        return {"text": entry}
    qs = [q for q in questions or [] if isinstance(q, dict) and q.get("id")]
    pairs = []
    if isinstance(entry, list):
        pairs = list(zip(qs, entry))
    elif isinstance(entry, dict):
        used = set()
        for q in qs:
            text = str(q.get("question") or "").lower()
            for k, v in entry.items():
                if k not in used and (k == q["id"] or k.strip().lower() in text):
                    pairs.append((q, v))
                    used.add(k)
                    break
    answers = [{"question_id": q["id"], "value": str(v).strip()[:300]} for q, v in pairs if str(v).strip()]
    body: dict = {"answers": answers}
    if not answers or len(answers) < len(qs):
        body["skipped"] = True          # the server tells the agent to use its defaults for the rest
    return body


def text_of(entry) -> str | None:
    """An answers entry as one typed message (the agent asked in prose rather than with question chips)."""
    if entry is None:
        return None
    if isinstance(entry, str):
        return entry
    if isinstance(entry, list):
        return ", ".join(str(x) for x in entry)
    return "; ".join(f"{k}: {v}" for k, v in entry.items())


def choose_concept(concepts: list) -> tuple[dict | None, str]:
    """concepts in the order of the concepts message (Expected, Different, Wildcard)."""
    ready = [c for c in concepts if isinstance(c, dict) and c.get("state") == "ready"]
    clean = [c for c in ready if not c.get("warnings")]
    if clean:
        return clean[0], "first ready concept with no warnings"
    if ready:
        return ready[0], "first ready concept (every ready concept has warnings)"
    return None, "no ready concept"


def part_match(pattern: str, part: str) -> bool:
    p, x = pattern.strip().upper(), str(part or "").strip().upper()
    return x.startswith(p[:-1]) if p.endswith("*") else x == p


def check_expect(expect: dict, rec: dict) -> tuple[dict, bool | None]:
    """({key: met, ...}, all met | None when the case has no expectations)."""
    parts = rec.get("parts") or []
    res: dict = {}
    if "buildable" in expect:
        res["buildable"] = bool(rec.get("buildable")) == expect["buildable"]
    if "electronics" in expect:
        res["electronics"] = rec.get("electronics") is not None and rec["electronics"] == expect["electronics"]
    if expect.get("parts_any"):
        res["parts_any"] = any(part_match(p, x) for p in expect["parts_any"] for x in parts)
    if expect.get("parts_all"):
        missing = [p for p in expect["parts_all"] if not any(part_match(p, x) for x in parts)]
        res["parts_all"] = not missing
        if missing:
            res["parts_all_missing"] = missing
    verdicts = [v for v in res.values() if isinstance(v, bool)]
    return res, (all(verdicts) if verdicts else None)


# ================================================================================================ project helpers
def last_assistant(p: dict | None) -> dict | None:
    for m in reversed((p or {}).get("chat") or []):
        if m.get("role") == "assistant":
            return m
    return None


def count_users(p: dict | None) -> int:
    return sum(1 for m in (p or {}).get("chat") or [] if m.get("role") == "user")


def find_version(p: dict | None, n) -> dict | None:
    return next((v for v in (p or {}).get("versions") or [] if v.get("n") == n), None)


def brief_for(p: dict, v: dict | None) -> dict | None:
    """The brief a version was built from (else the latest brief)."""
    if v and v.get("brief_version") is not None:
        b = next((b for b in p.get("brief_history") or [] if b.get("version") == v["brief_version"]), None)
        if b:
            return b
    return p.get("brief")


def parts_of(brief: dict | None, spec: dict | None) -> list[dict]:
    rows = [{"module_id": x.get("module_id"), "qty": x.get("qty"), "role": x.get("role"),
             "implied": x.get("implied")} for x in (brief or {}).get("parts") or [] if x.get("module_id")]
    if rows or not isinstance(spec, dict):
        return rows
    el = spec.get("electronics") if isinstance(spec.get("electronics"), dict) and not is_printed(spec) else {}
    ids = [el.get("core")] + [f.get("module") for f in spec.get("features") or [] if isinstance(f, dict)]
    ids += [m if isinstance(m, str) else (m or {}).get("module") for m in el.get("extra_modules") or []]
    seen: list[str] = []
    for i in ids:
        if i and i not in seen:
            seen.append(i)
    return [{"module_id": i, "qty": None, "role": None, "implied": None} for i in seen]


def is_printed(spec) -> bool:
    """A printed-only object (spec kind "printed", catalog Package A): no core, modules, PCB or firmware."""
    return isinstance(spec, dict) and str(spec.get("kind") or "").strip().lower() == "printed"


def has_electronics(spec) -> bool | None:
    """True when the spec has a core board or any module. A missing electronics block means the engine's default
    core for a gadget; a printed-only spec, an explicit null (or no core and no modules) means none."""
    if not isinstance(spec, dict):
        return None
    if is_printed(spec):
        return False
    feats = any(isinstance(f, dict) and f.get("module") for f in spec.get("features") or [])
    if "electronics" not in spec:
        return True
    el = spec.get("electronics")
    if isinstance(el, dict) and (el.get("core") or el.get("extra_modules")):
        return True
    return feats


def checks_of(dc) -> dict | None:
    """checks/digital-checks.json -> passed/total and the failed checks with their messages (as summary.py counts)."""
    items = []
    if isinstance(dc, dict):
        items = next((dc[k] for k in ("checks", "results", "items") if isinstance(dc.get(k), list)), [])
    elif isinstance(dc, list):
        items = dc
    else:
        return None
    out = {"passed": 0, "total": 0, "failed": [], "warned": []}
    for it in items:
        if not isinstance(it, dict):
            continue
        st = next((it[k] for k in ("status", "passed", "ok", "result") if it.get(k) is not None), None)
        if st is None:
            continue
        if isinstance(st, str):
            low = st.strip().lower()
            if low in SKIP:
                continue
            kind = "passed" if low in PASS else "warned" if low in WARN else "failed"
        else:
            kind = "passed" if bool(st) else "failed"
        out["total"] += 1
        if kind == "passed":
            out["passed"] += 1
            continue
        row = {"id": it.get("id"), "title": str(it.get("title") or it.get("name") or it.get("id") or "check")}
        if kind == "failed":
            row["detail"] = str(it.get("detail") or it.get("message") or "")[:600]
            row["stage"] = it.get("stage")
        out[kind].append(row)
    return out


def stage_rows(status: dict | None) -> list[dict]:
    rows = []
    for sg in (status or {}).get("stages") or []:
        a, b = parse_iso(sg.get("started")), parse_iso(sg.get("ended"))
        rows.append({"id": sg.get("id"), "label": sg.get("label"), "state": sg.get("state"),
                     "seconds": round(b - a, 1) if a is not None and b is not None else None,
                     "message": (sg.get("message") or "")[:300] or None})
    return rows


def quote_of(q) -> dict | None:
    if not isinstance(q, dict):
        return None
    keep = ("status", "status_label", "delivered_price_inr", "list_price_inr", "list_price_incl_gst", "price_inr",
            "total_inr", "currency", "binding", "complete", "reason", "reasons", "held_because")
    out = {k: q[k] for k in keep if k in q}
    if isinstance(out.get("held_because"), list):
        out["held_because"] = out["held_because"][:5]
    out["keys"] = sorted(q)[:40]
    return out


# ================================================================================================ HTTP
class Resp:
    def __init__(self, method: str, path: str):
        self.method, self.path = method, path
        self.status: int | None = None
        self.headers: dict = {}
        self.body = b""
        self.error: str | None = None
        self.wall_s: float | None = None
        self.ttfb_s: float | None = None
        self.attempts = 1
        self.set_cookie: list[str] = []

    def json(self):
        try:
            return json.loads(self.body or b"null")
        except ValueError:
            return None

    def retry_after(self) -> float | None:
        try:
            return float(self.headers.get("retry-after"))
        except (TypeError, ValueError):
            return None

    def err_text(self) -> str | None:
        if self.error:
            return self.error
        if self.status is not None and self.status >= 400:
            j = self.json()
            if isinstance(j, dict) and isinstance(j.get("error"), dict):
                return f"HTTP {self.status} {j['error'].get('code')}: {j['error'].get('message')}"
            return f"HTTP {self.status}: {self.body[:300].decode('utf-8', 'replace')}"
        return None


class _Raw:
    """http.client response + its connection (what an opener returns)."""

    def __init__(self, conn, resp):
        self.conn, self.resp, self.status = conn, resp, resp.status

    def getheaders(self):
        return self.resp.getheaders()

    def read(self, n: int = -1) -> bytes:
        return self.resp.read() if n is None or n < 0 else self.resp.read(n)

    def read1(self, n: int) -> bytes:
        return self.resp.read1(n)

    def close(self):
        try:
            self.resp.close()
        finally:
            self.conn.close()


class LoginError(Exception):
    pass


class Http:
    """One base URL and one session cookie, shared by every job (one login per run).

    opener(method, path, headers, body_bytes, timeout) -> object with .status, getheaders(), read(n), read1(n),
    close(); it raises OSError on a network failure. Tests pass a fake one."""

    def __init__(self, base: str, opener=None, sleep=time.sleep):
        u = urllib.parse.urlsplit((base or "").strip())
        if u.scheme not in ("http", "https") or not u.netloc:
            raise ValueError(f"--base must be an http(s) URL, got {base!r}")
        self.scheme, self.netloc, self.prefix = u.scheme, u.netloc, u.path.rstrip("/")
        self.base = f"{u.scheme}://{u.netloc}{self.prefix}"
        self.origin = f"{u.scheme}://{u.netloc}"
        self.opener = opener or self._open
        self.sleep = sleep
        self.cookie: str | None = None

    def _open(self, method, path, headers, data, timeout):
        cls = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        conn = cls(self.netloc, timeout=timeout)
        try:
            conn.request(method, self.prefix + path, body=data, headers=headers)
            return _Raw(conn, conn.getresponse())
        except BaseException:
            conn.close()
            raise

    def headers(self, accept: str = "application/json", body: bool = False) -> dict:
        h = {"Accept": accept, "User-Agent": UA}
        if body:
            h["Content-Type"] = "application/json"
        if self.cookie:
            h["Cookie"] = self.cookie
        return h

    def once(self, method: str, path: str, body=None, timeout: float = 60, max_body: int = MAX_BODY) -> Resp:
        r = Resp(method, path)
        t = time.time()
        data = json.dumps(body).encode() if body is not None else None
        raw = None
        try:
            raw = self.opener(method, path, self.headers(body=data is not None), data, timeout)
            r.ttfb_s = round(time.time() - t, 3)
            r.status = raw.status
            hs = list(raw.getheaders())
            r.headers = {k.lower(): v for k, v in hs}
            r.set_cookie = [v for k, v in hs if k.lower() == "set-cookie"]
            if method != "HEAD":
                b = raw.read(max_body + 1)
                r.body = b[:max_body]
        except (OSError, http.client.HTTPException) as e:
            r.error = f"{type(e).__name__}: {e}"
        finally:
            if raw is not None:
                try:
                    raw.close()
                except Exception:  # noqa: BLE001
                    pass
        r.wall_s = round(time.time() - t, 3)
        return r

    def call(self, method: str, path: str, body=None, *, retries: int | None = None, timeout: float = 60,
             deadline: float | None = None, max_body: int = MAX_BODY) -> Resp:
        """One request; network errors, 429 and 5xx are retried with backoff (default: 5 extra tries for GET/HEAD,
        none for other methods) as long as the wait ends before `deadline`."""
        if retries is None:
            retries = 5 if method in ("GET", "HEAD") else 0
        attempt = 0
        while True:
            r = self.once(method, path, body, timeout, max_body)
            r.attempts = attempt + 1
            retryable = r.error is not None or r.status is None or r.status == 429 or r.status >= 500
            if not retryable or attempt >= retries:
                return r
            wait = backoff_s(attempt, r.retry_after())
            if deadline is not None and time.time() + wait > deadline:
                return r
            self.sleep(wait)
            attempt += 1

    def login(self, key: str) -> dict:
        """POST /api/login once. Retried only when no answer came back at all: a refused key is never retried
        (the service locks the IP after 10 bad keys in 10 minutes). The key is never logged."""
        r = None
        for attempt in range(3):
            r = self.once("POST", "/api/login", {"key": key}, 30, 65536)
            if r.status is not None:
                break
            self.sleep(5 * (attempt + 1))
        for sc in r.set_cookie:
            if sc.startswith("gs_session="):
                self.cookie = sc.split(";", 1)[0]
        if r.status != 200 or not self.cookie:
            raise LoginError(f"login refused: {r.err_text() or 'HTTP ' + str(r.status) + ', no session cookie'}")
        return r.json() or {}

    def sse(self, path: str, body: dict, on_event, deadline: float | None = None,
            timeout: float = SOCK_TIMEOUT, stop=None) -> dict:
        """POST a chat turn and read its event stream to the end (or the deadline, or until stop() is true; the
        server's 15 s pings bound the wait). on_event(name, data, t)."""
        res = {"status": None, "error": None, "ttfb_s": None, "wall_s": None, "events": 0, "complete": False,
               "deadline_hit": False, "stopped": False, "body": b"", "retry_after": None}
        t = time.time()
        raw = None
        try:
            raw = self.opener("POST", path, self.headers("text/event-stream", True), json.dumps(body).encode(),
                              timeout)
            res["ttfb_s"] = round(time.time() - t, 3)
            res["status"] = raw.status
            hs = {k.lower(): v for k, v in raw.getheaders()}
            try:
                res["retry_after"] = float(hs.get("retry-after"))
            except (TypeError, ValueError):
                pass
            if raw.status >= 400:
                res["body"] = raw.read(65536)
            else:
                buf = b""
                while True:
                    if deadline is not None and time.time() > deadline:
                        res["deadline_hit"] = True
                        break
                    if stop is not None and stop():
                        res["stopped"] = True
                        break
                    chunk = raw.read1(65536)
                    if not chunk:
                        break
                    buf = (buf + chunk).replace(b"\r\n", b"\n")
                    while b"\n\n" in buf:
                        block, buf = buf.split(b"\n\n", 1)
                        ev, data = parse_sse_block(block)
                        if ev is None:
                            continue
                        res["events"] += 1
                        on_event(ev, data, round(time.time() - t, 2))
                        if ev == "done":
                            res["complete"] = True
        except (OSError, http.client.HTTPException) as e:
            res["error"] = f"{type(e).__name__}: {e}"
        finally:
            if raw is not None:
                try:
                    raw.close()
                except Exception:  # noqa: BLE001
                    pass
        res["wall_s"] = round(time.time() - t, 2)
        return res


def parse_sse_block(block: bytes):
    ev, data = None, []
    for line in block.decode("utf-8", "replace").split("\n"):
        if not line or line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if name == "event":
            ev = value
        elif name == "data":
            data.append(value)
    if ev is None and not data:
        return None, None
    raw = "\n".join(data)
    try:
        d = json.loads(raw) if raw else None
    except ValueError:
        d = raw
    return ev or "message", d


class EventLog:
    """Writes each SSE event of one turn with its arrival time; keeps what the runner needs from the stream."""

    def __init__(self, path: Path, on_progress=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.on_progress = on_progress
        self.f = open(path, "w", encoding="utf-8")
        self.produced: list = []        # kinds of the assistant messages this turn stored
        self.errors: list = []
        self.repairs: list = []         # spec validation repair rounds the agent went through
        self.project: dict | None = None
        self.build_version = None
        self.timeline: dict = {}
        self.counts: dict = {}

    def on_event(self, ev: str, data, t: float) -> None:
        self.counts[ev] = self.counts.get(ev, 0) + 1
        self.timeline.setdefault(f"first_{ev}", t)
        d = data if isinstance(data, dict) else {}
        keep = data
        if ev == "project":
            self.project = d.get("project") if isinstance(d.get("project"), dict) else None
            p = self.project or {}
            keep = {"id": p.get("id"), "stage": p.get("stage"), "current_version": p.get("current_version")}
        elif ev == "message.done":
            m = d.get("message") or {}
            if m.get("role") == "assistant":
                self.produced.append(m.get("kind"))
                if m.get("kind") == "error":
                    self.errors.append({"t": t, "text": (m.get("text") or "")[:600], "payload": m.get("payload")})
        elif ev == "error":
            self.errors.append({"t": t, "code": d.get("code"), "message": (d.get("message") or "")[:600]})
        elif ev == "repair":
            self.repairs.append({"t": t, "round": d.get("round"), "kind": d.get("kind") or "spec",
                                 "errors": [str(x)[:300] for x in (d.get("errors") or [])[:6]]})
        elif ev == "build.started":
            v = d.get("version")
            self.build_version = v.get("n") if isinstance(v, dict) else v
        elif ev == "build.progress":       # the full status rides on every progress event: keep its gist
            st = d.get("status") or {}
            keep = {"version_n": d.get("version_n"), "state": st.get("state"), "percent": st.get("percent"),
                    "stages": [[s.get("id"), s.get("state")] for s in st.get("stages") or [] if isinstance(s, dict)],
                    "buildable": st.get("buildable")}
            if self.on_progress:
                self.on_progress(d.get("version_n"), st)
        self.f.write(json.dumps({"t": t, "event": ev, "data": keep}, ensure_ascii=False) + "\n")
        self.f.flush()

    def close(self) -> None:
        self.f.close()


# ================================================================================================ results
class Results:
    """results.json, rewritten atomically after every update (the resume file)."""

    def __init__(self, path: Path, previous: dict | None = None):
        self.path = path
        self.lock = threading.Lock()
        self.data = previous or {"schema": SCHEMA, "runs": [], "order": [], "cases": {}}
        self.data.setdefault("runs", [])
        self.data.setdefault("cases", {})

    def get(self, cid: str) -> dict | None:
        with self.lock:
            rec = self.data["cases"].get(cid)
            return json.loads(json.dumps(rec)) if rec is not None else None

    def put(self, rec: dict) -> None:
        with self.lock:
            self.data["cases"][rec["id"]] = json.loads(json.dumps(rec, default=str))
            self._write()

    def save(self) -> None:
        with self.lock:
            self._write()

    def _write(self) -> None:
        self.data["updated_utc"] = now_utc()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False))
        os.replace(tmp, self.path)


def new_record(case: dict, base: str) -> dict:
    return {"id": case["id"], "wish": case["wish"], "mode": case["mode"], "api_mode": case["api_mode"],
            "expect": case["expect"], "base": base, "finished": False, "outcome": None, "runs": 0,
            "project_id": None, "project_name": None, "share_url": None, "started_utc": now_utc(),
            "started_epoch": time.time(), "ended_utc": None, "minutes": None, "marks": {}, "timings_s": {},
            "turns": [], "questions": [], "answer_rounds_used": 0, "concepts": [], "concept_choice": None,
            "brief": None, "build_version": None, "build": None, "build_progress": None, "price": None,
            "quote": None, "checks": None, "parts": [], "parts_chosen": [], "parts_detail": [], "electronics": None,
            "buildable": False, "usage_totals": None, "result_text": None, "error": None, "notes": [],
            "expect_results": {}, "expectations_met": None}


def _cell(s, limit: int = 400) -> str:
    s = " ".join(str(s if s is not None else "").split())
    if len(s) > limit:
        s = s[:limit - 3] + "..."
    return s.replace("|", "\\|") or "-"


def case_rows(data: dict) -> list[dict]:
    """One row per case in the run's order; a case that never started is a stub (it counts as unfinished)."""
    return [data["cases"].get(i) or {"id": i, "outcome": "not started", "finished": False}
            for i in data.get("order") or []]


def score_line(data: dict) -> str:
    rows = case_rows(data)
    done = [r for r in rows if r.get("finished")]
    nb = sum(1 for r in rows if r.get("buildable"))
    mins = [r["minutes"] for r in done if isinstance(r.get("minutes"), (int, float))]
    s = f"{nb} of {len(rows)} buildable"
    if mins:
        s += f"; median {statistics.median(mins):.1f} min"
    exp = [r for r in done if r.get("expectations_met") is not None]
    if exp:
        s += f"; expectations met {sum(1 for r in exp if r['expectations_met'])} of {len(exp)}"
    if len(done) < len(rows):
        s += f" ({len(rows) - len(done)} unfinished)"
    return s


def summary_md(data: dict) -> str:
    rows = case_rows(data)
    runs = data.get("runs") or [{}]
    run = runs[-1]
    out = ["# Wish Labs bench", "",
           f"Base: {run.get('base')}  ", f"Server: {json.dumps(run.get('health'))}  ",
           f"Cases: {run.get('cases_file')}; jobs {run.get('jobs')}; timeout {run.get('timeout_min')} min  ",
           f"Runs: {len(runs)} (started {', '.join(str(x.get('started_utc')) for x in runs)})  ",
           f"Updated: {data.get('updated_utc') or now_utc()}", "",
           f"**Score: {score_line(data)}**", "",
           "| Case | Buildable | Price | Parts | Failed checks | Minutes | Share link | Expectations |",
           "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if r.get("buildable"):
            b = "yes"
        else:
            why = r.get("outcome") or ("running" if not r.get("finished") else "unknown")
            prog = r.get("build_progress") or {}
            if why == "timeout" and prog.get("percent") is not None:
                why += f" at {prog.get('percent')}%"
            b = f"no ({why})"
        pr = r.get("price") or {}
        if pr.get("inr") is not None:
            price = pr.get("label") or f"Rs {pr['inr']:,.0f}"
        elif str(pr.get("status") or "").upper() == "HELD":
            price = "held (collision check failed)"
        else:
            price = pr.get("label") or "-"
        fails = (r.get("checks") or {}).get("failed") or []
        fc = "; ".join(f"{f.get('id') or f.get('title')}: {_cell(f.get('detail'), 110)}" if f.get("detail")
                       else str(f.get("id") or f.get("title")) for f in fails) or ("0" if r.get("checks") else "-")
        exp = r.get("expect_results") or {}
        if r.get("expectations_met") is None:
            e = "-"
        elif r["expectations_met"]:
            e = "met"
        else:
            miss = [k for k, v in exp.items() if v is False]
            e = "NOT met: " + ", ".join(miss) + (f" (missing {', '.join(exp['parts_all_missing'])})"
                                                 if exp.get("parts_all_missing") else "")
        mins = f"{r['minutes']:.1f}" if isinstance(r.get("minutes"), (int, float)) else "-"
        out.append(f"| {_cell(r['id'])} | {_cell(b)} | {_cell(price)} | {_cell(', '.join(r.get('parts_chosen') or r.get('parts') or []), 600)} "
                   f"| {_cell(fc, 700)} | {mins} | {_cell(r.get('share_url'))} | {_cell(e)} |")
    errs = [r for r in rows if r.get("error")]
    if errs:
        out += ["", "## Errors", ""]
        out += [f"- **{r['id']}** ({r.get('outcome')}): {_cell(r['error'], 1500)}" for r in errs]
    out += ["", "## Concept choices", ""]
    for r in rows:
        cc = r.get("concept_choice") or {}
        # the case's answers reach the agent only when it asks: say how many were used
        asked = (f"; questions {len(r.get('questions') or [])} round(s), case answers used "
                 f"{r.get('answer_rounds_used') or 0} of {r['case_answers']}" if r.get("case_answers") else "")
        if cc:
            out.append(f"- **{r['id']}**: {cc.get('title')} ({cc.get('tier')}, #{(cc.get('index') or 0) + 1} of "
                       f"{cc.get('of')}): {cc.get('rule')}{asked}")
        else:
            out.append(f"- **{r['id']}**: none{asked}")
    stage_ids: list[str] = []
    for r in rows:
        for s in (r.get("build") or {}).get("stages") or []:
            if s.get("id") and s["id"] not in stage_ids:
                stage_ids.append(s["id"])
    cols = ["create", "wish_to_concepts", "questions", "concepts_to_brief", "build", "collect", "total"]
    out += ["", "## Timings (seconds)", "",
            "| Case | " + " | ".join(cols) + (" | " + " | ".join(stage_ids) if stage_ids else "")
            + " | repair rounds | model USD (est.) |",
            "|---" * (3 + len(cols) + len(stage_ids)) + "|"]
    for r in rows:
        t = r.get("timings_s") or {}
        st = {s.get("id"): s.get("seconds") for s in (r.get("build") or {}).get("stages") or []}
        cost = (r.get("usage_totals") or {}).get("cost_usd_est")
        cells = [str(t.get(c, "-")) for c in cols] + [str(st.get(s) if st.get(s) is not None else "-")
                                                    for s in stage_ids]
        cells += [str(r.get("repair_rounds", "-")), f"{cost:.3f}" if isinstance(cost, (int, float)) else "-"]
        out.append(f"| {_cell(r['id'])} | " + " | ".join(cells) + " |")
    out += ["", "Buildable = the version finished and the engine's CAD collision gate passed (status.buildable). "
            "Parts = the design's own modules (implied parts such as cables and the backplane are in results.json "
            "and count for parts_any / parts_all). Minutes = the runner's wall time per case, project creation to "
            "share link. Builder stage seconds come from the version's status.json. Repair rounds = distinct spec "
            "validation rounds the agent hit (the server's own concept count is concept_set_check in results.json); "
            "model USD = the project's usage estimate (not billing data).", ""]
    return "\n".join(out)


# ================================================================================================ one case
class CaseFail(Exception):
    def __init__(self, outcome: str, message: str):
        super().__init__(message)
        self.outcome = outcome


class CaseTimeout(Exception):
    pass


class Unauthorized(Exception):
    pass


class Stopped(Exception):
    pass


class CaseRun:
    def __init__(self, runner: "Runner", case: dict, prev: dict | None):
        self.R, self.h, self.c = runner, runner.http, case
        self.resumed = bool(prev and prev.get("project_id") and not prev.get("finished"))
        self.rec = prev if self.resumed else new_record(case, runner.http.base)
        self.rec["runs"] = int(self.rec.get("runs") or 0) + 1
        self.rec.update(finished=False, outcome=None, error=None)
        self.run_t0 = time.time()
        self.timeout_s = 60.0 * (case.get("timeout_min") or runner.timeout_min)
        if self.resumed:      # a fresh budget: the time the runner was down must not turn into a false timeout
            self.rec["notes"].append(f"resumed {now_utc()} on project {self.rec['project_id']}")
        self.deadline = time.time() + self.timeout_s
        self.ev_dir = runner.out / "events" / case["id"]
        self.answers_left = list(case["answers"])[int(self.rec.get("answer_rounds_used") or 0):]
        self.nudges = self.agent_retries = self.regens = 0
        self.p: dict | None = None

    # ---------------------------------------------------------------- helpers
    @property
    def pid(self) -> str:
        return self.rec["project_id"]

    def say(self, msg: str) -> None:
        self.R.say(f"[{self.c['id']}] {msg}")

    def save(self) -> None:
        self.R.results.put(self.rec)

    def check(self) -> None:
        if self.R.stop.is_set():
            raise Stopped()
        if time.time() > self.deadline:
            raise CaseTimeout()

    def mark(self, k: str, first: bool = True) -> None:
        if first and k in self.rec["marks"]:
            return
        self.rec["marks"][k] = round(time.time() - float(self.rec["started_epoch"]), 1)

    def get(self, path: str, **kw) -> Resp:
        r = self.h.call("GET", path, deadline=self.deadline + 300, **kw)
        if r.status == 401:
            raise Unauthorized(path)
        return r

    def project(self) -> dict:
        r = self.get(f"/api/projects/{self.pid}")
        p = r.json()
        if r.status != 200 or not isinstance(p, dict) or not p.get("id"):
            raise CaseFail("runner_error", f"could not read the project: {r.err_text() or r.status}")
        self.p = p
        return p

    def wait_idle(self) -> dict:
        """Poll the project until no turn runs (a marker older than the server's 15 min stale limit counts as none)."""
        t = time.time()
        while True:
            p = self.project()
            tr = p.get("turn")
            if not tr or age_s(tr.get("started")) >= TURN_STALE_S:
                if time.time() - t > 1:
                    self.rec["waited_idle_s"] = round(float(self.rec.get("waited_idle_s") or 0) + time.time() - t, 1)
                return p
            self.check()
            self.R.sleep(self.R.idle_poll_s)

    # ---------------------------------------------------------------- the flow
    def run(self) -> None:
        rec = self.rec
        self.say(("resume" if self.resumed else "start") + f" ({self.c['mode']}): {self.c['wish'][:90]}")
        final, collect = True, True
        try:
            if not rec.get("project_id"):
                self.create()
            self.converse()
            self.finish_build()
        except CaseTimeout:
            rec["outcome"] = "timeout"
            rec["error"] = f"case timeout ({self.timeout_s / 60:g} min) reached"
        except CaseFail as e:
            rec["outcome"], rec["error"] = e.outcome, str(e)
            final = e.outcome in FINAL_OUTCOMES
        except Unauthorized:
            self.R.stop.set()
            rec["outcome"] = "runner_error"
            rec["error"] = "HTTP 401: the session is not valid any more (key rotated?); the run stops"
            final = collect = False
        except Stopped:
            rec["outcome"] = "interrupted"      # not final: --resume carries on from the project's state
            rec["notes"].append(f"interrupted {now_utc()}")
            final = collect = False
        except Exception as e:  # noqa: BLE001 - one case must never take the run down
            rec["outcome"], rec["error"] = "runner_error", f"{type(e).__name__}: {e}"
            rec["traceback"] = traceback.format_exc()[-3000:]
            final = False
        if collect and rec.get("project_id"):
            try:
                self.collect()
            except Exception as e:  # noqa: BLE001
                rec["notes"].append(f"collect failed: {type(e).__name__}: {e}")
        if rec.get("outcome") is None:
            b = rec.get("build") or {}
            rec["outcome"] = ("buildable" if rec.get("buildable") else "build_error" if b.get("state") == "error"
                              else "not_buildable" if b.get("buildable") is False
                              else "verdict_unknown" if b.get("state") == "done" else "runner_error")
            if rec["outcome"] == "runner_error":
                rec["error"] = rec.get("error") or "the build did not reach a final state"
                final = False
        # distinct rounds per turn: the server re-emits a concept round it no longer grants (agent/turn.py:529)
        rec["repair_rounds"] = sum(len({(x.get("kind"), x.get("round")) for x in t.get("repairs") or []})
                                   for t in rec.get("turns") or [])
        rec["expect_results"], rec["expectations_met"] = check_expect(self.c["expect"], rec)
        rec["case_answers"] = len(self.c["answers"])
        rec["ended_utc"] = now_utc()
        rec["timings_s"]["total"] = round(time.time() - float(rec["started_epoch"]), 1)
        if self.resumed and rec.get("server_span_s"):
            rec["minutes"] = round(rec["server_span_s"] / 60, 1)
        else:
            rec["minutes"] = round((time.time() - float(rec["started_epoch"])) / 60, 1)
        rec["finished"] = final
        self.save()
        price = (rec.get("price") or {}).get("label")
        self.say(f"end: {rec['outcome']}; buildable={rec['buildable']}; {price or 'no price'}; "
                 f"{rec['minutes']} min; expectations={rec['expectations_met']}"
                 + (f"; error: {rec['error'][:300]}" if rec.get("error") else ""))

    def create(self) -> None:
        t = time.time()
        r = self.h.call("POST", "/api/projects", {"mode": self.c["api_mode"]}, retries=3, deadline=self.deadline)
        if r.status == 401:
            raise Unauthorized("/api/projects")
        p = r.json()
        if r.status != 201 or not isinstance(p, dict) or not p.get("id"):
            raise CaseFail("runner_error", f"create project: {r.err_text() or r.status}")
        if r.attempts > 1:
            self.rec["notes"].append(f"create project took {r.attempts} tries (an empty orphan project is possible)")
        self.rec["project_id"], self.p = p["id"], p
        self.rec["timings_s"]["create"] = round(time.time() - t, 2)
        self.say(f"project {p['id']}")
        self.save()

    def converse(self) -> dict:
        """Drive the conversation from the project's server-side state until a version exists."""
        p = self.p or self.project()
        for _ in range(MAX_STEPS):
            self.check()
            if p.get("versions"):       # a build exists (its turn may still be following it): status polling next
                return p
            tr = p.get("turn")
            if tr and age_s(tr.get("started")) < TURN_STALE_S:
                p = self.wait_idle()
                continue
            if not count_users(p):
                self.mark("wish_sent")
                p = self.turn("wish", {"text": self.c["wish"]})
                continue
            chat = p.get("chat") or []
            m = chat[-1] if chat and chat[-1].get("role") == "assistant" else None
            kind = (m or {}).get("kind")
            if kind == "choices" and not (m.get("payload") or {}).get("answered"):
                p = self.answer(m)
            elif kind == "concepts":
                p = self.pick(p, m)
            elif kind == "brief":
                p = self.approve(p)
            elif kind == "error":
                pl = m.get("payload") or {}
                if self.agent_retries < 1 and pl.get("retryable", True):
                    self.agent_retries += 1
                    self.rec["notes"].append(f"agent error, retried once: {(m.get('text') or '')[:300]}")
                    p = self.turn(f"retry{self.agent_retries}", {"text": RETRY_TEXT})
                else:
                    raise CaseFail("agent_error", f"the agent stopped with an error: {(m.get('text') or '')[:600]}")
            else:   # a prose reply (or a question asked in text), or a user message without an answer
                if self.nudges >= MAX_NUDGES:
                    raise CaseFail("agent_stuck", f"no questions, concepts or brief after {MAX_NUDGES} nudges; "
                                                  f"last reply ({kind}): {((m or {}).get('text') or '')[:400]}")
                self.nudges += 1
                reply = None
                if self.answers_left:
                    reply = text_of(self.answers_left.pop(0))
                    self.rec["answer_rounds_used"] = int(self.rec.get("answer_rounds_used") or 0) + 1
                self.rec["notes"].append(f"agent replied with {kind} ({((m or {}).get('text') or '')[:200]}); "
                                         f"sent {'the next case answer' if reply else 'a nudge'}")
                p = self.turn(f"nudge{self.nudges}", {"text": reply or NUDGE})
        raise CaseFail("agent_stuck", f"no build after {MAX_STEPS} conversation steps")

    def answer(self, m: dict) -> dict:
        pl = m.get("payload") or {}
        qs = pl.get("questions") or []
        entry = self.answers_left.pop(0) if self.answers_left else None
        if entry is not None:
            self.rec["answer_rounds_used"] = int(self.rec.get("answer_rounds_used") or 0) + 1
        body = answer_body(entry, qs)
        rnd = {"round": len(self.rec["questions"]) + 1, "lead": (m.get("text") or "")[:800],
               "questions": [{"id": q.get("id"), "question": q.get("question"), "options": q.get("options")}
                             for q in qs if isinstance(q, dict)],
               "defaults": pl.get("defaults"), "source": "case answers" if entry is not None else "agent defaults",
               "sent": body}
        self.rec["questions"].append(rnd)
        self.save()
        self.say(f"questions round {rnd['round']}: {len(qs)} question(s); answering with {rnd['source']}")
        return self.turn(f"answers{rnd['round']}", body)

    def pick(self, p: dict, m: dict) -> dict:
        self.mark("concepts_ready")
        ids = (m.get("payload") or {}).get("concept_ids") or []
        cmap = {c.get("id"): c for c in p.get("concepts") or [] if isinstance(c, dict)}
        cs = [cmap[i] for i in ids if i in cmap]
        self.rec["concepts"] = [{"id": c.get("id"), "title": c.get("title"), "tier": c.get("tier"),
                                 "state": c.get("state"), "warnings": c.get("warnings") or [],
                                 "photo": bool(c.get("photo_url")), "photo_note": c.get("photo_note")} for c in cs]
        sc = (m.get("payload") or {}).get("set_check")
        if isinstance(sc, dict):           # the server's own concept-set verdict and repair counts
            self.rec["concept_set_check"] = {k: sc.get(k) for k in ("ok", "rule_rounds", "repair_rounds", "failures")}
        chosen, rule = choose_concept(cs)
        if chosen is None:
            if self.regens < 1:
                self.regens += 1
                self.rec["notes"].append(f"none of {len(cs)} concepts was ready: asked once for different concepts")
                return self.turn("regenerate-concepts", {"action": "regenerate_concepts"})
            raise CaseFail("no_concept", f"none of {len(cs)} concepts could be rendered, even after one new round")
        self.rec["concept_choice"] = {"id": chosen.get("id"), "title": chosen.get("title"),
                                      "tier": chosen.get("tier"), "index": cs.index(chosen), "of": len(cs),
                                      "rule": rule, "warnings": chosen.get("warnings") or []}
        self.say(f"concept: {chosen.get('title')} ({chosen.get('tier')}), {rule}")
        self.mark("select_sent")
        return self.turn("select-concept", {"action": "select_concept", "concept_id": chosen["id"]})

    def approve(self, p: dict) -> dict:
        self.mark("brief_ready")
        b = p.get("brief") or {}
        self.rec["brief"] = {"version": b.get("version"), "title": b.get("title"),
                             "summary": ((b.get("summary") or {}).get("value") or "")[:400] or None,
                             "size": (b.get("size") or {}).get("value"), "safety_flags": b.get("safety_flags"),
                             "parts": [x.get("module_id") for x in b.get("parts") or [] if x.get("module_id")]}
        self.mark("build_sent")
        self.say(f"brief v{b.get('version')} ({b.get('title')}): Build this")
        return self.turn("build", {"action": "approve_brief"}, build=True)

    def turn(self, step: str, body: dict, build: bool = False) -> dict:
        """Send one chat turn exactly once and return the project after it (see "turn lock" in the docstring)."""
        users_before = count_users(self.p)
        entry = {"n": len(self.rec["turns"]) + 1, "step": step, "request": body, "sent_utc": now_utc(),
                 "attempts": 0, "waits_409": 0}
        self.rec["turns"].append(entry)
        t0 = time.time()
        fails = 0
        try:
            while True:
                self.check()
                entry["attempts"] += 1
                name = f"{entry['n']:02d}-{step}" + (f"-try{entry['attempts']}" if entry["attempts"] > 1 else "")
                log = EventLog(self.ev_dir / f"{name}.jsonl", on_progress=self.note_progress if build else None)
                try:
                    res = self.h.sse(f"/api/projects/{self.pid}/chat", body, log.on_event, deadline=self.deadline,
                                     stop=self.R.stop.is_set)
                finally:
                    log.close()
                st = res["status"]
                entry.update(status=st, ttfb_s=res["ttfb_s"], stream_s=res["wall_s"], events=log.counts,
                             produced=log.produced, timeline=log.timeline)
                if log.errors:
                    entry["turn_errors"] = log.errors
                if log.repairs:
                    entry["repairs"] = entry.get("repairs", []) + log.repairs
                if log.build_version is not None:
                    self.rec["build_version"] = entry["build_version"] = log.build_version
                if res.get("stopped"):              # the run is stopping; the server finishes this turn alone
                    entry["stopped"] = True
                    raise Stopped()
                if st is not None and 200 <= st < 300:
                    if res["deadline_hit"]:
                        raise CaseTimeout()
                    if not res["complete"]:     # the stream broke: the server has the turn and finishes it
                        entry["stream_broke"] = res["error"] or "the stream ended without a done event"
                        self.say(f"{step}: stream broke ({entry['stream_broke'][:120]}); reading the project")
                        return self.project() if build else self.wait_idle()
                    if build or not log.project:
                        return self.project()
                    self.p = log.project
                    return self.p
                if st == 401:
                    raise Unauthorized(step)
                if st == 409:
                    entry["waits_409"] += 1
                    msg = _err_message(res["body"])
                    p = self.project()
                    lock = "still answering" in msg.lower() or bool(p.get("turn"))
                    self.say(f"{step}: 409 ({msg[:100]}); " + ("waiting for the running turn" if lock
                                                               else "re-reading the project state"))
                    if lock:
                        p = self.wait_idle()
                    if count_users(p) > users_before:      # an earlier try of ours did reach the server
                        entry["server_got_it"] = True
                        return p
                    if lock:
                        continue                            # the SAME request again, now that the project is idle
                    entry["conflict"] = msg
                    return p                                # not a turn lock: the caller looks at the state again
                if st is not None and 400 <= st < 500 and st not in (408, 429):
                    raise CaseFail("turn_rejected", f"{step}: HTTP {st}: {_err_message(res['body'])}")
                # no answer, 408, 429 or 5xx: did the server take it? (never send twice)
                err = res["error"] or f"HTTP {st}: {_err_message(res['body'])}"
                entry.setdefault("failures", []).append(err[:300])
                p = self.wait_idle()
                if count_users(p) > users_before:
                    entry["server_got_it"] = True
                    self.say(f"{step}: {err[:120]}; the server has the message, not resending")
                    return p
                fails += 1
                if fails >= MAX_TURN_FAILS:
                    raise CaseFail("runner_error", f"{step}: no answer after {fails} tries: {err[:300]}")
                wait = backoff_s(fails - 1, res.get("retry_after"))
                self.say(f"{step}: {err[:120]}; not stored, resending in {wait:.0f} s")
                self.R.sleep(wait)
        finally:
            entry["wall_s"] = round(time.time() - t0, 2)
            if step.startswith("answers"):
                self.rec["timings_s"]["questions"] = round(float(self.rec["timings_s"].get("questions") or 0)
                                                           + entry["wall_s"], 2)
            self.save()

    def finish_build(self) -> None:
        p = self.p or self.project()
        n = self.rec.get("build_version") or p.get("current_version") or max(
            [v.get("n") or 0 for v in p.get("versions") or []] or [0]) or None
        if not n:
            raise CaseFail("runner_error", "no version was started")
        self.rec["build_version"] = n
        v = find_version(p, n) or {}
        if not v.get("finalized"):
            self.poll_status(n)
            for _ in range(24):         # a project read finalizes it (summary + result message)
                p = self.project()
                v = find_version(p, n) or {}
                if v.get("finalized"):
                    break
                self.R.sleep(min(5.0, self.R.poll_s))
        self.mark("build_final")

    def note_progress(self, n, st: dict) -> None:
        """Build progress from the stream or from polling: kept in the record, logged at each 10 % step."""
        if not isinstance(st, dict):
            return
        self.rec["build_progress"] = {"state": st.get("state"), "percent": st.get("percent"), "at": now_utc()}
        try:
            key = (st.get("state"), int(float(st.get("percent") or 0)) // 10)
        except (TypeError, ValueError):
            key = (st.get("state"), None)
        if key != getattr(self, "_progress_key", None):
            self._progress_key = key
            running = [s.get("id") for s in st.get("stages") or [] if isinstance(s, dict) and s.get("state") == "running"]
            self.say(f"build v{n}: {st.get('state')} {st.get('percent')}%" + (f" ({', '.join(running)})" if running else ""))

    def poll_status(self, n: int) -> dict | None:
        while True:
            r = self.get(f"/api/projects/{self.pid}/v/{n}/status")
            st = r.json() if r.status == 200 else None
            if isinstance(st, dict):
                self.note_progress(n, st)
                if st.get("state") in FINAL:
                    return st
            self.check()
            self.R.sleep(self.R.poll_s)

    # ---------------------------------------------------------------- results
    def collect(self) -> None:
        t = time.time()
        rec, pid = self.rec, self.pid
        p = self.project()
        rec["project_name"] = p.get("name")
        rec["stage"] = p.get("stage")
        rec["usage_totals"] = (p.get("usage") or {}).get("totals")
        rec["chat_kinds"] = [f"{m.get('role')}:{m.get('kind')}" for m in p.get("chat") or []]
        n = rec.get("build_version") or p.get("current_version")
        v = find_version(p, n) if n else None
        brief = brief_for(p, v)
        spec = None
        files: list = []
        if v:
            vr = self.get(f"/api/projects/{pid}/v/{n}")
            vd = vr.json() if vr.status == 200 else None
            if not isinstance(vd, dict):    # never call a case finished on a read that failed: --resume retries it
                raise CaseFail("runner_error", f"could not read version {n}: {vr.err_text() or vr.status}")
            st = vd.get("status") or {}
            files = [f.get("path") for f in vd.get("files") or [] if isinstance(f, dict)]
            rec["build"] = {"version": n, "state": v.get("state"), "finalized": v.get("finalized"),
                            "error": v.get("error"), "label": v.get("label"), "percent": v.get("percent"),
                            "status_state": st.get("state"), "buildable": st.get("buildable"),
                            "verdict": st.get("verdict"), "verdict_message": st.get("verdict_message"),
                            "stages": stage_rows(st), "files": len(files), "started": v.get("started"),
                            "ended": v.get("ended")}
            if st.get("state") and rec.get("build_progress") is None:
                rec["build_progress"] = {"state": st.get("state"), "percent": st.get("percent"), "at": now_utc()}
            a, b = parse_iso(p.get("created")), parse_iso(v.get("ended"))
            if a is not None and b is not None:
                rec["server_span_s"] = round(b - a, 1)
            res = next((m for m in reversed(p.get("chat") or []) if m.get("kind") in ("result", "error")
                        and (m.get("payload") or {}).get("version") == n), None)
            if res:
                summ = (res.get("payload") or {}).get("summary") or {}
                rec["price"] = summ.get("price")
                rec["result_text"] = (res.get("text") or "")[:2000]
            if v.get("state") in FINAL or st.get("state") in FINAL:
                q = self.get(f"/api/projects/{pid}/v/{n}/files/order/quote.json", retries=2)
                rec["quote"] = quote_of(q.json()) if q.status == 200 else None
                dc = self.get(f"/api/projects/{pid}/v/{n}/files/checks/digital-checks.json", retries=2)
                if dc.status != 200:
                    dc = self.get(f"/api/projects/{pid}/v/{n}/files/cad/digital-checks.json", retries=2)
                rec["checks"] = checks_of(dc.json()) if dc.status == 200 else None
                sp = self.get(f"/api/projects/{pid}/v/{n}/files/spec.json", retries=2)
                spec = sp.json() if sp.status == 200 else None
            if rec.get("price") is None and rec.get("quote"):
                qq = rec["quote"]
                inr = next((qq[k] for k in ("delivered_price_inr", "list_price_inr", "list_price_incl_gst",
                                            "price_inr", "total_inr") if isinstance(qq.get(k), (int, float))), None)
                rec["price"] = {"inr": inr, "status": qq.get("status"),
                                "label": f"Rs {inr:,.0f} ({qq.get('status')})" if inr is not None
                                else f"no price ({qq.get('status')})"}
        if spec is None and brief:
            spec = brief.get("spec")
        rows = parts_of(brief, spec)
        rec["parts_detail"] = rows
        rec["parts"] = list(dict.fromkeys(r["module_id"] for r in rows))
        rec["parts_chosen"] = list(dict.fromkeys(r["module_id"] for r in rows if not r.get("implied")))
        rec["electronics"] = has_electronics(spec)     # the design's own spec: a core board or any module
        if rec["electronics"] is None and files:        # no spec at all: did the build make a board or firmware?
            rec["electronics"] = any(str(f).startswith(("pcb/", "firmware/")) for f in files)
        rec["buildable"] = bool(v and v.get("state") == "done" and (rec.get("build") or {}).get("buildable") is True)
        s = self.h.call("POST", f"/api/projects/{pid}/share", {}, retries=3, deadline=time.time() + 120)
        j = s.json() if s.status == 200 else None
        if isinstance(j, dict) and j.get("url"):
            rec["share_url"] = urllib.parse.urljoin(self.h.origin + "/", str(j["url"]))
        else:
            rec["notes"].append(f"share link failed: {s.err_text() or s.status}")
        rec["timings_s"]["collect"] = round(time.time() - t, 2)
        mk = rec.get("marks") or {}
        if "wish_sent" in mk and "concepts_ready" in mk:
            rec["timings_s"]["wish_to_concepts"] = round(mk["concepts_ready"] - mk["wish_sent"], 1)
        if "select_sent" in mk and "brief_ready" in mk:
            rec["timings_s"]["concepts_to_brief"] = round(mk["brief_ready"] - mk["select_sent"], 1)
        if "build_sent" in mk and "build_final" in mk:
            rec["timings_s"]["build"] = round(mk["build_final"] - mk["build_sent"], 1)
        self.save()


def _err_message(body: bytes) -> str:
    try:
        j = json.loads(body or b"null")
    except ValueError:
        j = None
    if isinstance(j, dict) and isinstance(j.get("error"), dict):
        return str(j["error"].get("message") or j["error"].get("code") or "")
    return (body or b"")[:300].decode("utf-8", "replace")


# ================================================================================================ the run
class Runner:
    def __init__(self, http_: Http, cases: list[dict], out, *, jobs: int = 2,
                 timeout_min: float = DEFAULT_TIMEOUT_MIN, resume: bool = False, poll_s: float = POLL_S,
                 idle_poll_s: float = IDLE_POLL_S, sleep=None, log=None, meta: dict | None = None):
        self.http, self.cases, self.out = http_, cases, Path(out)
        self.jobs = max(1, int(jobs))
        self.timeout_min, self.poll_s, self.idle_poll_s = timeout_min, poll_s, idle_poll_s
        self.stop = threading.Event()
        self.sleep = sleep or (lambda s: self.stop.wait(s))
        self._log = log or (lambda line: print(line, flush=True))
        self._log_lock = threading.Lock()
        self.t0 = time.time()
        path = self.out / "results.json"
        prev = None
        if path.exists():
            if not resume:
                raise FileExistsError(f"{path} exists: pass --resume to finish that run, or choose a new --out")
            prev = json.loads(path.read_text())
            if prev.get("schema") != SCHEMA:
                raise ValueError(f"{path} is not a {SCHEMA} results file")
        self.results = Results(path, prev)
        # a resume (perhaps with --only) keeps every case of the earlier runs in the table and the score
        order = list(self.results.data.get("order") or []) if prev else []
        self.results.data["order"] = order + [c["id"] for c in cases if c["id"] not in order]
        self.meta = dict(meta or {})       # added to results.json "runs" once this run has cases to do

    def say(self, msg: str) -> None:
        with self._log_lock:
            self._log(f"[{time.time() - self.t0:7.1f}s] {msg}")

    def pending(self) -> list[dict]:
        return [c for c in self.cases if not (self.results.get(c["id"]) or {}).get("finished")]

    def run(self, key: str | None) -> dict:
        todo = self.pending()
        if len(todo) < len(self.cases):
            self.say(f"resume: {len(self.cases) - len(todo)} finished case(s) skipped")
        try:
            if todo:
                if not key:
                    raise LoginError("no access key: set GS_KEY or pass --key-file")
                self.results.data["runs"].append(self.meta)
                self.http.login(key)
                h = self.http.call("GET", "/api/health")
                self.meta["health"] = h.json() if h.status == 200 else {"error": h.err_text()}
                self.say(f"signed in to {self.http.base}; server {json.dumps(self.meta['health'])}; "
                         f"{len(todo)} case(s), {min(self.jobs, len(todo))} at a time")
                self.results.save()
                with ThreadPoolExecutor(max_workers=min(self.jobs, len(todo))) as ex:
                    futs = [ex.submit(self.run_case, c) for c in todo]
                    try:
                        for f in futs:
                            f.result()
                    except KeyboardInterrupt:
                        self.stop.set()
                        self.say("interrupted: each case stops after its current request, a chat stream within "
                                 "about 15 s (resume with --resume)")
                        raise
        finally:
            self.write_summary()
        return self.results.data

    def run_case(self, case: dict) -> None:
        if self.stop.is_set():
            return
        CaseRun(self, case, self.results.get(case["id"])).run()

    def write_summary(self) -> str:
        with self.results.lock:
            self.results.data["score"] = score_line(self.results.data)
            self.results._write()
            md = summary_md(self.results.data)
        (self.out / "summary.md").write_text(md)
        self.say(f"score: {self.results.data['score']}; summary {self.out / 'summary.md'}")
        return md


# ================================================================================================ --server-local
def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def descendants(pid: int) -> list[int]:
    """Every process below pid (from `ps -eo pid=,ppid=`), deepest last."""
    try:
        out = subprocess.run(["ps", "-eo", "pid=,ppid="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    kids: dict[int, list[int]] = {}
    for line in out.splitlines():
        try:
            a, b = (int(x) for x in line.split())
        except ValueError:
            continue
        kids.setdefault(b, []).append(a)
    found, todo = [], [pid]
    while todo:
        for k in kids.get(todo.pop(), []):
            if k not in found:
                found.append(k)
                todo.append(k)
    return found


class LocalServer:
    """This checkout's web server on 127.0.0.1:<port>: BUILDER=local, AGENT=gemini, a throwaway in-memory key."""

    def __init__(self, port: int, out: Path, photos: bool = True, say=print):
        self.port, self.out, self.photos, self.say = port, out, photos, say
        self.base = f"http://127.0.0.1:{port}"
        self.key = secrets.token_urlsafe(24)
        self.log_path = out / f"server-{port}.log"
        self.proc: subprocess.Popen | None = None

    def start(self, wait_s: float = 120) -> dict:
        if _port_in_use(self.port):
            raise RuntimeError(f"port {self.port} is already in use; pick another one")
        server_dir = STUDIO / "web" / "server"
        env = dict(os.environ)
        for k in ("BUILDER_URL", "BUCKET"):
            env.pop(k, None)
        env.update(ACCESS_KEY=self.key, BUILDER="local", AGENT="gemini", PHOTOS="1" if self.photos else "0",
                   STORE_DIR=str((self.out / "store").resolve()), COOKIE_SECURE="0", PYTHONUNBUFFERED="1")
        env.setdefault("ENGINE_PYTHON", sys.executable)
        self.out.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "ab") as logf:
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.asgi:app", "--host", "127.0.0.1", "--port", str(self.port),
                 "--log-level", "info"], cwd=str(server_dir), env=env, stdin=subprocess.DEVNULL, stdout=logf,
                stderr=subprocess.STDOUT, start_new_session=True)
        self.say(f"local server pid {self.proc.pid} on {self.base} (BUILDER=local AGENT=gemini PHOTOS="
                 f"{env['PHOTOS']}); log {self.log_path}")
        h = Http(self.base)
        t = time.time()
        while time.time() - t < wait_s:
            if self.proc.poll() is not None:
                raise RuntimeError(f"the local server exited ({self.proc.returncode}); see {self.log_path}")
            r = h.once("GET", "/api/health", timeout=5)
            if r.status == 200:
                health = r.json() or {}
                if health.get("builder") != "local" or health.get("agent") != "gemini":
                    raise RuntimeError(f"unexpected server mode: {health}")
                return health
            time.sleep(1)
        raise RuntimeError(f"the local server did not answer /api/health in {wait_s:.0f} s; see {self.log_path}")

    def stop(self) -> None:
        """SIGTERM, then SIGKILL, to exactly the server's process group (while its leader is ours, i.e. not yet
        reaped) and to its descendants by PID, each checked against the start time seen when it was listed, so a
        reused PID is never signalled."""
        if self.proc is None:
            return
        pids = {pid: _start_time(pid) for pid in descendants(self.proc.pid)}
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if self.proc.poll() is None:
                try:
                    os.killpg(self.proc.pid, sig)       # the group this runner created (start_new_session)
                except (ProcessLookupError, PermissionError):
                    pass
            for pid, started in pids.items():
                if _alive(pid) and _start_time(pid) == started:
                    try:
                        os.kill(pid, sig)
                    except (ProcessLookupError, PermissionError):
                        pass
            t = time.time()
            while time.time() - t < 15:
                alive = [x for x, st in pids.items() if _alive(x) and _start_time(x) == st]
                if self.proc.poll() is not None and not alive:
                    self.say(f"local server pid {self.proc.pid} stopped ({len(pids)} child process(es))")
                    return
                time.sleep(0.5)
        left = [x for x, st in pids.items() if _alive(x) and _start_time(x) == st]
        self.say(f"local server pid {self.proc.pid}: still running after SIGKILL: {left}")


def _start_time(pid: int) -> str | None:
    """Field 22 of /proc/<pid>/stat (start time in clock ticks); None where there is no /proc."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[-1].split()[19]
    except (OSError, IndexError):
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:   # a zombie child of ours is gone for our purposes
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


# ================================================================================================ CLI
class _Parser(argparse.ArgumentParser):
    def error(self, message):
        if "unrecognized arguments" in message:     # never echo stray arguments: one may be a key
            message = "unrecognized arguments (not shown, in case one is a key). The key comes from GS_KEY or --key-file."
        super().error(message)


def read_key(key_file: str | None) -> str | None:
    if key_file:
        path = Path(key_file).expanduser()
        try:
            st = path.stat()
        except OSError:
            raise SystemExit("--key-file: no such file") from None
        if st.st_mode & 0o077:
            raise SystemExit(f"--key-file must be chmod 600 (it is {oct(st.st_mode & 0o777)})")
        key = path.read_text().strip()
        if not key:
            raise SystemExit("--key-file is empty")
        return key
    return os.environ.get("GS_KEY", "").strip() or None


def _git_head() -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(STUDIO), "rev-parse", "--short", "HEAD"], capture_output=True,
                           text=True, timeout=10)
        return r.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def main(argv=None) -> int:
    ap = _Parser(description="Wish Labs batch benchmark (see the module docstring for the case format and rules).",
                 allow_abbrev=False, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", required=True, help="JSON or YAML case file")
    ap.add_argument("--base", help="studio base URL, e.g. https://wish.plugbook.si or http://127.0.0.1:9231")
    ap.add_argument("--server-local", type=int, metavar="PORT",
                    help="VM only: start this checkout's server (BUILDER=local, AGENT=gemini) on PORT, run, stop it")
    ap.add_argument("--no-photos", action="store_true", help="with --server-local: PHOTOS=0 (no concept photos)")
    ap.add_argument("--key-file", help="file holding the access key (chmod 600); default: env GS_KEY")
    ap.add_argument("--out", help="output folder (default studio/_out/bench/<UTC time>)")
    ap.add_argument("--jobs", type=int, default=2, help="cases at a time (default 2)")
    ap.add_argument("--timeout-min", type=float, default=DEFAULT_TIMEOUT_MIN, help="per-case timeout (default 20)")
    ap.add_argument("--resume", action="store_true", help="skip finished cases of <out>/results.json")
    ap.add_argument("--only", help="comma-separated case ids to run")
    ap.add_argument("--check", action="store_true", help="validate the case file and exit")
    a = ap.parse_args(argv)
    try:
        cases = load_cases(a.cases)
    except (OSError, CaseError) as e:
        print(f"cases: {e}", file=sys.stderr)
        return 2
    if a.only:
        want = [x.strip() for x in a.only.split(",") if x.strip()]
        missing = [x for x in want if x not in {c["id"] for c in cases}]
        if missing:
            print(f"--only: unknown case ids {missing}", file=sys.stderr)
            return 2
        cases = [c for c in cases if c["id"] in want]
    if a.check:
        for c in cases:
            print(f"{c['id']}: mode {c['mode']} (API {c['api_mode']}), {len(c['answers'])} answer round(s), "
                  f"expect {json.dumps(c['expect'])}")
        print(f"{len(cases)} case(s) OK")
        return 0
    if bool(a.base) == bool(a.server_local):
        print("give exactly one of --base URL or --server-local PORT", file=sys.stderr)
        return 2
    if a.jobs < 1 or a.timeout_min <= 0:
        print("--jobs must be >= 1 and --timeout-min > 0", file=sys.stderr)
        return 2
    out = Path(a.out) if a.out else STUDIO / "_out" / "bench" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    server = None

    def _term(signum, frame):       # `kill <pid>` runs the finally below, so a --server-local server is stopped too
        raise KeyboardInterrupt()
    prev_term = signal.signal(signal.SIGTERM, _term)
    try:
        if a.server_local:
            server = LocalServer(a.server_local, out, photos=not a.no_photos,
                                 say=lambda m: print(f"[server] {m}", flush=True))
            base, key = server.base, server.key
        else:
            base, key = a.base, read_key(a.key_file)
        meta = {"started_utc": now_utc(), "base": base, "cases_file": str(Path(a.cases).resolve()),
                "jobs": a.jobs, "timeout_min": a.timeout_min, "server_local": bool(a.server_local),
                "photos": (not a.no_photos) if a.server_local else None, "git": _git_head(), "only": a.only}
        # the Runner checks <out>/results.json (resume or refuse) before any server starts
        runner = Runner(Http(base), cases, out, jobs=a.jobs, timeout_min=a.timeout_min, resume=a.resume, meta=meta)
        if runner.pending():
            if server is not None:
                meta["server_health"] = server.start()
            elif not key:
                print("no access key: set GS_KEY or pass --key-file (chmod 600)", file=sys.stderr)
                return 2
        data = runner.run(key)
        print(f"score: {data.get('score')}")
        print(f"results: {out / 'results.json'}\nsummary: {out / 'summary.md'}")
        rows = [data["cases"].get(c["id"]) or {} for c in cases]
        return 0 if all(r.get("finished") for r in rows) else 1
    except (LoginError, FileExistsError, ValueError, RuntimeError) as e:
        print(f"bench: {e}", file=sys.stderr)
        return 3 if isinstance(e, LoginError) else 2
    except KeyboardInterrupt:
        return 130
    finally:
        if server is not None:
            server.stop()
        signal.signal(signal.SIGTERM, prev_term)


if __name__ == "__main__":
    sys.exit(main())
