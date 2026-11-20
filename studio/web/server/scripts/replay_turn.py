"""Replay one design-agent model call from a stored project, N times, with and without thought summaries, and print
what came back (finish reason, parts, usage). For checking whether include_thoughts changes the agent's answers.

  cd web/server && python scripts/replay_turn.py <project.json> [--n 3] [--instruction "..."] [--modes on,off]

The project's trailing assistant error messages are dropped, so the call is the one that produced them.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def one(svc, brain, model, contents, allowed, thoughts: bool) -> dict:
    from app.genai_client import get_client

    cfg = brain._config(allowed, thoughts)
    t0 = time.time()
    stream = await get_client(svc.settings).aio.models.generate_content_stream(model=model, contents=contents,
                                                                               config=cfg)
    parts, finish, last = [], None, None
    async for chunk in stream:
        if getattr(chunk, "usage_metadata", None) is not None:
            last = chunk.usage_metadata
        for cand in chunk.candidates or []:
            if cand.finish_reason:
                finish = str(cand.finish_reason)
            for p in (cand.content.parts if cand.content else None) or []:
                parts.append("thought(%d)" % len(p.text or "") if p.thought else
                             "call:%s" % p.function_call.name if p.function_call else
                             "text(%d)%s" % (len(p.text or ""), "+sig" if p.thought_signature else "")
                             if p.text is not None else "other%s" % ("+sig" if p.thought_signature else ""))
    u = {k: getattr(last, k, None) for k in ("prompt_token_count", "candidates_token_count", "thoughts_token_count")} \
        if last else None
    return {"thoughts": thoughts, "finish": finish, "parts": parts, "usage": u, "s": round(time.time() - t0, 1)}


async def main() -> None:
    from app.agent.gemini import GeminiBrain
    from app.agent.history import build_contents
    from app.agent.tools import allowed_tools
    from app.config import Settings
    from app.services import Services

    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--instruction", default="The user skipped the questions: use your sensible defaults and say which.")
    ap.add_argument("--modes", default="on,off")
    a = ap.parse_args()
    p = json.loads(Path(a.project).read_text())
    while p["chat"] and p["chat"][-1]["role"] == "assistant" and p["chat"][-1]["kind"] == "error":
        p["chat"].pop()
    s = Settings.from_env()
    s.photos = False
    svc = Services(s)
    brain = GeminiBrain(svc)
    model = s.model_for(p.get("mode"))
    allowed = allowed_tools(p["stage"], max(0, 3 - int(p.get("questions_asked") or 0)), bool(p.get("selected_concept")),
                            bool(p.get("versions")))
    contents = build_contents(svc, p, allowed, a.instruction)
    print(json.dumps({"model": model, "allowed": allowed, "stage": p["stage"], "messages": len(p["chat"])}))
    for i in range(a.n):
        for mode in a.modes.split(","):
            try:
                print(json.dumps(await one(svc, brain, model, contents, allowed, mode == "on")), flush=True)
            except Exception as exc:  # noqa: BLE001
                print(json.dumps({"thoughts": mode == "on", "error": f"{type(exc).__name__}: {str(exc)[:300]}"}))


if __name__ == "__main__":
    asyncio.run(main())
