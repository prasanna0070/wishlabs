"""Gemini brain: streamed generate_content with function calling on Vertex AI (global). Within one turn the
model's own Content objects (with thought signatures) are kept verbatim; across turns history is plain text.

Thought summaries (ThinkingConfig include_thoughts, on unless AGENT_THOUGHTS=0) stream to the browser as
thinking.delta events and into the turn's progress record; they never enter the visible answer or the stored chat.
A model that rejects include_thoughts (HTTP 400) is called again without it and remembered: the UI then shows the
step labels only."""
from __future__ import annotations

import asyncio
import logging
import time

from ..genai_client import get_client
from ..projects import make_message
from ..usage import record_from_response
from .history import build_contents
from .prompt import PromptBuilder
from .tools import TERMINAL, declarations
from .voice import lint

log = logging.getLogger("gstudio.gemini")

MAX_STEPS = 8


class GeminiBrain:
    kind = "gemini"

    def __init__(self, svc):
        self.svc = svc
        self.prompts = PromptBuilder(svc.bridge, svc.settings)
        self.no_thoughts: set[str] = set()     # models that rejected include_thoughts in this process

    def thoughts_on(self, model: str) -> bool:
        return bool(getattr(self.svc.settings, "agent_thoughts", True)) and model not in self.no_thoughts

    def _config(self, allowed: list[str], thoughts: bool = False):
        from google.genai import types

        s = self.svc.settings
        kw = dict(
            system_instruction=self.prompts.system_prompt(),
            temperature=1.0,
            max_output_tokens=32768,
            tools=[types.Tool(function_declarations=declarations(allowed))],
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="AUTO")),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        tk: dict = {}
        if s.thinking_level:
            tk["thinking_level"] = s.thinking_level
        if thoughts:
            tk["include_thoughts"] = True       # summaries only; thinking tokens are billed with or without them
        if tk:
            kw["thinking_config"] = types.ThinkingConfig(**tk)
        return types.GenerateContentConfig(**kw)

    async def _stream(self, turn, model: str, contents, config):
        """One model call. Streams text deltas; returns (all parts, function calls, text message or None)."""
        client = get_client(self.svc.settings)
        parts, calls = [], []
        msg_id = None
        text = ""
        t0 = time.time()
        last = None
        stream = await client.aio.models.generate_content_stream(model=model, contents=contents, config=config)
        async for chunk in stream:
            if getattr(chunk, "usage_metadata", None) is not None:
                last = chunk
            for cand in chunk.candidates or []:
                for part in (cand.content.parts if cand.content else None) or []:
                    parts.append(part)                  # kept verbatim for the next call (thought signatures)
                    if part.function_call:
                        calls.append(part.function_call)
                    elif part.thought:                  # a thought summary: the thinking panel only, never the answer
                        if part.text:
                            await turn.thought(part.text)
                    elif part.text:
                        if msg_id is None:
                            msg_id = make_message("assistant", "text", "")["id"]
                            await turn.emit("message.start", {"message_id": msg_id, "role": "assistant",
                                                              "kind": "text"})
                        text += part.text
                        await turn.emit("text.delta", {"message_id": msg_id, "delta": part.text})
        if last is not None:
            turn.record_usage(record_from_response(model, last, "text", time.time() - t0))
        msg = None
        if msg_id and text.strip():
            msg = {**make_message("assistant", "text", lint(text)), "id": msg_id}
        return parts, calls, msg

    async def _call_with_retry(self, turn, model: str, contents, allowed: list[str]):
        last = None
        plain = False                               # True after a 400 with thought summaries on: retry without them
        for attempt in range(3):
            thoughts = self.thoughts_on(model) and not plain
            try:
                out = await self._stream(turn, model, contents, self._config(allowed, thoughts))
                if plain:
                    log.warning("model %s rejects include_thoughts; showing step labels only", model)
                    self.no_thoughts.add(model)
                return out
            except Exception as exc:  # noqa: BLE001 - google.genai.errors.APIError and transport errors
                last = exc
                code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
                log.warning("model %s attempt %d failed: %s %s", model, attempt + 1, code, str(exc)[:300])
                if code == 400 and thoughts:
                    plain = True
                    continue
                if code in (400, 401, 403, 404):
                    raise
                await asyncio.sleep(2 * (attempt + 1))
        raise last  # type: ignore[misc]

    async def run(self, turn) -> None:
        from google.genai import types

        s = self.svc.settings
        model = s.model_for(turn.mode)
        p = await turn.project()
        allowed = turn.allowed(p)
        contents = await asyncio.to_thread(build_contents, self.svc, p, allowed, turn.instruction)
        await turn.status("Thinking")
        for step in range(MAX_STEPS):
            try:
                parts, calls, msg = await self._call_with_retry(turn, model, contents, allowed)
            except Exception as exc:  # noqa: BLE001
                code = getattr(exc, "code", None)
                if code == 404 and model != s.model_fast:
                    log.warning("model %s not available, falling back to %s", model, s.model_fast)
                    model = s.model_fast
                    continue
                log.exception("model call failed")
                await turn.fail("The design assistant is unavailable right now. Please try again in a minute.",
                                code="model_unavailable", retryable=True)
                return
            if msg:
                await turn.add_message(msg)
                await turn.emit("message.done", {"message": msg})
            if not parts:
                await turn.fail("The assistant returned nothing. Please try again.", code="empty", retryable=True)
                return
            contents.append(types.Content(role="model", parts=parts))
            if not calls:
                return
            responses, terminal = [], False
            for fc in calls:
                res = await turn.call_tool(fc.name, dict(fc.args or {}))
                responses.append(types.Part(function_response=types.FunctionResponse(
                    id=fc.id, name=fc.name, response=res.response)))
                terminal = terminal or (res.terminal and fc.name in TERMINAL)
            if terminal:
                return
            contents.append(types.Content(role="user", parts=responses))
            p = await turn.project()
            allowed = turn.allowed(p)
            await turn.status("Fixing the design" if any(not r.function_response.response.get("ok")
                                                         for r in responses) else "Thinking")
        await turn.fail("I went round in circles on that one. Could you say it another way?", code="too_many_steps")
