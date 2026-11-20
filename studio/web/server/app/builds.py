"""Version builds: start (new version, background task), watch (status.json polling), finalize (summary +
result message), reconcile (finish versions whose builder completed while no task was watching).

Partial builds: a version can start as a copy of an earlier one (`seed_from`) and run only some engine stages
(`options.only`), e.g. the story-audio rerun (firmware + checks + docs + package, BLK-4) or a colour-only tweak
that reuses pcb/ and firmware/ (MIN-37). Builders that cannot see the seeded files (remote) get a full build.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator

from . import export_extras
from .brief import revise_brief, size_from_spec
from .projects import NotFound, find_message, find_version, make_message, pkey, vkey
from .sound import maybe_auto
from .summary import build_summary, check_counts, story_audio_state
from .trace import cad_size, requirements
from .util import new_id, now_iso, parse_iso, spec_hash

log = logging.getLogger("gstudio.builds")

FINAL = ("done", "error")
EDIT_SOURCES = ("update", "tweak")
SEED_DROP = ("status.json", "export.zip", "manifest.json")


def build_options(spec: dict, options: dict | None = None) -> dict:
    """Options sent to the builder. story_player designs get their story audio by default (BLK-4)."""
    opts = {"fast": False}
    tmpl = ((spec or {}).get("behaviour") or {}).get("template")
    if tmpl == "story_player":
        opts["stories_audio"] = True
    opts.update({k: v for k, v in (options or {}).items() if v is not None})
    return opts


class BuildManager:
    def __init__(self, svc):
        self.svc = svc
        self.tasks: dict[tuple[str, int], asyncio.Task] = {}
        self._fin_locks: dict[tuple[str, int], asyncio.Lock] = {}

    @property
    def store(self):
        return self.svc.store

    @property
    def repo(self):
        return self.svc.repo

    # ------------------------------------------------------------------ start
    async def start(self, pid: str, spec: dict, *, source: str, label: str, brief: dict | None = None,
                    base_version: int | None = None, extra_messages: list[dict] | None = None,
                    copy_from: int | None = None, seed_from: int | None = None, options: dict | None = None,
                    changes: list[dict] | None = None, change_text: str | None = None,
                    plan: dict | None = None) -> tuple[dict, dict]:
        """Create version n (queued), make it current, add the progress message, launch the build task.
        Edit builds (update/tweak) also write brief v(k+1) carrying the change (MAJ-36).
        Returns (version, progress_message)."""
        holder: dict = {}
        names = await asyncio.to_thread(self.svc.bridge.module_names) if source in EDIT_SOURCES and brief else {}
        public = getattr(self.svc.settings, "public_url", None)

        def mut(p):
            busy = [v for v in p["versions"] if v["state"] in ("queued", "running")]
            if busy and copy_from is None:
                raise BuildBusy(busy[-1]["n"])
            n = max([v["n"] for v in p["versions"]] or [0]) + 1
            b = brief
            if source in EDIT_SOURCES and brief:
                b = revise_brief(p.get("brief") or brief, spec, version=len(p.get("brief_history") or []) + 1,
                                 for_version=n, source=source, change=change_text or label, names=names)
                p["brief"] = b
                p["brief_history"] = (p.get("brief_history") or []) + [b]
            v = {"n": n, "created": now_iso(), "label": label[:120], "source": source, "state": "queued",
                 "summary": "", "spec_hash": spec_hash(spec), "brief_version": (b or {}).get("version"),
                 "base_version": base_version, "percent": 0, "error": None, "started": now_iso(), "ended": None,
                 "finalized": False, "thumb": None}
            if changes is not None:
                v["changes"] = [c.get("text") if isinstance(c, dict) else str(c) for c in changes]
            if plan:
                v["plan"] = plan
            if copy_from is not None:
                v["restored_from"] = copy_from
            p["versions"].append(v)
            p["current_version"] = n
            for m in extra_messages or []:
                p["chat"].append(m)
            msg = make_message("assistant", "progress", f"Building version {n}: {label}",
                               {"version": n, "status": {"state": "queued", "percent": 0, "stages": []},
                                **({"changes": v["changes"]} if changes is not None else {}),
                                **({"plan": plan} if plan else {})})
            p["chat"].append(msg)
            if source in ("build", "update", "restore", "tweak", "story_audio"):
                p["stage"] = "building"
            # MAJ-33: a story card build tells the firmware where its parent page can fetch spoken name clips
            if public and ((spec.get("behaviour") or {}).get("template")) == "story_player":
                if not (p.get("device") or {}).get("token"):
                    p["device"] = {"token": new_id("d", 24), "created": now_iso()}
                    holder["new_token"] = p["device"]["token"]
                holder["name_audio_url"] = f"{public}/api/device/{p['device']['token']}/name-audio"
            holder["v"], holder["m"], holder["b"], holder["p"] = v, msg, b, p

        await self.repo.aupdate(pid, mut)
        v, msg, b = holder["v"], holder["m"], holder["b"]
        n = v["n"]
        if holder.get("new_token"):
            await asyncio.to_thread(self.store.write_json, f"devices/{holder['new_token']}.json",
                                    {"pid": pid, "created": now_iso()})
        if holder.get("name_audio_url"):
            options = {**(options or {}), "name_audio_url": holder["name_audio_url"]}
        if base_version is not None and copy_from is None and not (options or {}).get("pcb_reuse_from"):
            # MIN-37: the PCB stage reuses the base version's routed board when its pcb_key is unchanged
            src = self._version_location(pid, base_version)
            if src:
                options = {**(options or {}), "pcb_reuse_from": src}
        await asyncio.to_thread(self.store.write_json, f"projects/{pid}/inputs/v{n}.json",
                                {"spec": spec, "brief": b, "source": source, "label": label,
                                 "options": options or {}, "seed_from": seed_from})
        if copy_from is None:
            if seed_from is not None:
                await asyncio.to_thread(self._seed, pid, seed_from, n)
            try:
                concept = export_extras.concept_for(holder["p"], b)
                await asyncio.to_thread(export_extras.seed_references, self.store, pid, n, concept)
            except Exception:  # noqa: BLE001 - references are a nice-to-have, never block a build
                log.exception("could not seed references for %s v%s", pid, n)
        await asyncio.to_thread(self.store.write_json, vkey(pid, n, "status.json"),
                                {"state": "queued", "percent": 0, "stages": [], "error": None})
        if copy_from is not None:
            task = asyncio.create_task(self._copy(pid, n, copy_from))
        else:
            task = asyncio.create_task(self._run(pid, n, spec, b, options))
        self.tasks[(pid, n)] = task
        task.add_done_callback(lambda t, k=(pid, n): self.tasks.pop(k, None))
        return v, msg

    def _version_location(self, pid: str, n: int) -> str | None:
        """Where a builder can read version n: the local folder, or gs://bucket/projects/<pid>/v<n>/."""
        local = self.store.local_path(vkey(pid, n, "status.json"))
        if local is not None:
            return str(local.parent)
        bucket = getattr(self.svc.settings, "bucket", None)
        return f"gs://{bucket}/projects/{pid}/v{n}/" if bucket else None

    def _seed(self, pid: str, src: int, n: int) -> None:
        """v<n> starts as a copy of v<src> (every file, engine stage records included) minus the files the new run
        must rewrite, so nothing stale can be shipped if a later stage fails."""
        self.store.copy_prefix(f"projects/{pid}/v{src}", f"projects/{pid}/v{n}")
        for rel in SEED_DROP:
            self.store.delete(vkey(pid, n, rel))

    async def _run(self, pid: str, n: int, spec: dict, brief: dict | None, options: dict | None = None) -> None:
        try:
            await self.repo.aupdate(pid, lambda p: _set_state(p, n, "running"))
            opts = build_options(spec, options)
            res = await self.svc.builder.build(pid, n, spec, brief, opts)
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
    def _read_text(self, key: str) -> str | None:
        b = self.store.read_bytes(key)
        return b.decode(errors="replace") if b is not None else None

    async def finalize(self, pid: str, n: int) -> dict | None:
        """Idempotent: compute the summary from files, set version state, append the result message."""
        lock = self._fin_locks.setdefault((pid, n), asyncio.Lock())
        async with lock:
            return await self._finalize(pid, n)

    async def _finalize(self, pid: str, n: int) -> dict | None:
        rd = lambda rel: asyncio.to_thread(self.store.read_json, vkey(pid, n, rel))  # noqa: E731
        status = await rd("status.json") or {}
        if status.get("state") not in FINAL:
            return None
        status = await asyncio.to_thread(enrich_status, self.store, pid, n, status)
        status["draft_url"] = None
        (build, manifest, dc, quote, spec, msum, stories, electrical, comps, cad_sum) = await asyncio.gather(
            rd("build.json"), rd("manifest.json"), rd("checks/digital-checks.json"), rd("order/quote.json"),
            rd("spec.json"), rd("manual/manual-summary.json"), rd("firmware/sdcard/stories.json"),
            rd("electrical.json"), rd("cad/components.json"), rd("cad/cad-summary.json"))
        fw_text = await asyncio.to_thread(self._read_text, vkey(pid, n, "firmware/test-results.txt"))
        if isinstance(comps, dict) and isinstance(comps.get("components"), dict):
            comps = comps["components"]
        measured = cad_size(comps)
        try:
            p0 = await self.repo.aget(pid)
        except NotFound:
            return None
        v0 = find_version(p0, n)
        if v0 is None or v0.get("finalized"):
            return None
        if status["state"] == "done":
            try:
                rec = export_extras.project_record(p0, n, now_iso())
                info = await asyncio.to_thread(export_extras.finalize, self.store, pid, n, rec)
                log.info("export extras %s v%s: %s", pid, n, info)
            except Exception:  # noqa: BLE001 - the export still ships without the extras
                log.exception("export extras failed for %s v%s", pid, n)
            manifest = await rd("manifest.json")
        holder: dict = {}

        def mut(p):
            v = find_version(p, n)
            if v is None or v.get("finalized"):
                return
            # MAJ-36: a brief written by the server for this edit build gets the CAD-measured size
            if measured and status["state"] == "done":
                for b in [p.get("brief")] + list(p.get("brief_history") or []):
                    if isinstance(b, dict) and b.get("auto_for_version") == n:
                        b["size"] = size_from_spec(b.get("spec") or {}, measured, f"the CAD of v{n}")
            name = (spec or {}).get("name") or (p.get("brief") or {}).get("title") or p["name"]
            brief = next((b for b in p.get("brief_history") or [] if b.get("version") == v.get("brief_version")),
                         None) or (p.get("brief") if v.get("brief_version") is None else None)
            story = story_audio_state(spec, stories, check_counts(dc))
            reqs = requirements(brief, electrical=electrical, build=build, components=comps, fw_text=fw_text,
                                story=story, stories=stories, quote=quote) if status["state"] == "done" else []
            payload, text, one = build_summary(name, status, build, manifest, dc, quote, v.get("brief_version"),
                                               {"manual_summary": msum, "stories": stories, "spec": spec,
                                                "requirements": reqs, "cad_summary": cad_sum})
            if measured:
                payload["cad_size"] = measured
            v["state"] = status["state"]
            v["percent"] = 100 if status["state"] == "done" else status.get("percent")
            v["error"] = status.get("error")
            v["ended"] = now_iso()
            v["summary"] = one
            v["finalized"] = True
            if spec:   # v["spec_hash"] stays the input-spec hash (same basis as brief.spec_hash)
                v["output_spec_hash"] = spec_hash(spec)
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
        if holder and status["state"] == "done":
            await maybe_auto(self.svc, pid, n)      # the gadget's sound: once, after its first successful build
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
