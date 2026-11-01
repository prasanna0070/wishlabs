"""Version builds: start (new version, background task), watch (status.json polling), finalize (summary +
result message), reconcile (finish versions whose builder completed while no task was watching)."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator

from .projects import NotFound, find_message, find_version, make_message, pkey, vkey
from .summary import build_summary
from .util import now_iso, parse_iso, spec_hash

log = logging.getLogger("gstudio.builds")

FINAL = ("done", "error")


class BuildManager:
    def __init__(self, svc):
        self.svc = svc
        self.tasks: dict[tuple[str, int], asyncio.Task] = {}

    @property
    def store(self):
        return self.svc.store

    @property
    def repo(self):
        return self.svc.repo

    # ------------------------------------------------------------------ start
    async def start(self, pid: str, spec: dict, *, source: str, label: str, brief: dict | None = None,
                    base_version: int | None = None, extra_messages: list[dict] | None = None,
                    copy_from: int | None = None) -> tuple[dict, dict]:
        """Create version n (queued), make it current, add the progress message, launch the build task.
        Returns (version, progress_message)."""
        holder: dict = {}

        def mut(p):
            busy = [v for v in p["versions"] if v["state"] in ("queued", "running")]
            if busy and copy_from is None:
                raise BuildBusy(busy[-1]["n"])
            n = max([v["n"] for v in p["versions"]] or [0]) + 1
            v = {"n": n, "created": now_iso(), "label": label[:120], "source": source, "state": "queued",
                 "summary": "", "spec_hash": spec_hash(spec), "brief_version": (brief or {}).get("version"),
                 "base_version": base_version, "percent": 0, "error": None, "started": now_iso(), "ended": None,
                 "finalized": False, "thumb": None}
            if copy_from is not None:
                v["restored_from"] = copy_from
            p["versions"].append(v)
            p["current_version"] = n
            for m in extra_messages or []:
                p["chat"].append(m)
            msg = make_message("assistant", "progress", f"Building version {n}: {label}",
                               {"version": n, "status": {"state": "queued", "percent": 0, "stages": []}})
            p["chat"].append(msg)
            if source in ("build", "update", "restore", "tweak"):
                p["stage"] = "building"
            holder["v"], holder["m"] = v, msg

        await self.repo.aupdate(pid, mut)
        v, msg = holder["v"], holder["m"]
        n = v["n"]
        await asyncio.to_thread(self.store.write_json, f"projects/{pid}/inputs/v{n}.json",
                                {"spec": spec, "brief": brief, "source": source, "label": label})
        await asyncio.to_thread(self.store.write_json, vkey(pid, n, "status.json"),
                                {"state": "queued", "percent": 0, "stages": [], "error": None})
        if copy_from is not None:
            task = asyncio.create_task(self._copy(pid, n, copy_from))
        else:
            task = asyncio.create_task(self._run(pid, n, spec, brief))
        self.tasks[(pid, n)] = task
        task.add_done_callback(lambda t, k=(pid, n): self.tasks.pop(k, None))
        return v, msg

    async def _run(self, pid: str, n: int, spec: dict, brief: dict | None) -> None:
        try:
            await self.repo.aupdate(pid, lambda p: _set_state(p, n, "running"))
            res = await self.svc.builder.build(pid, n, spec, brief, {"fast": False})
            log.info("build %s v%s returned %s", pid, n, {k: res.get(k) for k in ("ok", "state", "error")}
                     if isinstance(res, dict) else res)
            st = await asyncio.to_thread(self.store.read_json, vkey(pid, n, "status.json")) or {}
            if st.get("state") not in FINAL:
                ok = isinstance(res, dict) and res.get("ok")
                st = {**st, "state": "done" if ok else "error", "percent": 100 if ok else st.get("percent"),
                      "error": None if ok else (res.get("error") if isinstance(res, dict) else str(res))
                      or "builder returned without finishing"}
                await asyncio.to_thread(self.store.write_json, vkey(pid, n, "status.json"), st)
        except Exception as exc:  # noqa: BLE001 - any failure becomes an error version, never a crash
            log.exception("build %s v%s failed", pid, n)
            st = await asyncio.to_thread(self.store.read_json, vkey(pid, n, "status.json")) or {}
            st = {**st, "state": "error", "error": f"{type(exc).__name__}: {exc}"[:500]}
            await asyncio.to_thread(self.store.write_json, vkey(pid, n, "status.json"), st)
        await self.finalize(pid, n)

    async def _copy(self, pid: str, n: int, src: int) -> None:
        try:
            await asyncio.to_thread(self.store.copy_prefix, f"projects/{pid}/v{src}", f"projects/{pid}/v{n}")
            st = await asyncio.to_thread(self.store.read_json, vkey(pid, n, "status.json")) or {}
            st = {**st, "state": "done", "percent": 100, "restored_from": src}
            await asyncio.to_thread(self.store.write_json, vkey(pid, n, "status.json"), st)
        except Exception as exc:  # noqa: BLE001
            log.exception("restore copy failed")
            await asyncio.to_thread(self.store.write_json, vkey(pid, n, "status.json"),
                                    {"state": "error", "percent": None, "stages": [],
                                     "error": f"restore copy failed: {exc}"[:300]})
        await self.finalize(pid, n)

    # ------------------------------------------------------------------ finalize / reconcile
    async def finalize(self, pid: str, n: int) -> dict | None:
        """Idempotent: compute the summary from files, set version state, append the result message."""
        rd = lambda rel: asyncio.to_thread(self.store.read_json, vkey(pid, n, rel))  # noqa: E731
        status = await rd("status.json") or {}
        if status.get("state") not in FINAL:
            return None
        status = await asyncio.to_thread(enrich_status, self.store, pid, n, status)
        status["draft_url"] = None
        build, manifest, dc, quote = await asyncio.gather(rd("build.json"), rd("manifest.json"),
                                                          rd("checks/digital-checks.json"), rd("order/quote.json"))
        spec = await rd("spec.json")
        holder: dict = {}

        def mut(p):
            v = find_version(p, n)
            if v is None or v.get("finalized"):
                return
            name = (spec or {}).get("name") or (p.get("brief") or {}).get("title") or p["name"]
            payload, text, one = build_summary(name, status, build, manifest, dc, quote, v.get("brief_version"))
            v["state"] = status["state"]
            v["percent"] = 100 if status["state"] == "done" else status.get("percent")
            v["error"] = status.get("error")
            v["ended"] = now_iso()
            v["summary"] = one
            v["finalized"] = True
            if spec:
                v["spec_hash"] = spec_hash(spec)
            files = {f.get("path") for f in (manifest or {}).get("files") or [] if isinstance(f, dict)}
            for cand in ("manual/assembled.png", "manual/hero.png", "cad/preview.png"):
                if cand in files or (not files and status["state"] == "done" and cand == "manual/assembled.png"):
                    v["thumb"] = cand
                    break
            for m in p["chat"]:
                if m["kind"] == "progress" and (m.get("payload") or {}).get("version") == n:
                    m["payload"]["status"] = status
                    m["text"] = (f"Version {n} built." if status["state"] == "done"
                                 else f"Version {n} build stopped.")
            kind = "result" if status["state"] == "done" else "error"
            msg = make_message("assistant", kind, text,
                               {"version": n, "summary": payload} if kind == "result"
                               else {"version": n, "code": "build_failed", "retryable": True, "summary": payload})
            p["chat"].append(msg)
            busy = any(x["state"] in ("queued", "running") for x in p["versions"] if x["n"] != n)
            if not busy:
                p["stage"] = "ready" if status["state"] == "done" or any(
                    x["state"] == "done" for x in p["versions"]) else "error"
            holder["v"], holder["m"] = dict(v), msg

        try:
            await self.repo.aupdate(pid, mut)
        except NotFound:
            return None
        return holder or None

    async def reconcile(self, p: dict) -> dict:
        """Called on project reads. Finalizes versions whose status.json is final; marks orphaned ones stale."""
        pid = p["id"]
        changed = False
        for v in p.get("versions", []):
            if v.get("finalized"):
                continue
            st = await asyncio.to_thread(self.store.read_json, vkey(pid, v["n"], "status.json")) or {}
            if st.get("state") in FINAL:
                await self.finalize(pid, v["n"])
                changed = True
            elif (pid, v["n"]) not in self.tasks:
                started = parse_iso(v.get("started"))
                stat = await asyncio.to_thread(self.store.stat, vkey(pid, v["n"], "status.json"))
                last = stat.updated if stat else (started.timestamp() if started else 0)
                if time.time() - last > self.svc.settings.stale_build_minutes * 60:
                    await asyncio.to_thread(self.store.write_json, vkey(pid, v["n"], "status.json"),
                                            {**st, "state": "error",
                                             "error": "The build stopped reporting progress."})
                    await self.finalize(pid, v["n"])
                    changed = True
            elif st.get("state") and st.get("state") != v["state"]:
                def mut(pp, n=v["n"], s=st):
                    vv = find_version(pp, n)
                    if vv and not vv.get("finalized"):
                        vv["state"] = s["state"] if s["state"] in ("queued", "running") else vv["state"]
                        vv["percent"] = s.get("percent")
                await self.repo.aupdate(pid, mut)
                changed = True
        return await self.repo.aget(pid) if changed else p

    # ------------------------------------------------------------------ watch
    async def watch(self, pid: str, n: int, interval: float = 1.0) -> AsyncIterator[tuple[str, dict]]:
        """Yields ('build.progress', {...}) on each status change and finally ('result', {...})."""
        last = None
        seen = 0
        while True:
            raw = await asyncio.to_thread(self.store.read_json, vkey(pid, n, "status.json")) or {}
            st = await asyncio.to_thread(enrich_status, self.store, pid, n, raw)
            msgs = st.get("messages") or []
            sig = (st.get("state"), st.get("percent"), len(st.get("stages") or []),
                   tuple(s.get("state") for s in st.get("stages") or []), st.get("draft_url"), len(msgs))
            if sig != last:
                last = sig
                yield "build.progress", {"version_n": n, "status": st}
            for m in msgs[seen:]:
                yield "build.message", {"version_n": n, **m}
            seen = max(seen, len(msgs))
            if st.get("state") in FINAL:
                res = None
                for _ in range(60):
                    p = await self.repo.aget(pid)
                    v = find_version(p, n)
                    if v and v.get("finalized"):
                        msg = next((m for m in reversed(p["chat"])
                                    if m["kind"] in ("result", "error") and (m.get("payload") or {}).get("version") == n),
                                   None)
                        res = {"version": v, "message": msg}
                        break
                    if (pid, n) not in self.tasks:
                        await self.finalize(pid, n)
                    await asyncio.sleep(0.5)
                if res:
                    yield "result", res
                return
            await asyncio.sleep(interval)


STAGE_LINES = {
    "running": "Working on {label}.",
    "done": "{Label} finished.",
    "failed": "{Label} failed{detail}.",
    "error": "{Label} failed{detail}.",
    "skipped": "{Label} skipped{detail}.",
}


def status_messages(st: dict) -> list[dict]:
    """Plain-English progress lines: the pipeline's own status.messages when present, else lines derived from the
    stage list (marked derived)."""
    msgs = st.get("messages")
    if isinstance(msgs, list) and msgs:
        out = []
        for m in msgs:
            if isinstance(m, str):
                out.append({"text": m})
            elif isinstance(m, dict) and (m.get("text") or m.get("message")):
                out.append({"text": str(m.get("text") or m.get("message")), "ts": m.get("ts") or m.get("at"),
                            "stage": m.get("stage")})
        return out
    out = []
    for stg in st.get("stages") or []:
        state = stg.get("state")
        if state not in STAGE_LINES:
            continue
        label = str(stg.get("label") or stg.get("id") or "a stage")
        detail = f": {stg.get('message')}" if state in ("failed", "error", "skipped") and stg.get("message") else ""
        if state != "running":
            out.append({"text": STAGE_LINES["running"].format(label=label.lower()), "stage": stg.get("id"),
                        "ts": stg.get("started"), "derived": True})
        out.append({"text": STAGE_LINES[state].format(label=label.lower(), Label=label, detail=detail),
                    "stage": stg.get("id"), "ts": stg.get("ended") or stg.get("started"), "derived": True})
    return out


def enrich_status(store, pid: str, n: int, st: dict | None) -> dict | None:
    """Adds draft_url (cad/draft.glb while it exists) and messages[] to a status.json document."""
    if st is None:
        return None
    st = dict(st)
    # the builder writes draft: "cad/draft.glb" + draft_rev (int) once the file is in the store
    d = st.get("draft")
    draft_rel = str(d.get("glb")) if isinstance(d, dict) and d.get("glb") else (d if isinstance(d, str) and d else None)
    rev = st.get("draft_rev")
    if draft_rel and ".." not in draft_rel and rev is not None:
        st["draft_url"] = f"/api/projects/{pid}/v/{n}/files/{draft_rel}?rev={rev}"
    else:
        rel = draft_rel if draft_rel and ".." not in draft_rel else "cad/draft.glb"
        stat = store.stat(vkey(pid, n, rel)) if st.get("state") not in ("done", "error") else None
        st["draft_url"] = f"/api/projects/{pid}/v/{n}/files/{rel}?rev={stat.etag}" if stat else None
    st["messages"] = status_messages(st)
    return st


class BuildBusy(Exception):
    def __init__(self, n: int):
        super().__init__(f"version {n} is still building")
        self.n = n


def _set_state(p: dict, n: int, state: str) -> None:
    v = find_version(p, n)
    if v and not v.get("finalized"):
        v["state"] = state
