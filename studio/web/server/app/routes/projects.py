"""Authenticated project routes: CRUD, checklist, brief edit, versions, undo/redo, files, export, tweak,
preview, order requests, share links, the chat SSE turn and the project event stream."""
from __future__ import annotations

import asyncio
import re
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..agent.turn import Turn, TurnError
from ..auth import require_session
from ..brief import assemble_brief
from ..builds import BuildBusy, enrich_status
from ..projects import NotFound, find_version, make_message, vkey
from ..sse import EventChannel, sse_response
from ..tweak import TweakError, apply_tweak, describe_tweak
from ..util import new_id, now_iso, spec_hash
from .common import (err, load_project, serve_concept_file, serve_export, serve_store_file, serve_version_file,
                     svc_of)

router = APIRouter(dependencies=[Depends(require_session)])


async def _body(request: Request) -> dict:
    try:
        b = await request.json()
    except ValueError:
        raise err(422, "invalid", "Body must be JSON.") from None
    if not isinstance(b, dict):
        raise err(422, "invalid", "Body must be a JSON object.")
    return b


async def _update(request: Request, pid: str, fn) -> dict:
    try:
        return await svc_of(request).repo.aupdate(pid, fn)
    except NotFound:
        raise err(404, "not_found", "Project not found.") from None


@router.get("/api/me")
async def me(exp: int = Depends(require_session)):
    from datetime import datetime, timezone

    return {"authenticated": True, "expires": datetime.fromtimestamp(exp, timezone.utc).isoformat()
            .replace("+00:00", "Z")}


# ------------------------------------------------------------------ CRUD
@router.get("/api/projects")
async def list_projects(request: Request):
    return {"projects": await asyncio.to_thread(svc_of(request).repo.list)}


@router.post("/api/projects", status_code=201)
async def create_project(request: Request):
    b = await _body(request) if (await request.body()) else {}
    mode = b.get("mode")
    if mode is not None and mode not in ("pro", "fast"):
        raise err(422, "invalid", "mode must be pro or fast")
    return await asyncio.to_thread(svc_of(request).repo.create, b.get("name"), mode)


@router.get("/api/projects/{pid}")
async def get_project(request: Request, pid: str):
    return await load_project(request, pid)


@router.patch("/api/projects/{pid}")
async def patch_project(request: Request, pid: str):
    b = await _body(request)

    def mut(p):
        if "name" in b:
            name = str(b["name"] or "").strip()[:120]
            if not name:
                raise TurnError(422, "invalid", "name cannot be empty")
            p["name"] = name
            p["name_source"] = "user"
        if "mode" in b:
            if b["mode"] not in ("pro", "fast"):
                raise TurnError(422, "invalid", "mode must be pro or fast")
            p["mode"] = b["mode"]
    return await _guarded_update(request, pid, mut)


@router.delete("/api/projects/{pid}")
async def delete_project(request: Request, pid: str):
    await _update(request, pid, lambda p: p.update(deleted=True))
    return {"ok": True}


async def _guarded_update(request, pid, mut):
    try:
        return await _update(request, pid, mut)
    except TurnError as e:
        raise err(e.status, e.code, e.message) from None


@router.patch("/api/projects/{pid}/checklist")
async def patch_checklist(request: Request, pid: str):
    b = await _body(request)

    def mut(p):
        cl = p.setdefault("checklist", {"assembly": {}, "wiring": {}})
        for sec in ("assembly", "wiring"):
            vals = b.get(sec)
            if vals is None:
                continue
            if not isinstance(vals, dict):
                raise TurnError(422, "invalid", f"{sec} must be an object of step_id: bool")
            for k, v in vals.items():
                if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", str(k)):
                    raise TurnError(422, "invalid", f"bad step id {k!r}")
                if v is None:
                    cl.setdefault(sec, {}).pop(k, None)
                else:
                    cl.setdefault(sec, {})[k] = bool(v)
    p = await _guarded_update(request, pid, mut)
    return {"checklist": p["checklist"]}


@router.put("/api/projects/{pid}/brief")
async def put_brief(request: Request, pid: str):
    b = (await _body(request)).get("brief")
    if not isinstance(b, dict):
        raise err(422, "invalid", "brief must be an object")
    svc = svc_of(request)
    p = await load_project(request, pid, reconcile=False)
    if not p.get("brief"):
        raise err(409, "conflict", "There is no brief yet.")
    if p["stage"] not in ("brief", "ready", "error"):
        raise err(409, "conflict", "The brief cannot be edited right now.")
    spec = p["brief"]["spec"]
    names = await asyncio.to_thread(svc.bridge.module_names)

    def mut(pp):
        nb = assemble_brief({**pp["brief"], **b}, spec, pp["brief"].get("concept_id"),
                            len(pp.get("brief_history") or []) + 1, names)
        nb["edited_by_user"] = True
        pp["brief"] = nb
        pp["brief_history"] = (pp.get("brief_history") or []) + [nb]
        pp["chat"].append(make_message("assistant", "brief", f"Brief v{nb['version']} saved with your edits.",
                                       {"brief_version": nb["version"]}))
        if not pp.get("versions"):
            pp["stage"] = "brief"
    return await _update(request, pid, mut)


# ------------------------------------------------------------------ chat turn (SSE)
@router.post("/api/projects/{pid}/chat")
async def chat(request: Request, pid: str):
    return await _turn_stream(request, pid, await _body(request))


@router.post("/api/projects/{pid}/parts/{component}/edit")
async def edit_part(request: Request, pid: str, component: str):
    b = await _body(request)
    body = {"action": "edit_part", "component": component, "instruction": b.get("instruction")}
    for k in ("mode", "follow_build"):
        if k in b:
            body[k] = b[k]
    return await _turn_stream(request, pid, body)


async def _turn_stream(request: Request, pid: str, body: dict):
    svc = svc_of(request)
    await load_project(request, pid)
    ch = EventChannel()
    turn = Turn(svc, pid, ch.emit)
    try:
        await turn.begin(body)
    except TurnError as e:
        raise err(e.status, e.code, e.message) from None
    except NotFound:
        raise err(404, "not_found", "Project not found.") from None

    async def runner():
        try:
            await turn.run()
        finally:
            await ch.emit("done", {})
            await ch.close()

    task = asyncio.create_task(runner())
    request.app.state.bg.add(task)
    task.add_done_callback(request.app.state.bg.discard)
    return sse_response(ch.stream())


@router.get("/api/projects/{pid}/stream")
async def stream(request: Request, pid: str, once: int = 0):
    svc = svc_of(request)
    p = await load_project(request, pid)
    ch = EventChannel()

    async def producer():
        try:
            await ch.emit("project", {"project": p})
            if not once:
                active = [v["n"] for v in p.get("versions", []) if not v.get("finalized")]
                for n in active:
                    async for ev, data in svc.builds.watch(pid, n):
                        await ch.emit(ev, data)
                if active:
                    await ch.emit("project", {"project": await svc.repo.aget(pid)})
        finally:
            await ch.emit("done", {})
            await ch.close()

    task = asyncio.create_task(producer())

    async def gen():
        try:
            async for chunk in ch.stream():
                yield chunk
        finally:
            task.cancel()

    return sse_response(gen())


# ------------------------------------------------------------------ versions
@router.get("/api/projects/{pid}/versions")
async def versions(request: Request, pid: str):
    p = await load_project(request, pid)
    return {"versions": p["versions"], "current_version": p.get("current_version")}


@router.get("/api/projects/{pid}/v/{n}")
async def version_detail(request: Request, pid: str, n: int):
    svc = svc_of(request)
    p = await load_project(request, pid)
    v = find_version(p, n)
    if not v:
        raise err(404, "not_found", "Version not found.")
    status, manifest = await asyncio.gather(asyncio.to_thread(svc.store.read_json, vkey(pid, n, "status.json")),
                                            asyncio.to_thread(svc.store.read_json, vkey(pid, n, "manifest.json")))
    status = await asyncio.to_thread(enrich_status, svc.store, pid, n, status)
    base = f"/api/projects/{pid}/v/{n}"
    return {**v, "status": status, "files": (manifest or {}).get("files") or [],
            "urls": {"files": f"{base}/files/", "export": f"{base}/export", "status": f"{base}/status",
                     "build": f"{base}/files/build.json", "glb": f"{base}/files/cad/assembly.glb"}}


@router.get("/api/projects/{pid}/v/{n}/status")
async def version_status(request: Request, pid: str, n: int):
    svc = svc_of(request)
    await load_project(request, pid, reconcile=False)
    st = await asyncio.to_thread(svc.store.read_json, vkey(pid, n, "status.json"))
    if st is None:
        raise err(404, "not_found", "No status for that version.")
    st = await asyncio.to_thread(enrich_status, svc.store, pid, n, st)
    return JSONResponse(st, headers={"Cache-Control": "no-store"})


@router.api_route("/api/projects/{pid}/v/{n}/files/{path:path}", methods=["GET", "HEAD"])
async def version_file(request: Request, pid: str, n: int, path: str, download: int = 0):
    p = await load_project(request, pid, reconcile=False)
    return await serve_version_file(request, p, n, path, bool(download))


@router.api_route("/api/projects/{pid}/v/{n}/export", methods=["GET", "HEAD"])
async def export(request: Request, pid: str, n: int):
    return await serve_export(request, await load_project(request, pid, reconcile=False), n)


@router.api_route("/api/projects/{pid}/concepts/{cid}/{name}", methods=["GET", "HEAD"])
async def concept_file(request: Request, pid: str, cid: str, name: str):
    return await serve_concept_file(request, await load_project(request, pid, reconcile=False), cid, name)


@router.api_route("/api/projects/{pid}/previews/{name}", methods=["GET", "HEAD"])
async def preview_file(request: Request, pid: str, name: str):
    await load_project(request, pid, reconcile=False)
    if not re.fullmatch(r"[0-9a-f]{16,64}\.(png|glb)", name):
        raise err(404, "not_found", "File not found.")
    return await serve_store_file(request, f"projects/{pid}/previews/{name}", immutable=True)


def _move(p: dict, step: int | None, to: int | None = None) -> None:
    ns = sorted(v["n"] for v in p["versions"])
    if not ns:
        raise TurnError(409, "conflict", "There are no versions yet.")
    if to is not None:
        if to not in ns:
            raise TurnError(404, "not_found", "Version not found.")
        p["current_version"] = to
        return
    cur = p.get("current_version")
    i = ns.index(cur) if cur in ns else len(ns) - 1
    j = min(max(i + step, 0), len(ns) - 1)
    p["current_version"] = ns[j]


@router.post("/api/projects/{pid}/undo")
async def undo(request: Request, pid: str):
    return await _guarded_update(request, pid, lambda p: _move(p, -1))


@router.post("/api/projects/{pid}/redo")
async def redo(request: Request, pid: str):
    return await _guarded_update(request, pid, lambda p: _move(p, +1))


@router.put("/api/projects/{pid}/current")
async def set_current(request: Request, pid: str):
    b = await _body(request)
    try:
        n = int(b.get("n"))
    except (TypeError, ValueError):
        raise err(422, "invalid", "n must be a version number") from None
    return await _guarded_update(request, pid, lambda p: _move(p, None, n))


async def _input_spec(svc, pid: str, n: int) -> dict | None:
    inp = await asyncio.to_thread(svc.store.read_json, f"projects/{pid}/inputs/v{n}.json") or {}
    return inp.get("spec") or await asyncio.to_thread(svc.store.read_json, vkey(pid, n, "spec.json"))


@router.post("/api/projects/{pid}/versions/{n}/restore", status_code=201)
async def restore(request: Request, pid: str, n: int):
    svc = svc_of(request)
    p = await load_project(request, pid)
    v = find_version(p, n)
    if not v:
        raise err(404, "not_found", "Version not found.")
    spec = await _input_spec(svc, pid, n)
    if not spec:
        raise err(409, "conflict", "That version has no saved design to restore.")
    try:
        nv, _ = await svc.builds.start(pid, spec, source="restore", label=f"Restore of v{n}", brief=p.get("brief"),
                                       base_version=n, copy_from=n if v["state"] == "done" else None)
    except BuildBusy as e:
        raise err(409, "conflict", f"Version {e.n} is still building.") from None
    return nv


# ------------------------------------------------------------------ tweak + preview
def _tweak_inputs(b: dict) -> tuple[dict, float | None]:
    patch = b.get("patch") or {}
    if not isinstance(patch, dict):
        raise err(422, "invalid", "patch must be a JSON object")
    scale = b.get("scale")
    if scale is not None:
        try:
            scale = float(scale)
        except (TypeError, ValueError):
            raise err(422, "invalid", "scale must be a number") from None
    return patch, scale


async def _tweaked_spec(request: Request, p: dict, b: dict) -> tuple[dict, int, dict, float | None]:
    svc = svc_of(request)
    base = b.get("base_version", p.get("current_version"))
    if base is None or not find_version(p, base):
        raise err(409, "conflict", "There is no built version to tweak yet.")
    spec = await _input_spec(svc, p["id"], int(base))
    if not spec:
        raise err(409, "conflict", "The base version has no saved design.")
    patch, scale = _tweak_inputs(b)
    try:
        new = apply_tweak(spec, patch, scale)
    except TweakError as e:
        raise err(422, "invalid", str(e), errors=[str(e)]) from None
    return new, int(base), patch, scale


@router.post("/api/projects/{pid}/tweak")
async def tweak(request: Request, pid: str):
    svc = svc_of(request)
    b = await _body(request)
    p = await load_project(request, pid)
    spec, base, patch, scale = await _tweaked_spec(request, p, b)
    r = await svc.builder.validate(spec)
    if not r.get("ok"):
        raise err(422, "invalid", "The tweak does not fit the design rules.", errors=r.get("errors") or [])
    if b.get("validate_only"):
        return {"ok": True, "normalized": r.get("normalized"), "warnings": r.get("warnings") or []}
    label = str(b.get("label") or describe_tweak(patch, scale))[:80]
    msg = make_message("user", "tweak", describe_tweak(patch, scale),
                       {"version": None, "patch": patch, "scale": scale, "base_version": base})
    try:
        v, _ = await svc.builds.start(pid, spec, source="tweak", label=label, brief=p.get("brief"),
                                      base_version=base, extra_messages=[msg])
    except BuildBusy as e:
        raise err(409, "conflict", f"Version {e.n} is still building.") from None

    def fix(pp):
        for m in pp["chat"]:
            if m["id"] == msg["id"]:
                m["payload"]["version"] = v["n"]
    await _update(request, pid, fix)
    return JSONResponse({"ok": True, "version": v}, status_code=202)


@router.post("/api/projects/{pid}/preview")
async def preview(request: Request, pid: str):
    svc = svc_of(request)
    b = await _body(request)
    p = await load_project(request, pid)
    spec, _, _, _ = await _tweaked_spec(request, p, b)
    r = await svc.builder.validate(spec)
    if not r.get("ok"):
        raise err(422, "invalid", "The tweak does not fit the design rules.", errors=r.get("errors") or [])
    h = spec_hash(spec).split(":")[1][:32]
    key = f"projects/{pid}/previews/{h}"
    base = f"/api/projects/{pid}/previews/{h}"
    if await asyncio.to_thread(svc.store.exists, key + ".png"):
        has_glb = await asyncio.to_thread(svc.store.exists, key + ".glb")
        return {"ok": True, "png_url": base + ".png", "glb_url": base + ".glb" if has_glb else None,
                "warnings": r.get("warnings") or [], "errors": [], "cached": True}
    res = await svc.builder.preview(spec)
    if not res.get("ok"):
        raise err(422, "invalid", "The preview could not be rendered.", errors=res.get("errors") or [])
    await asyncio.to_thread(svc.store.write_bytes, key + ".png", res["png"], "image/png")
    if res.get("glb"):
        await asyncio.to_thread(svc.store.write_bytes, key + ".glb", res["glb"], "model/gltf-binary")
    return {"ok": True, "png_url": base + ".png", "glb_url": base + ".glb" if res.get("glb") else None,
            "warnings": (r.get("warnings") or []) + (res.get("warnings") or []), "errors": [], "cached": False}


# ------------------------------------------------------------------ orders
@router.post("/api/projects/{pid}/order-request", status_code=201)
async def order_request(request: Request, pid: str):
    svc = svc_of(request)
    b = await _body(request)
    p = await load_project(request, pid)
    pin = str(b.get("pincode") or "").strip()
    if not re.fullmatch(r"[1-9][0-9]{5}", pin):
        raise err(422, "invalid", "Enter a 6-digit Indian pincode.", errors=["pincode: 6 digits, not starting with 0"])
    try:
        qty = int(b.get("quantity", 1))
    except (TypeError, ValueError):
        raise err(422, "invalid", "quantity must be a whole number") from None
    if not 1 <= qty <= 20:
        raise err(422, "invalid", "quantity must be between 1 and 20")
    note = str(b.get("note") or "").strip()
    if len(note) > 1000:
        raise err(422, "invalid", "note is too long (1000 characters at most)")
    n = b.get("version", p.get("current_version"))
    v = find_version(p, n)
    if not v or v.get("state") != "done":
        raise err(409, "conflict", "Only a finished version can be requested.")
    quote = await asyncio.to_thread(svc.store.read_json, vkey(pid, n, "order/quote.json"))
    if quote is None:
        build = await asyncio.to_thread(svc.store.read_json, vkey(pid, n, "build.json")) or {}
        quote = build.get("order")
    req = {"id": new_id("o"), "created": now_iso(), "version": n, "pincode": pin, "quantity": qty, "note": note,
           "status": "requested",
           "status_text": "Request received. This is not an order: nothing is charged or reserved, and the price "
                          "shown is a snapshot that may change before a binding quote.",
           "quote": quote}

    def mut(pp):
        pp.setdefault("order_requests", []).append(req)
    await _update(request, pid, mut)
    return {"ok": True, "request": req, "quote": quote}


# ------------------------------------------------------------------ share
@router.post("/api/projects/{pid}/share")
async def share_create(request: Request, pid: str):
    svc = svc_of(request)
    p = await load_project(request, pid, reconcile=False)
    tok = (p.get("share") or {}).get("token")
    if not tok:
        tok = new_id("s", 24)
        await asyncio.to_thread(svc.store.write_json, f"shares/{tok}.json", {"pid": pid, "created": now_iso()})

        def mut(pp):
            pp["share"] = {"token": tok, "created": now_iso()}
        await _update(request, pid, mut)
    return {"token": tok, "url": f"/s/{tok}"}


@router.delete("/api/projects/{pid}/share")
async def share_delete(request: Request, pid: str):
    svc = svc_of(request)
    p = await load_project(request, pid, reconcile=False)
    tok = (p.get("share") or {}).get("token")
    if tok:
        await asyncio.to_thread(svc.store.delete, f"shares/{tok}.json")
    await _update(request, pid, lambda pp: pp.update(share=None))
    return {"ok": True}

