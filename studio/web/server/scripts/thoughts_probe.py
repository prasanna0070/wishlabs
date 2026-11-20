"""Probe: do the design agent's models stream thought summaries on Vertex AI when include_thoughts is on?

One streamed function-calling call per model, shaped like the agent's first turn (the agent's own clarify-stage
tools, automatic function calling off), with ThinkingConfig(include_thoughts=True) plus the optional thinking level.
Prints per model: thought parts and characters, seconds to the first thought and to the first answer part, answer
text, function calls, and the usage metadata (thinking tokens are billed as output tokens either way).

  cd web/server && python scripts/thoughts_probe.py                 # MODEL_PRO and MODEL_FAST from config
  python scripts/thoughts_probe.py --full-prompt                    # with the real system prompt (~14k tokens)
  AGENT_THINKING=low python scripts/thoughts_probe.py               # also set thinking_level
  python scripts/thoughts_probe.py --no-thoughts                    # the current production call, for comparison
  python scripts/thoughts_probe.py --two-step                       # also send the model's parts (thought summaries
                                                                    # included) back with a tool result: no 400
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PROMPT = ("I want a cute robot that tells my daughter bedtime stories and remembers her favourite characters. "
          "Ask me what you need to know first.")
SHORT_SYSTEM = ("You are a friendly hardware design assistant. Before proposing anything, ask the user at most three "
                "clarifying questions with the ask_user tool.")


async def probe(model: str, full_prompt: bool, thoughts: bool, two_step: bool = False) -> dict:
    from google.genai import types

    from app.agent.tools import allowed_tools, declarations
    from app.config import Settings
    from app.genai_client import get_client

    s = Settings.from_env()
    system = SHORT_SYSTEM
    if full_prompt:
        from app.agent.prompt import PromptBuilder
        from app.engine_bridge import EngineBridge

        system = PromptBuilder(EngineBridge(s), s).system_prompt()
    tk: dict = {}
    if s.thinking_level:
        tk["thinking_level"] = s.thinking_level
    if thoughts:
        tk["include_thoughts"] = True
    cfg = types.GenerateContentConfig(
        system_instruction=system, temperature=1.0, max_output_tokens=32768,
        tools=[types.Tool(function_declarations=declarations(allowed_tools("clarify", 3, False, False)))],
        tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="AUTO")),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        **({"thinking_config": types.ThinkingConfig(**tk)} if tk else {}))
    client = get_client(s)
    t0 = time.time()
    out = {"model": model, "include_thoughts": thoughts, "thinking_level": s.thinking_level,
           "full_prompt": full_prompt, "thought_parts": 0, "thought_chars": 0, "first_thought_s": None,
           "first_answer_s": None, "answer_text": "", "calls": [], "thoughts": [], "chunks": 0, "usage": None,
           "signatures": 0}
    contents = [types.Content(role="user", parts=[types.Part(text=PROMPT)])]
    stream = await client.aio.models.generate_content_stream(model=model, contents=contents, config=cfg)
    last = None
    parts, fcs = [], []
    async for chunk in stream:
        out["chunks"] += 1
        if getattr(chunk, "usage_metadata", None) is not None:
            last = chunk.usage_metadata
        for cand in chunk.candidates or []:
            for part in (cand.content.parts if cand.content else None) or []:
                parts.append(part)
                if part.function_call:
                    fcs.append(part.function_call)
                if getattr(part, "thought_signature", None):
                    out["signatures"] += 1
                    out.setdefault("signature_on", []).append(
                        "thought" if part.thought else "call" if part.function_call else "text")
                if part.thought and part.text:
                    out["thought_parts"] += 1
                    out["thought_chars"] += len(part.text)
                    out["thoughts"].append(part.text)
                    if out["first_thought_s"] is None:
                        out["first_thought_s"] = round(time.time() - t0, 2)
                elif part.function_call:
                    out["calls"].append({"name": part.function_call.name,
                                         "args": json.dumps(dict(part.function_call.args or {}))[:300]})
                    out["first_answer_s"] = out["first_answer_s"] or round(time.time() - t0, 2)
                elif part.text:
                    out["answer_text"] += part.text
                    out["first_answer_s"] = out["first_answer_s"] or round(time.time() - t0, 2)
    out["seconds"] = round(time.time() - t0, 2)
    if last is not None:
        out["usage"] = {k: getattr(last, k, None) for k in ("prompt_token_count", "candidates_token_count",
                                                            "thoughts_token_count", "total_token_count")}
    if two_step and fcs:
        # the agent keeps the model's own Content verbatim within a turn (thought parts and signatures included)
        contents.append(types.Content(role="model", parts=parts))
        contents.append(types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
            id=fc.id, name=fc.name, response={"ok": False, "error": "The user is away; just say one short sentence "
                                                                       "with the say tool."})) for fc in fcs]))
        t1 = time.time()
        step2 = {"thought_parts": 0, "calls": [], "text": ""}
        stream = await client.aio.models.generate_content_stream(model=model, contents=contents, config=cfg)
        async for chunk in stream:
            for cand in chunk.candidates or []:
                for part in (cand.content.parts if cand.content else None) or []:
                    if part.thought and part.text:
                        step2["thought_parts"] += 1
                    elif part.function_call:
                        step2["calls"].append(part.function_call.name)
                    elif part.text:
                        step2["text"] += part.text
        step2["seconds"] = round(time.time() - t1, 2)
        out["step2"] = step2
    return out


async def main() -> int:
    from app.config import Settings

    s = Settings.from_env()
    full = "--full-prompt" in sys.argv
    thoughts = "--no-thoughts" not in sys.argv
    models = [a for a in sys.argv[1:] if not a.startswith("--")] or [s.model_pro, s.model_fast]
    rc = 0
    for m in models:
        try:
            r = await probe(m, full, thoughts, "--two-step" in sys.argv)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"model": m, "error": f"{type(exc).__name__}: {str(exc)[:400]}"}))
            rc = 1
            continue
        joined = "\n---\n".join(r.pop("thoughts"))
        print(json.dumps(r, ensure_ascii=False))
        print(f"--- {m}: thought text ({len(joined)} chars) ---\n{joined[:2500]}\n")
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
