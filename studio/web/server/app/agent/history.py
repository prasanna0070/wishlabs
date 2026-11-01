"""Chat history -> model contents. Past turns are rendered as plain text (no stale function-call parts, so no
thought-signature bookkeeping across turns); the latest user message carries a <state> block with the stage,
the remaining question budget, the selected concept, the brief and the current spec."""
from __future__ import annotations

import json

from ..brief import brief_text
from ..projects import find_concept, find_version


def render_message(m: dict, p: dict) -> tuple[str, str]:
    """(role 'user'|'model', text)"""
    k, pl = m["kind"], m.get("payload") or {}
    if m["role"] == "user":
        if k == "tweak":
            return "user", f"[I edited the design directly: {m['text']}. That made version {pl.get('version')}.]"
        return "user", m["text"]
    if k == "choices":
        qs = pl.get("questions") or []
        body = "\n".join(f"- ({q.get('id')}) {q.get('question')} [{' / '.join(q.get('options') or [])}] "
                         f"-> {q.get('consequence')}" for q in qs)
        return "model", f"[I asked]\n{body}"
    if k == "concepts":
        lines = []
        for cid in pl.get("concept_ids") or []:
            c = find_concept(p, cid)
            if c:
                lines.append(f"- [{c.get('tier') or '-'}] {c['title']} ({cid}): {c.get('blurb', '')}"
                             + ("" if c.get("state") == "ready" else f" [render {c.get('state')}]"))
        return "model", f"{m['text']}\n[I proposed concepts]\n" + "\n".join(lines)
    if k == "brief":
        return "model", f"{m['text']}\n[I wrote brief v{pl.get('brief_version')}]"
    if k == "progress":
        return "model", f"[Build of version {pl.get('version')} started]"
    if k == "result":
        return "model", f"[Build of version {pl.get('version')} finished. Summary shown to the user:]\n{m['text']}"
    if k == "error":
        return "model", f"[Error shown to the user: {m['text']}]"
    return "model", m["text"]


def _current_spec(svc, p: dict) -> dict | None:
    n = p.get("current_version")
    if n is None:
        return None
    # the design-level input spec (what the model wrote), not the engine-normalized v<n>/spec.json
    inp = svc.store.read_json(f"projects/{p['id']}/inputs/v{n}.json") or {}
    return inp.get("spec") or svc.store.read_json(f"projects/{p['id']}/v{n}/spec.json")


def state_block(svc, p: dict, allowed: list[str], instruction: str | None) -> str:
    left = max(0, 3 - int(p.get("questions_asked") or 0))
    lines = ["<state>", f"stage: {p['stage']}", f"questions asked so far: {p.get('questions_asked', 0)} "
             f"(you may ask {left} more)", f"tools you may use now: {', '.join(allowed)}"]
    if p["stage"] in ("concepts",) and p.get("concepts"):
        latest = next((m for m in reversed(p["chat"]) if m["kind"] == "concepts"), None)
        ids = set(((latest or {}).get("payload") or {}).get("concept_ids") or [])
        lines.append("current concepts (tier, title, id, spec):")
        for c in p["concepts"]:
            if c["id"] in ids:
                lines.append(f"- [{c.get('tier')}] {c['title']} ({c['id']}): "
                             f"{json.dumps(c['spec'], separators=(',', ':'))}")
    sel = find_concept(p, p.get("selected_concept"))
    if sel:
        lines.append(f"selected concept: {sel['title']} ({sel['id']})")
        if p["stage"] in ("brief",) or not p.get("versions"):
            lines.append("selected concept spec: " + json.dumps(sel["spec"], separators=(",", ":")))
    b = p.get("brief")
    if b:
        lines.append(("approved " if b.get("approved") else "latest ") + brief_text(b))
        if p["stage"] == "brief" and b.get("spec") is not (sel or {}).get("spec"):
            lines.append("brief spec: " + json.dumps(b.get("spec"), separators=(",", ":")))
    v = find_version(p, p.get("current_version"))
    if v:
        lines.append(f"current version: v{v['n']} ({v['state']}) {v.get('label', '')}: {v.get('summary', '')}")
        spec = _current_spec(svc, p)
        if spec:
            lines.append("current spec (edit THIS for update_design): " + json.dumps(spec, separators=(",", ":")))
    lines.append("</state>")
    if instruction:
        lines.append(f"<instruction>{instruction}</instruction>")
    return "\n".join(lines)


def build_contents(svc, p: dict, allowed: list[str], instruction: str | None):
    from google.genai import types

    turns: list[tuple[str, str]] = []
    for m in p["chat"]:
        role, text = render_message(m, p)
        if turns and turns[-1][0] == role:
            turns[-1] = (role, turns[-1][1] + "\n\n" + text)
        else:
            turns.append((role, text))
    if not turns or turns[0][0] != "user":
        turns.insert(0, ("user", "(The user opened a new project.)"))
    state = state_block(svc, p, allowed, instruction)
    if turns[-1][0] != "user":
        turns.append(("user", state))
    else:
        turns[-1] = ("user", turns[-1][1] + "\n\n" + state)
    return [types.Content(role=r, parts=[types.Part.from_text(text=t)]) for r, t in turns]
