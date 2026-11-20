"""Gemini brain: streamed generate_content with function calling on Vertex AI (global). Within one turn the
model's own Content objects (with thought signatures) are kept verbatim; across turns history is plain text."""
from __future__ import annotations

import asyncio
import logging
import re
import time

from ..genai_client import get_client
from ..projects import make_message
from ..usage import record_from_response
from .history import build_contents
from .prompt import PromptBuilder, wish_of
from .tools import TERMINAL, TOOLS, declarations
from .voice import lint

log = logging.getLogger("gstudio.gemini")

MAX_STEPS = 8
# a-to-e 1 (BENCH keychain-cap): Flash sometimes writes a tool call as message text ("`propose_concepts`{...}") instead
# of calling it, and the turn ended with nothing done. Such a reply is replaced by a short line and the model is asked,
# at most twice per turn, to make the real call.
TOOL_AS_TEXT = re.compile(r"^\s*`{0,3}\s*(" + "|".join(sorted(TOOLS, key=len, reverse=True)) + r")\s*`{0,3}\s*[({]")
TOOL_TEXT_NUDGE = ("Your last reply wrote the {name} tool call as message text. Call {name} now as a real function call "
                   "with the same arguments; message text must never contain tool names, code or JSON.")
TOOL_TEXT_RETRIES = 2


def tool_written_as_text(text: str | None) -> str | None:
    """The tool name when a reply's text is a tool call written out as text, else None."""
    m = TOOL_AS_TEXT.match(str(text or ""))
    return m.group(1) if m else None


class GeminiBrain:
    kind = "gemini"

    def __init__(self, svc):
        self.svc = svc
        self.prompts = PromptBuilder(svc.bridge, svc.settings)

    def _config(self, allowed: list[str], wish: str | None = None):
        from google.genai import types

        s = self.svc.settings
        kw = dict(
            system_instruction=self.prompts.system_prompt(wish),
            temperature=1.0,
            max_output_tokens=32768,
            tools=[types.Tool(function_declarations=declarations(allowed))],
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="AUTO")),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        if s.thinking_level:
            kw["thinking_config"] = types.ThinkingConfig(thinking_level=s.thinking_level)
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
                    parts.append(part)
                    if part.function_call:
                        calls.append(part.function_call)
                    elif part.text and not part.thought:
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

    async def _call_with_retry(self, turn, model: str, contents, config):
        last = None
        for attempt in range(3):
            try:
                return await self._stream(turn, model, contents, config)
            except Exception as exc:  # noqa: BLE001 - google.genai.errors.APIError and transport errors
                last = exc
                code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
                log.warning("model %s attempt %d failed: %s %s", model, attempt + 1, code, str(exc)[:300])
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
        wish = wish_of(p)  # picks the worked examples closest to the user's own words (CATALOG-PLAN E3)
        text_calls = 0
        for step in range(MAX_STEPS):
            config = self._config(allowed, wish)
            try:
                parts, calls, msg = await self._call_with_retry(turn, model, contents, config)
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
            leaked = tool_written_as_text(msg.get("text")) if (msg and not calls) else None
            retry = bool(leaked) and text_calls < TOOL_TEXT_RETRIES
            if leaked:  # never leave a raw tool call in the chat (message.done replaces the streamed text by id)
                msg = {**msg, "text": "Working on it." if retry else "I could not finish that step. Please try again."}
            if msg:
                await turn.add_message(msg)
                await turn.emit("message.done", {"message": msg})
            if not parts:
                await turn.fail("The assistant returned nothing. Please try again.", code="empty", retryable=True)
                return
            contents.append(types.Content(role="model", parts=parts))
            if not calls:
                if retry:
                    text_calls += 1
                    log.warning("model wrote %s as text; asking for a real call (%d)", leaked, text_calls)
                    contents.append(types.Content(role="user", parts=[types.Part(text=TOOL_TEXT_NUDGE.format(name=leaked))]))
                    await turn.status("Thinking")
                    continue
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
