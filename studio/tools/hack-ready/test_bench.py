"""Unit tests for bench.py with the HTTP layer replaced by an in-memory fake studio (no network, no server).

    cd studio/tools/hack-ready && python -m pytest -q test_bench.py      (VM: /opt/gs/venv has pytest)
"""
from __future__ import annotations

import io
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench  # noqa: E402

KEY = "test-key-7c1f0e9a"          # a fake key; the tests assert it never reaches any output
COOKIE = "gs_session=v1.fake"
T0 = "2026-11-20T10:00:00Z"


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class FakeRaw:
    def __init__(self, status: int, body: bytes = b"", headers=None, chunks=None):
        self.status = status
        self._headers = list(headers or [("Content-Type", "application/json")])
        self._buf = io.BytesIO(body)
        self._chunks = list(chunks) if chunks is not None else None

    def getheaders(self):
        return self._headers

    def read(self, n=-1):
        return self._buf.read() if n is None or n < 0 else self._buf.read(n)

    def read1(self, n):
        if self._chunks is None:
            return self._buf.read(n)
        if not self._chunks:
            return b""
        c = self._chunks.pop(0)
        if isinstance(c, BaseException):
            raise c
        if callable(c):
            return c()
        return c


def jraw(status: int, obj, headers=None) -> FakeRaw:
    return FakeRaw(status, json.dumps(obj).encode(), headers)


def err(status: int, code: str, message: str) -> FakeRaw:
    return jraw(status, {"error": {"code": code, "message": message}})


def sse_chunks(events) -> list:
    return [f"event: {ev}\ndata: {json.dumps(d)}\n\n".encode() for ev, d in events]


class FakeStudio:
    """Just enough of the studio API for the runner: projects, the chat turn script, versions, files, share."""

    def __init__(self, *, questions=True, concept_states=None, buildable=True, build_state="done",
                 build_finishes=True):
        self.projects: dict[str, dict] = {}
        self.files: dict[tuple, object] = {}
        self.status: dict[tuple, dict] = {}
        self.logins = 0
        self.calls: list = []
        self.inject: list = []          # rule(studio, method, path, body) -> FakeRaw | Exception | None
        self.turn_reads: dict[str, int] = {}   # pid -> project reads until a fake running turn clears
        self.questions = questions
        self.concept_states = concept_states or [("ready", ["tight fit near the button"]), ("ready", []),
                                                 ("failed", [])]
        self.buildable = buildable
        self.build_state = build_state
        self.build_finishes = build_finishes
        self.lock = threading.Lock()
        self.n = 0

    # ------------------------------------------------------------------ plumbing
    def opener(self, method, path, headers, data, timeout):
        body = json.loads(data) if data else None
        with self.lock:
            self.calls.append((method, path, body))
            for rule in list(self.inject):
                r = rule(self, method, path, body)
                if r is not None:
                    if isinstance(r, BaseException):
                        raise r
                    return r
            return self.route(method, path, body, headers)

    def route(self, method, path, body, headers):
        if path == "/api/login":
            self.logins += 1
            if (body or {}).get("key") != KEY:
                return err(401, "unauthorized", "That access key is not right.")
            return jraw(200, {"ok": True, "expires": "2026-12-20T00:00:00Z"},
                        [("Set-Cookie", COOKIE + "; HttpOnly; SameSite=Lax; Path=/")])
        if headers.get("Cookie") != COOKIE:
            return err(401, "unauthorized", "Sign in first.")
        if path == "/api/health":
            return jraw(200, {"ok": True, "builder": "fake", "agent": "scripted"})
        if path == "/api/projects" and method == "POST":
            return jraw(201, self.create((body or {}).get("mode")))
        parts = path.split("/")
        pid = parts[3]
        p = self.projects.get(pid)
        if p is None:
            return err(404, "not_found", "Project not found.")
        rest = "/".join(parts[4:])
        if rest == "" and method == "GET":
            if pid in self.turn_reads:
                self.turn_reads[pid] -= 1
                if self.turn_reads[pid] <= 0:
                    p["turn"] = None
                    del self.turn_reads[pid]
            return jraw(200, p)
        if rest == "chat" and method == "POST":
            if p.get("turn"):
                return err(409, "conflict", "The assistant is still answering the previous message.")
            return FakeRaw(200, headers=[("Content-Type", "text/event-stream")], chunks=sse_chunks(self.turn(p, body)))
        if rest == "share" and method == "POST":
            p["share"] = {"token": "s_" + pid[2:], "created": T0}
            return jraw(200, {"token": p["share"]["token"], "url": f"/studio/s/{p['share']['token']}"})
        if parts[4] == "v":
            n = int(parts[5])
            v = bench.find_version(p, n)
            if v is None:
                return err(404, "not_found", "Version not found.")
            st = self.status.get((pid, n))
            if len(parts) == 6:
                return jraw(200, {**v, "status": st, "files": [{"path": f} for f in ("spec.json", "pcb/board.kicad_pcb",
                                                                                     "firmware/src/main.cpp")]})
            if parts[6] == "status":
                return jraw(200, st) if st else err(404, "not_found", "No status for that version.")
            if parts[6] == "files":
                f = self.files.get((pid, n, "/".join(parts[7:])))
                return jraw(200, f) if f is not None else err(404, "not_found", "File not found.")
        return err(404, "not_found", "No such API route.")

    # ------------------------------------------------------------------ state
    def create(self, mode):
        self.n += 1
        pid = f"p_{self.n:012d}"
        p = {"id": pid, "name": "Untitled gadget", "created": iso_now(), "stage": "clarify", "mode": mode,
             "turn": None, "chat": [], "concepts": [], "brief": None, "brief_history": [], "versions": [],
             "current_version": None, "share": None, "usage": {"totals": {"turns": 0, "cost_usd_est": 0.0}}}
        self.projects[pid] = p
        return json.loads(json.dumps(p))

    def msg(self, role, kind, text, payload=None):
        self.n += 1
        return {"id": f"m_{self.n:010d}", "role": role, "kind": kind, "text": text, "payload": payload, "ts": iso_now()}

    def turn(self, p, body) -> list:
        """Apply one turn to the project; return its SSE events (as the server would stream them)."""
        ev = [("turn.start", {"seq": 1, "turn_id": "t_x"})]
        action = body.get("action") or ("answers" if "answers" in body else None)
        p["chat"].append(self.msg("user", "text", body.get("text") or f"My choices: {action}",
                                  {"action": action} if action else None))
        last = next((m for m in reversed(p["chat"]) if m["role"] == "assistant"), None)
        out = []
        if action is None and last is None:
            ev.append(("repair", {"seq": 2, "round": 1, "errors": ["body.outline[0]: Input should be a valid tuple"]}))
        if action is None and last is None and self.questions:
            qs = [{"id": "who", "question": "Who is it for?", "options": ["Me", "A child"]},
                  {"id": "look", "question": "What look do you like?", "options": ["Calm", "Playful"]}]
            out.append(self.msg("assistant", "choices", "A few questions.",
                                {"questions": qs, "defaults": ["Country: India (default)"], "answered": False}))
        elif action in ("answers", "regenerate_concepts") or (action is None and p["stage"] in ("clarify",)):
            for m in p["chat"]:
                if m["kind"] == "choices":
                    m["payload"]["answered"] = True
            states = self.concept_states if action != "regenerate_concepts" else [("ready", [])] * 3
            cs = []
            for i, (state, warns) in enumerate(states):
                self.n += 1
                cs.append({"id": f"c_{self.n:010d}", "title": f"Idea {i + 1}", "tier": ["expected", "expected",
                           "different"][i % 3], "state": state, "warnings": warns,
                           "photo_url": None, "photo_note": None})
            p["concepts"] += cs
            p["stage"] = "concepts"
            out.append(self.msg("assistant", "concepts", "Pick one.", {"concept_ids": [c["id"] for c in cs]}))
            ev.append(("concepts", {"seq": 2, "concepts": cs}))
        elif action == "select_concept":
            c = next((c for c in p["concepts"] if c["id"] == body.get("concept_id")), None)
            assert c and c["state"] == "ready", "the runner picked a concept that is not ready"
            p["selected_concept"] = c["id"]
            b = {"version": 1, "title": c["title"], "concept_id": c["id"],
                 "summary": {"value": "A calm lamp."}, "size": {"value": "About 100 mm"},
                 "parts": [{"module_id": "CORE_ESP32S3_DEVKITC1_N8R8", "implied": False},
                           {"module_id": "LED_RING_12_WS2812_50MM", "implied": False},
                           {"module_id": "BACKPLANE_V1", "implied": True}],
                 "spec": {"name": c["title"], "electronics": {"core": "CORE_ESP32S3_DEVKITC1_N8R8"},
                          "features": [{"id": "glow", "module": "LED_RING_12_WS2812_50MM"}]}}
            p["brief"], p["brief_history"], p["stage"] = b, [b], "brief"
            out.append(self.msg("assistant", "brief", "Here is the brief.", {"brief_version": 1}))
        elif action == "approve_brief":
            pid = p["id"]
            v = {"n": 1, "state": "running", "brief_version": 1, "finalized": False, "label": "Build from brief v1",
                 "started": iso_now(), "ended": None, "error": None, "percent": 0}
            p["versions"], p["current_version"], p["stage"] = [v], 1, "building"
            ev.append(("build.started", {"seq": 3, "version": v}))
            if self.build_finishes:
                self.finish_build(p)
                ev.append(("build.progress", {"seq": 4, "version_n": 1, "status": self.status[(pid, 1)]}))
                ev.append(("result", {"seq": 5, "version": v}))
            else:
                self.status[(pid, 1)] = {"state": "running", "percent": 40, "stages": []}
        for m in out:
            p["chat"].append(m)
            ev.append(("message.done", {"seq": 9, "message": m}))
        ev.append(("project", {"seq": 10, "project": json.loads(json.dumps(p))}))
        ev.append(("done", {"seq": 11}))
        return ev

    def finish_build(self, p):
        pid = p["id"]
        v = p["versions"][0]
        ok = self.build_state == "done"
        self.status[(pid, 1)] = {"state": self.build_state, "percent": 100 if ok else 70,
                                 "buildable": self.buildable if ok else None,
                                 "verdict": "buildable" if self.buildable else "not_buildable",
                                 "error": None if ok else "pcb failed",
                                 "stages": [{"id": "cad", "label": "Shell and CAD", "state": "done",
                                             "started": "2026-11-20T10:00:00Z", "ended": "2026-11-20T10:03:00Z"},
                                            {"id": "pcb", "label": "PCB", "state": "done" if ok else "failed",
                                             "started": "2026-11-20T10:00:00Z", "ended": "2026-11-20T10:01:30Z"}]}
        v.update(state=self.build_state, finalized=True, ended=iso_now(), percent=100 if ok else 70)
        price = ({"inr": 16599, "status": "ESTIMATE", "label": "Rs 16,599 delivered (estimate)"} if ok and self.buildable
                 else {"inr": None, "status": "HELD", "label": "Price held"})
        p["chat"].append(self.msg("assistant", "result" if ok else "error", "Built.",
                                  {"version": 1, "summary": {"price": price}}))
        p["stage"] = "ready" if ok else "error"
        self.files[(pid, 1, "order/quote.json")] = {"status": price["status"], "delivered_price_inr": price["inr"]}
        self.files[(pid, 1, "checks/digital-checks.json")] = {"checks": [
            {"id": "collisions", "title": "No parts collide", "status": "passed"},
            {"id": "min_wall", "title": "Walls thick enough", "status": "failed", "detail": "1.1 mm at the rear cover",
             "stage": "cad"},
            {"id": "purchased-fields", "title": "Purchased fields", "status": "warning"},
            {"id": "x", "title": "Not run", "status": "skipped"}]}
        self.files[(pid, 1, "spec.json")] = {"name": "Idea", "electronics": {"core": "CORE_ESP32S3_DEVKITC1_N8R8"}}

    def user_messages(self, pid):
        return [m for m in self.projects[pid]["chat"] if m["role"] == "user"]


def make_runner(fake: FakeStudio, cases, out, **kw) -> tuple[bench.Runner, list]:
    lines: list[str] = []
    http_ = bench.Http("http://studio.test", opener=fake.opener, sleep=lambda s: None)
    kw.setdefault("poll_s", 0.0)
    kw.setdefault("idle_poll_s", 0.0)
    r = bench.Runner(http_, cases, out, sleep=lambda s: time.sleep(0.001), log=lines.append, **kw)
    return r, lines


CASE = {"id": "lamp", "wish": "a calm desk lamp", "answers": ["for me, on my desk"],
        "expect": {"buildable": True, "electronics": True, "parts_any": ["LED_RING_*"],
                   "parts_all": ["CORE_ESP32S3_DEVKITC1_N8R8"]}}


class CaseParsing(unittest.TestCase):
    def test_defaults_and_modes(self):
        cs = bench.parse_cases({"cases": [{"id": "a", "wish": " w "}, {"id": "b", "wish": "x", "mode": "Deep"},
                                          {"id": "c.1", "wish": "y", "mode": "pro", "timeout_min": 5}]})
        self.assertEqual([c["api_mode"] for c in cs], ["fast", "pro", "pro"])
        self.assertEqual(cs[0]["mode"], "standard")
        self.assertEqual(cs[0]["wish"], "w")
        self.assertEqual(cs[0]["answers"], [])
        self.assertEqual(cs[0]["expect"], {})
        self.assertIsNone(cs[0]["timeout_min"])
        self.assertEqual(cs[2]["timeout_min"], 5.0)
        self.assertEqual(len(bench.parse_cases([{"id": "x", "wish": "y"}])), 1)   # a bare list works too

    def test_answer_forms_and_expect(self):
        cs = bench.parse_cases([{"id": "a", "wish": "w", "answers": ["t", ["a", "b"], {"age": 5}],
                                 "expect": {"buildable": False, "parts_any": [" SPK_* "]}}])
        self.assertEqual(cs[0]["expect"]["parts_any"], ["SPK_*"])

    def test_errors_name_the_case(self):
        bad = [
            ([{"id": "a"}], "wish"),
            ([{"id": "a", "wish": "w"}, {"id": "a", "wish": "v"}], "duplicate"),
            ([{"id": "a b", "wish": "w"}], "id must"),
            ([{"id": "a", "wish": "w", "mode": "turbo"}], "mode"),
            ([{"id": "a", "wish": "w", "expect": {"buildable": "yes"}}], "expect.buildable"),
            ([{"id": "a", "wish": "w", "expect": {"parts_all": "CORE"}}], "parts_all"),
            ([{"id": "a", "wish": "w", "expect": {"price": 1}}], "unknown expect"),
            ([{"id": "a", "wish": "w", "colour": "red"}], "unknown keys"),
            ([{"id": "a", "wish": "w", "answers": "yes"}], "answers must be a list"),
            ([{"id": "a", "wish": "w", "answers": [""]}], "empty"),
            ([{"id": "a", "wish": "w", "answers": [{"k": True}]}], "non-empty values"),
            ([{"id": "a", "wish": "w", "timeout_min": 0}], "timeout_min"),
            ([], "non-empty list"),
            ({"cases": "x"}, "non-empty list"),
        ]
        for data, words in bad:
            with self.subTest(data=data):
                with self.assertRaises(bench.CaseError) as cm:
                    bench.parse_cases(data)
                self.assertIn(words, str(cm.exception))

    def test_files_json_and_yaml(self):
        with tempfile.TemporaryDirectory() as td:
            jp = Path(td) / "c.json"
            jp.write_text(json.dumps({"cases": [{"id": "a", "wish": "w"}]}))
            self.assertEqual(bench.load_cases(str(jp))[0]["id"], "a")
            bad = Path(td) / "bad.json"
            bad.write_text("{nope")
            with self.assertRaises(bench.CaseError):
                bench.load_cases(str(bad))
            yp = Path(td) / "c.yaml"
            yp.write_text("cases:\n  - id: y1\n    wish: a lamp\n    mode: deep\n    answers:\n      - for me\n"
                          "    expect:\n      buildable: true\n      parts_any: [LED_*]\n")
            try:
                import yaml  # noqa: F401
            except ImportError:
                with self.assertRaises(bench.CaseError) as cm:
                    bench.load_cases(str(yp))
                self.assertIn("PyYAML", str(cm.exception))
            else:
                c = bench.load_cases(str(yp))[0]
                self.assertEqual((c["id"], c["api_mode"], c["answers"], c["expect"]["parts_any"]),
                                 ("y1", "pro", ["for me"], ["LED_*"]))

    def test_example_file_is_valid(self):
        cs = bench.load_cases(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench_cases.example.json"))
        self.assertEqual(len(cs), 3)


class Rules(unittest.TestCase):
    def test_concept_choice(self):
        warned = {"id": "c1", "state": "ready", "warnings": ["w"]}
        clean = {"id": "c2", "state": "ready", "warnings": []}
        failed = {"id": "c3", "state": "failed", "warnings": []}
        self.assertEqual(bench.choose_concept([failed, warned, clean])[0]["id"], "c2")
        c, rule = bench.choose_concept([failed, warned, {"id": "c4", "state": "ready", "warnings": ["x"]}])
        self.assertEqual(c["id"], "c1")
        self.assertIn("every ready concept has warnings", rule)
        self.assertEqual(bench.choose_concept([failed, {"id": "c5", "state": "rendering"}]), (None, "no ready concept"))
        self.assertEqual(bench.choose_concept([])[0], None)

    def test_answer_bodies(self):
        qs = [{"id": "who", "question": "Who is it for?"}, {"id": "look", "question": "Which look do you like?"}]
        self.assertEqual(bench.answer_body(None, qs), {"answers": [], "skipped": True})
        self.assertEqual(bench.answer_body("for me", qs), {"text": "for me"})
        self.assertEqual(bench.answer_body(["Me", "Calm"], qs),
                         {"answers": [{"question_id": "who", "value": "Me"}, {"question_id": "look", "value": "Calm"}]})
        self.assertEqual(bench.answer_body(["Me"], qs),
                         {"answers": [{"question_id": "who", "value": "Me"}], "skipped": True})
        self.assertEqual(bench.answer_body({"look": "Calm"}, qs),
                         {"answers": [{"question_id": "look", "value": "Calm"}], "skipped": True})
        self.assertEqual(bench.answer_body({"WHO IS": "a child"}, qs)["answers"],
                         [{"question_id": "who", "value": "a child"}])
        self.assertEqual(bench.answer_body({"nothing": "x"}, qs), {"answers": [], "skipped": True})
        self.assertEqual(bench.text_of({"a": "1", "b": 2}), "a: 1; b: 2")
        self.assertEqual(bench.text_of(["x", "y"]), "x, y")

    def test_expectations(self):
        rec = {"buildable": True, "electronics": True, "parts": ["CORE_ESP32S3_DEVKITC1_N8R8", "SPK_40MM_4R_3W"]}
        res, met = bench.check_expect({"buildable": True, "parts_any": ["spk_*"], "parts_all": ["CORE_*"]}, rec)
        self.assertTrue(met)
        res, met = bench.check_expect({"parts_all": ["CORE_*", "LED_RING_*"], "electronics": False}, rec)
        self.assertFalse(met)
        self.assertEqual(res["parts_all_missing"], ["LED_RING_*"])
        self.assertFalse(res["electronics"])
        self.assertEqual(bench.check_expect({}, rec), ({}, None))
        self.assertFalse(bench.check_expect({"electronics": True}, {"electronics": None})[1])

    def test_checks_and_electronics(self):
        c = bench.checks_of({"checks": [{"id": "a", "title": "A", "status": "passed"},
                                        {"id": "b", "title": "B", "status": "failed", "detail": "why", "stage": "cad"},
                                        {"id": "c", "title": "C", "status": "warning"},
                                        {"id": "d", "status": "skipped"}, {"id": "e", "ok": False}]})
        self.assertEqual((c["passed"], c["total"]), (1, 4))
        self.assertEqual(c["failed"][0], {"id": "b", "title": "B", "detail": "why", "stage": "cad"})
        self.assertEqual(c["failed"][1]["id"], "e")
        self.assertEqual(c["warned"], [{"id": "c", "title": "C"}])
        self.assertIsNone(bench.checks_of(None))
        self.assertTrue(bench.has_electronics({"name": "x"}))                 # engine default core
        self.assertFalse(bench.has_electronics({"electronics": None, "features": []}))
        self.assertTrue(bench.has_electronics({"electronics": None, "features": [{"module": "SPK_28MM_8R_2W"}]}))
        self.assertIsNone(bench.has_electronics(None))

    def test_score_line(self):
        data = {"order": ["a", "b", "c"], "cases": {
            "a": {"finished": True, "buildable": True, "minutes": 9.0, "expectations_met": True},
            "b": {"finished": True, "buildable": False, "minutes": 10.0, "expectations_met": False},
            "c": {"finished": True, "buildable": True, "minutes": 12.0, "expectations_met": None}}}
        self.assertEqual(bench.score_line(data), "2 of 3 buildable; median 10.0 min; expectations met 1 of 2")
        data["cases"]["c"]["finished"] = False
        self.assertEqual(bench.score_line(data), "2 of 3 buildable; median 9.5 min; expectations met 1 of 2 "
                                                 "(1 unfinished)")

    def test_sse_parsing(self):
        self.assertEqual(bench.parse_sse_block(b": ping"), (None, None))
        self.assertEqual(bench.parse_sse_block(b'event: status\ndata: {"text": "x"}'), ("status", {"text": "x"}))
        self.assertEqual(bench.parse_sse_block(b"data: plain"), ("message", "plain"))


class EndToEndWithFakeStudio(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.out = Path(self.td.name) / "run"

    def tearDown(self):
        self.td.cleanup()

    def run_cases(self, fake, cases, **kw):
        runner, lines = make_runner(fake, bench.parse_cases(cases), self.out, **kw)
        data = runner.run(KEY)
        return data, lines

    def test_full_case(self):
        fake = FakeStudio()
        data, lines = self.run_cases(fake, [CASE])
        r = data["cases"]["lamp"]
        self.assertTrue(r["finished"], r.get("error"))
        self.assertEqual(r["outcome"], "buildable")
        self.assertTrue(r["buildable"])
        self.assertEqual(r["concept_choice"]["rule"], "first ready concept with no warnings")
        self.assertEqual(r["concept_choice"]["index"], 1)
        self.assertEqual(r["parts"], ["CORE_ESP32S3_DEVKITC1_N8R8", "LED_RING_12_WS2812_50MM", "BACKPLANE_V1"])
        self.assertEqual(r["parts_chosen"], ["CORE_ESP32S3_DEVKITC1_N8R8", "LED_RING_12_WS2812_50MM"])
        self.assertTrue(r["electronics"])
        self.assertEqual(r["price"]["inr"], 16599)
        self.assertEqual(r["checks"]["failed"], [{"id": "min_wall", "title": "Walls thick enough",
                                                  "detail": "1.1 mm at the rear cover", "stage": "cad"}])
        self.assertTrue(r["share_url"].startswith("http://studio.test/studio/s/s_"))
        self.assertTrue(r["expectations_met"])
        self.assertEqual(r["questions"][0]["sent"], {"text": "for me, on my desk"})
        self.assertEqual([s["seconds"] for s in r["build"]["stages"]], [180.0, 90.0])
        self.assertEqual([t["step"] for t in r["turns"]], ["wish", "answers1", "select-concept", "build"])
        self.assertEqual(r["repair_rounds"], 1)
        self.assertEqual(r["turns"][0]["repairs"][0]["errors"], ["body.outline[0]: Input should be a valid tuple"])
        for k in ("create", "wish_to_concepts", "concepts_to_brief", "build", "collect", "total"):
            self.assertIn(k, r["timings_s"])
        self.assertEqual(fake.logins, 1)
        md = (self.out / "summary.md").read_text()
        self.assertIn("| lamp | yes | Rs 16,599 delivered (estimate) |", md)
        self.assertIn("**Score: 1 of 1 buildable; median", md)
        self.assertIn("Walls thick enough: 1.1 mm at the rear cover", md)
        self.assertIn("| repair rounds | model USD (est.) |", md)
        self.assertEqual(json.loads((self.out / "results.json").read_text())["score"], data["score"])
        self.assertTrue(list((self.out / "events" / "lamp").glob("01-wish.jsonl")))
        everything = (self.out / "results.json").read_text() + md + "\n".join(lines)
        for f in (self.out / "events" / "lamp").glob("*.jsonl"):
            everything += f.read_text()
        self.assertNotIn(KEY, everything)
        self.assertNotIn("v1.fake", everything)     # the session cookie is never written either

    def test_defaults_when_no_answers_and_not_buildable(self):
        fake = FakeStudio(buildable=False)
        data, _ = self.run_cases(fake, [{"id": "a", "wish": "w", "expect": {"buildable": False}}])
        r = data["cases"]["a"]
        self.assertEqual(r["questions"][0]["sent"], {"answers": [], "skipped": True})
        self.assertEqual(r["questions"][0]["source"], "agent defaults")
        self.assertEqual(r["outcome"], "not_buildable")
        self.assertFalse(r["buildable"])
        self.assertTrue(r["expectations_met"])
        self.assertTrue(r["finished"])

    def test_build_error_is_final(self):
        fake = FakeStudio(build_state="error")
        data, _ = self.run_cases(fake, [CASE])
        r = data["cases"]["lamp"]
        self.assertEqual(r["outcome"], "build_error")
        self.assertTrue(r["finished"])
        self.assertFalse(r["expectations_met"])

    def test_409_turn_lock_waits_and_sends_the_same_request_once(self):
        fake = FakeStudio()
        hits = {"n": 0}

        def lock_on_answers(st, method, path, body):
            if method == "POST" and path.endswith("/chat") and body and "text" in body and body["text"].startswith("for"):
                hits["n"] += 1
                if hits["n"] <= 2:     # another turn is running: 409 twice, then the project goes idle
                    pid = path.split("/")[3]
                    st.projects[pid]["turn"] = {"id": "t_other", "started": iso_now()}
                    st.turn_reads[pid] = 2
                    return err(409, "conflict", "The assistant is still answering the previous message.")
            return None
        fake.inject.append(lock_on_answers)
        data, lines = self.run_cases(fake, [CASE])
        r = data["cases"]["lamp"]
        self.assertTrue(r["buildable"], r.get("error"))
        pid = r["project_id"]
        answers = [m for m in fake.user_messages(pid) if m["text"] == "for me, on my desk"]
        self.assertEqual(len(answers), 1)
        t = next(t for t in r["turns"] if t["step"] == "answers1")
        self.assertEqual(t["waits_409"], 2)
        self.assertEqual(t["attempts"], 3)
        self.assertTrue(any("409" in ln for ln in lines))

    def test_409_that_is_not_a_turn_lock_goes_back_to_the_state(self):
        fake = FakeStudio()
        once = {"done": False}

        def conflict_on_build(st, method, path, body):
            if method == "POST" and path.endswith("/chat") and (body or {}).get("action") == "approve_brief" \
                    and not once["done"]:
                once["done"] = True
                return err(409, "conflict", "The brief is not waiting for approval.")
            return None
        fake.inject.append(conflict_on_build)
        data, _ = self.run_cases(fake, [CASE])
        r = data["cases"]["lamp"]
        self.assertTrue(r["buildable"], r.get("error"))
        self.assertEqual([t["step"] for t in r["turns"]].count("build"), 2)
        self.assertEqual(r["turns"][3]["conflict"], "The brief is not waiting for approval.")

    def test_network_break_after_the_server_took_it_is_not_resent(self):
        fake = FakeStudio()

        def drop_select(st, method, path, body):
            if method == "POST" and path.endswith("/chat") and (body or {}).get("action") == "select_concept":
                st.route(method, path, body, {"Cookie": COOKIE})    # the server processes the turn ...
                return ConnectionResetError("connection reset by peer")   # ... and the answer is lost
            return None
        fake.inject.append(drop_select)
        data, lines = self.run_cases(fake, [CASE])
        r = data["cases"]["lamp"]
        self.assertTrue(r["buildable"], r.get("error"))
        sel = [c for c in fake.calls if c[0] == "POST" and (c[2] or {}).get("action") == "select_concept"]
        self.assertEqual(len(sel), 1)
        self.assertTrue(next(t for t in r["turns"] if t["step"] == "select-concept")["server_got_it"])

    def test_network_error_before_the_server_got_it_is_resent_once(self):
        fake = FakeStudio()
        n = {"fails": 0}

        def refuse_wish(st, method, path, body):
            if method == "POST" and path.endswith("/chat") and (body or {}).get("text") == "a calm desk lamp" \
                    and n["fails"] < 1:
                n["fails"] += 1
                return ConnectionRefusedError("refused")
            return None
        fake.inject.append(refuse_wish)
        data, _ = self.run_cases(fake, [CASE])
        r = data["cases"]["lamp"]
        self.assertTrue(r["buildable"], r.get("error"))
        self.assertEqual(len([m for m in fake.user_messages(r["project_id"]) if m["text"] == "a calm desk lamp"]), 1)
        self.assertEqual(r["turns"][0]["attempts"], 2)

    def test_stream_break_mid_turn_reads_the_project(self):
        fake = FakeStudio()

        def break_stream(st, method, path, body):
            if method == "POST" and path.endswith("/chat") and (body or {}).get("text") == "a calm desk lamp":
                events = st.turn(st.projects[path.split("/")[3]], body)
                return FakeRaw(200, chunks=sse_chunks(events[:1]) + [ConnectionResetError("reset mid-stream")])
            return None
        fake.inject.append(break_stream)
        data, _ = self.run_cases(fake, [CASE])
        r = data["cases"]["lamp"]
        self.assertTrue(r["buildable"], r.get("error"))
        self.assertIn("ConnectionResetError", r["turns"][0]["stream_broke"])
        self.assertEqual(len(fake.user_messages(r["project_id"])), 4)

    def test_429_and_5xx_on_reads_back_off(self):
        fake = FakeStudio()
        waits: list = []
        seq = [FakeRaw(503, b"Service Unavailable"), FakeRaw(429, b"Rate exceeded.", [("Retry-After", "7")]),
               FakeRaw(502, b"Bad Gateway")]

        def flaky_reads(st, method, path, body):
            if method == "GET" and path.endswith("/v/1") and seq:
                return seq.pop(0)
            return None
        fake.inject.append(flaky_reads)
        runner, _ = make_runner(fake, bench.parse_cases([CASE]), self.out)
        runner.http.sleep = waits.append
        data = runner.run(KEY)
        r = data["cases"]["lamp"]
        self.assertTrue(r["buildable"], r.get("error"))
        self.assertEqual(r["build"]["verdict"], "buildable")
        self.assertEqual(waits, [2.0, 7.0, 8.0])     # backoff 2 s, then Retry-After 7 s, then backoff 8 s

    def test_429_on_a_chat_post_is_resent_after_checking(self):
        fake = FakeStudio()
        n = {"left": 1}

        def rate_limit_select(st, method, path, body):
            if method == "POST" and (body or {}).get("action") == "select_concept" and n["left"]:
                n["left"] -= 1
                return FakeRaw(429, b"Rate exceeded.", [("Retry-After", "1")])
            return None
        fake.inject.append(rate_limit_select)
        data, _ = self.run_cases(fake, [CASE])
        r = data["cases"]["lamp"]
        self.assertTrue(r["buildable"], r.get("error"))
        t = next(t for t in r["turns"] if t["step"] == "select-concept")
        self.assertEqual((t["attempts"], t["failures"][0][:8]), (2, "HTTP 429"))

    def test_no_ready_concept_asks_once_for_new_ones(self):
        fake = FakeStudio(concept_states=[("failed", []), ("failed", []), ("failed", [])])
        data, _ = self.run_cases(fake, [CASE])
        r = data["cases"]["lamp"]
        self.assertTrue(r["buildable"], r.get("error"))
        self.assertIn("regenerate-concepts", [t["step"] for t in r["turns"]])

    def test_timeout_records_progress(self):
        fake = FakeStudio(build_finishes=False)

        def endless_build_stream(st, method, path, body):
            if method == "POST" and (body or {}).get("action") == "approve_brief":
                events = st.turn(st.projects[path.split("/")[3]], body)
                pings = [lambda: (time.sleep(0.05), b": ping\n\n")[1] for _ in range(400)]
                return FakeRaw(200, chunks=sse_chunks(events[:2]) + pings)
            return None
        fake.inject.append(endless_build_stream)
        case = dict(CASE, timeout_min=0.02)       # 1.2 s
        data, _ = self.run_cases(fake, [case])
        r = data["cases"]["lamp"]
        self.assertEqual(r["outcome"], "timeout")
        self.assertTrue(r["finished"])
        self.assertFalse(r["buildable"])
        self.assertEqual(r["build_progress"]["percent"], 40)
        self.assertIn("no (timeout at 40%)", (self.out / "summary.md").read_text())
        self.assertTrue(r["share_url"])

    def test_jobs_run_cases_in_parallel_with_one_login(self):
        fake = FakeStudio()
        cases = [dict(CASE, id=f"c{i}") for i in range(3)]
        data, _ = self.run_cases(fake, cases, jobs=2)
        self.assertEqual(fake.logins, 1)
        self.assertEqual(len(fake.projects), 3)
        self.assertTrue(all(data["cases"][f"c{i}"]["buildable"] for i in range(3)))
        self.assertTrue(data["score"].startswith("3 of 3 buildable; median"))

    def test_refused_login_stops_without_retrying(self):
        fake = FakeStudio()
        runner, _ = make_runner(fake, bench.parse_cases([CASE]), self.out)
        with self.assertRaises(bench.LoginError):
            runner.run("wrong-key")
        self.assertEqual(fake.logins, 1)
        self.assertEqual(len(fake.projects), 0)

    def test_401_mid_run_stops_and_leaves_the_case_resumable(self):
        fake = FakeStudio()

        def rotate_key(st, method, path, body):
            if method == "POST" and (body or {}).get("action") == "select_concept":
                return err(401, "unauthorized", "Sign in first.")
            return None
        fake.inject.append(rotate_key)
        data, _ = self.run_cases(fake, [CASE])
        r = data["cases"]["lamp"]
        self.assertFalse(r["finished"])
        self.assertIn("401", r["error"])


class Resume(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.out = Path(self.td.name) / "run"

    def tearDown(self):
        self.td.cleanup()

    def test_resume_skips_finished_and_runs_the_rest(self):
        cases = bench.parse_cases([dict(CASE, id="a"), dict(CASE, id="b")])
        fake = FakeStudio()
        r1, _ = make_runner(fake, cases[:1], self.out)
        r1.run(KEY)
        self.assertEqual(len(fake.projects), 1)
        with self.assertRaises(FileExistsError):            # never overwrite a run without --resume
            make_runner(fake, cases, self.out)
        r2, lines = make_runner(fake, cases, self.out, resume=True)
        data = r2.run(KEY)
        self.assertEqual(len(fake.projects), 2)               # only "b" made a project
        self.assertTrue(data["cases"]["a"]["finished"] and data["cases"]["b"]["finished"])
        self.assertTrue(any("1 finished case(s) skipped" in ln for ln in lines))
        self.assertEqual(len(data["runs"]), 2)
        r3, _ = make_runner(fake, cases, self.out, resume=True)
        r3.run(None)                                          # nothing left: no login needed
        self.assertEqual(fake.logins, 2)

    def test_resume_carries_on_from_the_server_state(self):
        cases = bench.parse_cases([CASE])
        fake = FakeStudio()
        p = fake.create("fast")
        pid = p["id"]
        proj = fake.projects[pid]
        for body in ({"text": "a calm desk lamp"}, {"text": "for me, on my desk"}):
            fake.turn(proj, body)
        cid = next(c["id"] for c in proj["concepts"] if c["state"] == "ready" and not c["warnings"])
        fake.turn(proj, {"action": "select_concept", "concept_id": cid})     # the brief waits for approval
        rec = bench.new_record(cases[0], "http://studio.test")
        rec.update(project_id=pid, answer_rounds_used=1, started_epoch=time.time() - 60)
        self.out.mkdir(parents=True)
        (self.out / "results.json").write_text(json.dumps({"schema": bench.SCHEMA, "runs": [], "order": ["lamp"],
                                                           "cases": {"lamp": rec}}))
        posts_before = len([c for c in fake.calls if c[1].endswith("/chat")])
        runner, _ = make_runner(fake, cases, self.out, resume=True)
        data = runner.run(KEY)
        r = data["cases"]["lamp"]
        self.assertTrue(r["buildable"], r.get("error"))
        self.assertEqual(r["project_id"], pid)
        self.assertEqual(len(fake.projects), 1)               # no new project
        chat_posts = [c for c in fake.calls if c[1].endswith("/chat")][posts_before:]
        self.assertEqual([c[2] for c in chat_posts], [{"action": "approve_brief"}])   # nothing re-sent
        self.assertEqual(r["runs"], 1)
        self.assertTrue(any("resumed" in n for n in r["notes"]))

    def test_resume_reattaches_a_running_build(self):
        cases = bench.parse_cases([CASE])
        fake = FakeStudio(build_finishes=False)
        p = fake.create("fast")
        pid = p["id"]
        proj = fake.projects[pid]
        for body in ({"text": "a calm desk lamp"}, {"text": "for me"}):
            fake.turn(proj, body)
        cid = next(c["id"] for c in proj["concepts"] if c["state"] == "ready")
        fake.turn(proj, {"action": "select_concept", "concept_id": cid})
        fake.turn(proj, {"action": "approve_brief"})          # the build is running on the server
        reads = {"n": 0}

        def finish_later(st, method, path, body):
            if method == "GET" and path.endswith("/v/1/status"):
                reads["n"] += 1
                if reads["n"] == 3:
                    st.finish_build(st.projects[pid])
            return None
        fake.inject.append(finish_later)
        rec = bench.new_record(cases[0], "http://studio.test")
        rec.update(project_id=pid, build_version=1)
        self.out.mkdir(parents=True)
        (self.out / "results.json").write_text(json.dumps({"schema": bench.SCHEMA, "runs": [], "order": ["lamp"],
                                                           "cases": {"lamp": rec}}))
        runner, _ = make_runner(fake, cases, self.out, resume=True)
        r = runner.run(KEY)["cases"]["lamp"]
        self.assertTrue(r["buildable"], r.get("error"))
        self.assertFalse([c for c in fake.calls if c[0] == "POST" and c[1].endswith("/chat")])   # none re-sent
        self.assertGreaterEqual(reads["n"], 3)


class KeyHandling(unittest.TestCase):
    def test_key_file_must_be_private(self):
        with tempfile.TemporaryDirectory() as td:
            kf = Path(td) / "k"
            kf.write_text(KEY + "\n")
            os.chmod(kf, 0o644)
            with self.assertRaises(SystemExit) as cm:
                bench.read_key(str(kf))
            self.assertNotIn(KEY, str(cm.exception))
            os.chmod(kf, 0o600)
            self.assertEqual(bench.read_key(str(kf)), KEY)
            with self.assertRaises(SystemExit):
                bench.read_key(str(Path(td) / "missing"))
        old = os.environ.get("GS_KEY")
        os.environ["GS_KEY"] = " " + KEY + " "
        try:
            self.assertEqual(bench.read_key(None), KEY)
        finally:
            if old is None:
                del os.environ["GS_KEY"]
            else:
                os.environ["GS_KEY"] = old

    def test_cli_never_echoes_stray_arguments(self):
        err_buf = io.StringIO()
        old = sys.stderr
        sys.stderr = err_buf
        try:
            with self.assertRaises(SystemExit):
                bench.main(["--cases", "x.json", "--key", KEY])
        finally:
            sys.stderr = old
        self.assertNotIn(KEY, err_buf.getvalue())
        self.assertIn("not shown", err_buf.getvalue())

    def test_main_check_mode(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench_cases.example.json")
        out = io.StringIO()
        old = sys.stdout
        sys.stdout = out
        try:
            self.assertEqual(bench.main(["--cases", path, "--check"]), 0)
            self.assertEqual(bench.main(["--cases", path, "--check", "--only", "focus-lamp"]), 0)
        finally:
            sys.stdout = old
        self.assertIn("3 case(s) OK", out.getvalue())
        self.assertIn("1 case(s) OK", out.getvalue())


if __name__ == "__main__":
    unittest.main()
