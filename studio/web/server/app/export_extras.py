"""MAJ-38: what the export carries besides the engine's delivery.

* references/: the chosen concept's photo and CAD render plus references/concept.json. They are written into the
  version folder before the build starts, so the package stage lists them in manifest.json like any other
  delivered file. When a builder could not see them (remote builder, temp dir), finalize() adds them to
  manifest.json and export.zip afterwards.
* project-record.json at the zip root, next to the <slug>/ folder and outside manifest.json: the conversation,
  brief history and version list at the time the version was finalized.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import zipfile
from typing import Any

from .projects import find_concept, vkey

log = logging.getLogger("gstudio.export")

ZIP_TIME = (2026, 1, 1, 0, 0, 0)
RECORD = "project-record.json"
PHOTO_EXT = ("png", "jpg", "webp")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def concept_for(p: dict, brief: dict | None) -> dict | None:
    cid = (brief or {}).get("concept_id") or p.get("selected_concept")
    return find_concept(p, cid)


def seed_references(store, pid: str, n: int, concept: dict | None) -> list[str]:
    """Copy the concept photo/render into v<n>/references/ and write references/concept.json. Returns the rels."""
    if not concept:
        return []
    cid = concept["id"]
    src = f"projects/{pid}/concepts/{cid}"
    rels: list[str] = []
    photo_rel = render_rel = None
    for ext in PHOTO_EXT:
        b = store.read_bytes(f"{src}/photo.{ext}")
        if b:
            photo_rel = f"references/concept-{cid}.{ext}"
            store.write_bytes(vkey(pid, n, photo_rel), b, f"image/{'jpeg' if ext == 'jpg' else ext}")
            rels.append(photo_rel)
            break
    b = store.read_bytes(f"{src}/render.png")
    if b:
        render_rel = f"references/concept-{cid}-render.png"
        store.write_bytes(vkey(pid, n, render_rel), b, "image/png")
        rels.append(render_rel)
    doc = {"schema": "gadget-concept-reference/1", "id": cid, "title": concept.get("title"),
           "tier": concept.get("tier"), "blurb": concept.get("blurb"), "photo": photo_rel, "render": render_rel,
           "note": ("The render is the CAD preview of this concept's shell. The photo, when present, is an AI image "
                    "generated from that render: it shows the look, not a manufactured part. The CAD files in this "
                    "export are the design.")}
    store.write_bytes(vkey(pid, n, "references/concept.json"),
                      json.dumps(doc, indent=1, ensure_ascii=False).encode(), "application/json")
    rels.append("references/concept.json")
    return rels


def _lite_payload(m: dict) -> Any:
    pl = m.get("payload") or {}
    k = m.get("kind")
    if k == "choices":
        return {"questions": [{x: q.get(x) for x in ("id", "question", "consequence", "options")}
                              for q in pl.get("questions") or []],
                "answers": pl.get("answers"), "skipped": pl.get("skipped"), "defaults": pl.get("defaults")}
    if k == "concepts":
        return {"concept_ids": pl.get("concept_ids"), "tiers": pl.get("tiers"), "selected": pl.get("selected")}
    if k in ("brief",):
        return {"brief_version": pl.get("brief_version")}
    if k in ("progress", "result", "error"):
        return {"version": pl.get("version")}
    if k == "tweak":
        return {"version": pl.get("version"), "patch": pl.get("patch"), "scale": pl.get("scale")}
    if m.get("role") == "user" and pl.get("action"):
        return {"action": pl.get("action")}
    return None


def project_record(p: dict, n: int, created: str) -> dict:
    briefs = []
    for b in p.get("brief_history") or []:
        briefs.append({k: v for k, v in b.items() if k != "spec"})
    return {
        "schema": "gadget-studio-project-record/1",
        "note": ("Conversation, brief history and versions at the time version "
                 f"{n} was finalized. Not listed in manifest.json, because it changes as the project goes on."),
        "project_id": p.get("id"), "name": p.get("name"), "created": p.get("created"), "exported_version": n,
        "written": created,
        "selected_concept": p.get("selected_concept"),
        "concepts": [{k: c.get(k) for k in ("id", "title", "tier", "blurb", "state")} for c in p.get("concepts") or []],
        "chat": [{"role": m.get("role"), "kind": m.get("kind"), "text": m.get("text"), "ts": m.get("ts"),
                  "payload": _lite_payload(m)} for m in p.get("chat") or []],
        "brief_history": briefs,
        "versions": [{k: v.get(k) for k in ("n", "label", "source", "base_version", "brief_version", "spec_hash",
                                            "state", "created", "summary", "changes", "plan")}
                     for v in p.get("versions") or []],
    }


def _zip_top(names: list[str]) -> str | None:
    tops = {nm.split("/", 1)[0] for nm in names if "/" in nm}
    return next(iter(tops)) if len(tops) == 1 else None


def finalize(store, pid: str, n: int, record: dict) -> dict:
    """Make manifest.json and export.zip carry references/ and put project-record.json at the zip root.
    Idempotent. Returns {references_added, record, zip_bytes} (or {skipped: reason})."""
    man = store.read_json(vkey(pid, n, "manifest.json"))
    zb = store.read_bytes(vkey(pid, n, "export.zip"))
    if not isinstance(man, dict) or not zb:
        return {"skipped": "no manifest.json or export.zip in this version"}
    try:
        zin = zipfile.ZipFile(io.BytesIO(zb))
        names = zin.namelist()
    except zipfile.BadZipFile:
        return {"skipped": "export.zip does not open"}
    top = _zip_top(names)
    listed = {f.get("path") for f in man.get("files") or [] if isinstance(f, dict)}
    prefix = f"projects/{pid}/v{n}/"
    refs = [k[len(prefix):] for k in store.list_keys(prefix + "references")]
    ours = [r for r in refs if r.startswith("references/concept")]
    missing = [r for r in ours if r not in listed]
    rec_bytes = json.dumps(record, indent=1, ensure_ascii=False).encode()
    has_record = RECORD in names
    stale_record = False
    if has_record:
        try:
            stale_record = json.loads(zin.read(RECORD)).get("exported_version") != record.get("exported_version")
        except (KeyError, ValueError):
            stale_record = True
    out = {"references_added": missing, "record": RECORD}
    if (missing and top) or stale_record:
        add = {}
        for r in missing if top else []:
            b = store.read_bytes(prefix + r)
            if b is None:
                continue
            add[r] = b
            man.setdefault("files", []).append({"path": r, "bytes": len(b), "sha256": _sha(b)})
        man_bytes = None
        if add:
            man["files"] = sorted(man["files"], key=lambda f: f["path"])
            man_bytes = json.dumps(man, indent=1, ensure_ascii=False).encode()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zout:
            for info in zin.infolist():
                if info.filename == RECORD or (man_bytes and info.filename == f"{top}/manifest.json"):
                    continue
                zout.writestr(info, zin.read(info))
            for r, b in sorted(add.items()):
                zi = zipfile.ZipInfo(f"{top}/{r}", date_time=ZIP_TIME)
                zi.compress_type = zipfile.ZIP_DEFLATED
                zout.writestr(zi, b)
            if man_bytes:
                zi = zipfile.ZipInfo(f"{top}/manifest.json", date_time=ZIP_TIME)
                zi.compress_type = zipfile.ZIP_DEFLATED
                zout.writestr(zi, man_bytes)
            zi = zipfile.ZipInfo(RECORD, date_time=ZIP_TIME)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(zi, rec_bytes)
        if man_bytes:
            store.write_bytes(vkey(pid, n, "manifest.json"), man_bytes, "application/json")
        out["references_added"] = sorted(add)
        new = buf.getvalue()
    elif not has_record:
        zin.close()
        buf = io.BytesIO(zb)
        with zipfile.ZipFile(buf, "a", compression=zipfile.ZIP_DEFLATED) as za:
            zi = zipfile.ZipInfo(RECORD, date_time=ZIP_TIME)
            zi.compress_type = zipfile.ZIP_DEFLATED
            za.writestr(zi, rec_bytes)
        new = buf.getvalue()
    else:
        return {**out, "zip_bytes": len(zb), "unchanged": True}
    store.write_bytes(vkey(pid, n, "export.zip"), new, "application/zip")
    out["zip_bytes"] = len(new)
    return out
