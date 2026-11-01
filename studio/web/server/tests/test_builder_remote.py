"""RemoteBuilder.build and a failed engine build (live failure 2026-11-02, "Storybot Pal").

The builder answers /build with the engine's build summary. When a stage fails its "error" is a plain string
("1 stage(s) failed: order: ..."), not the {"code", "message"} envelope of a builder refusal. RemoteBuilder called .get
on it, raised AttributeError, and BuildManager overwrote the real stage message in status.json with that exception.
"""
from __future__ import annotations

import asyncio

from conftest import events_named, sse

from app.builder.fake import FakeBuilder
from app.builder.remote import RemoteBuilder
from app.engine_bridge import EngineBridge
from app.projects import vkey
from app.services import Services
from app.store import make_store

PROMPT = "I want a cute robot that tells my daughter bedtime stories and remembers her favorite characters."
ORDER_MSG = ("parts list written, but no quote: 1 print part(s) could not be sliced: PRINT_Shell: part is 167.6 x 97.6 x "
             "212.7 mm and does not fit the 250 x 210 x 210 mm build volume in this orientation")
ENGINE_ERROR = f"1 stage(s) failed: order: {ORDER_MSG[:160]}"
STAGES = [("normalize", "Check the design", "done"), ("electrical", "Wiring and pin map", "done"), ("cad", "Shell and CAD", "done"),
          ("pcb", "Backplane PCB", "done"), ("firmware", "Firmware", "done"), ("order", "Print slicing and price", "failed"),
          ("manual", "Build manual", "done"), ("checks", "Digital checks", "done"), ("finalize", "Final guide checks", "done"),
          ("docs", "Project documents", "done"), ("package", "Package and export", "done")]
# the shapes the engine writes (pipeline.Status.as_dict / Pipeline.summary), trimmed to what the web reads
STATUS = {"state": "error", "percent": 100.0, "error": ENGINE_ERROR, "failed": ["order"], "messages": [],
          "stages": [{"id": i, "label": lbl, "state": st, "message": ORDER_MSG[:500] if i == "order" else "done", "percent": 100.0,
                      **({"error": ORDER_MSG} if st == "failed" else {})} for i, lbl, st in STAGES]}
SUMMARY = {"schema": "gadget-build-summary/1", "state": "error", "error": ENGINE_ERROR, "seconds": 167.5,
           "stages": {i: {"label": lbl, "state": st, "error": ORDER_MSG if st == "failed" else None} for i, lbl, st in STAGES},
           "project_id": "p", "version": 1, "gcs": "gs://bucket/projects/p/v1", "uploaded_files": 376, "upload_errors": []}


def remote_with(answers: list) -> tuple[RemoteBuilder, list]:
    rb = RemoteBuilder.__new__(RemoteBuilder)  # no URL or ID token: _post is replaced
    calls: list = []

    async def _post(path, body, timeout):
        calls.append(path)
        return answers.pop(0)
    rb._post = _post
    rb.settings = type("S", (), {"build_timeout_s": 10})()
    return rb, calls


def test_failed_engine_summary_comes_back_as_a_failed_build_not_an_attribute_error():
    rb, calls = remote_with([dict(SUMMARY)])
    res = asyncio.run(rb.build("p", 1, {}, None, {}))
    assert calls == ["/build"]
    assert res["ok"] is False and res["state"] == "error" and res["error"] == ENGINE_ERROR
    assert res["stages"]["order"]["error"] == ORDER_MSG


def test_done_summary_is_ok_and_a_rate_limit_envelope_is_retried(monkeypatch):
    async def no_sleep(_s):
        return None
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    busy = {"error": {"code": "rate_limited", "message": "2 builds already running"}}
    rb, calls = remote_with([busy, {**SUMMARY, "state": "done", "error": None}])
    res = asyncio.run(rb.build("p", 1, {}, None, {}))
    assert calls == ["/build", "/build"] and res["ok"] is True and res["state"] == "done"


def test_builder_refusal_envelope_becomes_a_plain_error_string():
    rb, _ = remote_with([{"error": {"code": "invalid", "message": "spec must be an object"}}])
    res = asyncio.run(rb.build("p", 1, {}, None, {}))
    assert res == {"error": "spec must be an object", "ok": False, "code": "invalid"}


class LiveFailBuilder(FakeBuilder):
    """The fake builder for the conversation, but /build answers like gstudio-builder did for the live build: the engine's
    final status.json is already in the store (the builder mirrors it last) and the response is the build summary."""

    async def build(self, pid, n, spec, brief, options):
        await asyncio.to_thread(self.store.write_json, vkey(pid, n, "status.json"), STATUS)
        rb, _ = remote_with([dict(SUMMARY)])
        return await rb.build(pid, n, spec, brief, options)


def test_failed_order_stage_reaches_the_user_with_its_own_message(settings):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from conftest import KEY

    store = make_store(settings)
    svc = Services(settings, store=store, builder=LiveFailBuilder(settings, EngineBridge(settings), store))
    with TestClient(create_app(settings, svc)) as c:
        assert c.post("/api/login", json={"key": KEY}).status_code == 200
        pid = c.post("/api/projects", json={}).json()["id"]
        evs = sse(c, "POST", f"/api/projects/{pid}/chat", json={"text": PROMPT})
        qs = events_named(evs, "choices")[0]["message"]["payload"]["questions"]
        evs = sse(c, "POST", f"/api/projects/{pid}/chat",
                  json={"answers": [{"question_id": qs[0]["id"], "value": "5 yo old"}, {"question_id": qs[1]["id"], "value": "yes"}]})
        cons = events_named(evs, "concepts")[0]["concepts"]
        sse(c, "POST", f"/api/projects/{pid}/chat", json={"action": "select_concept", "concept_id": cons[0]["id"]})
        evs = sse(c, "POST", f"/api/projects/{pid}/chat", json={"action": "approve_brief"})
        res = events_named(evs, "result")
        assert res, [e for e, _ in evs]
        assert res[0]["message"]["kind"] == "error" and res[0]["version"]["error"] == ENGINE_ERROR
        text = res[0]["message"]["text"]
        assert "AttributeError" not in text and "Print slicing and price" in text and ENGINE_ERROR in text
        st = c.get(f"/api/projects/{pid}/v/1/status").json()
        assert st["state"] == "error" and st["error"] == ENGINE_ERROR
        p = c.get(f"/api/projects/{pid}").json()
        v = next(x for x in p["versions"] if x["n"] == 1)
        assert v["state"] == "error" and v["error"] == ENGINE_ERROR and "AttributeError" not in str(v)
