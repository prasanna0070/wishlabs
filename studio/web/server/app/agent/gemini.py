"""Gemini brain: streamed generate_content with function calling on Vertex AI (global). Within one turn the
model's own Content objects (with thought signatures) are kept verbatim; across turns history is plain text.

Thought summaries (ThinkingConfig include_thoughts, on unless AGENT_THOUGHTS=0) stream to the browser as
thinking.delta events and into the turn's progress record; they never enter the visible answer or the stored chat.
A model that rejects include_thoughts (HTTP 400) is called again without it and remembered: the UI then shows the
step labels only."""
from __future__ import annotations

import asyncio
import logging
import re
import time

from ..genai_client import get_client
from ..projects import make_message
from ..usage import record_from_response
from .history import build_contents
from .image_rules import with_rules
from .prompt import PromptBuilder, wish_of
from .tools import TERMINAL, TOOLS, declarations
from .voice import lint

log = logging.getLogger("gstudio.gemini")

MAX_STEPS = 8
# a-to-e 1 (BENCH keychain-cap): Flash sometimes writes a tool call as message text ("`propose_concepts`{...}") instead
# of calling it, and the turn ended with nothing done. Such a reply is replaced by a short line and the model is asked,
# at most twice per turn, to make the real call.
_TOOL_NAMES = "|".join(sorted(TOOLS, key=len, reverse=True))
# Also Gemini's own call notation, seen live on 2026-11-20 as a 9,650-character chat message
# ("‹call:default_api:propose_concepts{...}"), and the print(default_api.name(...)) form, with or without a fence.
TOOL_AS_TEXT = re.compile(r"^\s*(?:`{1,3}[A-Za-z]*)?\s*(?:[‹<«]?\s*call\s*:\s*)?(?:print\s*\(\s*)?"
                          r"(?:default_api\s*[.:]\s*)?(" + _TOOL_NAMES + r")\s*`{0,3}\s*[({]")
# default_api never appears in words meant for a person, so it marks a written-out call anywhere in a reply.
TOOL_CALL_ANYWHERE = re.compile(r"default_api\s*[.:]\s*(" + _TOOL_NAMES + r")\s*[({]")
TOOL_TEXT_NUDGE = ("Your last reply wrote the {name} tool call as message text. Call {name} now as a real function call "
                   "with the same arguments; message text must never contain tool names, code or JSON.")
TOOL_TEXT_RETRIES = 2
# A reply with no tool call and no words (finish STOP, 0 tokens, ~2.5 s) comes back now and then from Vertex, with or
# without thought summaries: 2 of 6 replays of one stored state on 2026-11-20 (scripts/replay_turn.py). Asking the same
# thing again gets a real answer, so the turn does that instead of failing with "I could not finish that step".
# Only before the turn has answered: an empty reply after `say` (the only tool during a build) just means "nothing to
# add", and asking again there repeated the answer. The retries do not use up MAX_STEPS.
EMPTY_RETRIES = 2
EMPTY_RETRY_DELAY_S = 1.5
# Finish reasons worth asking again for: the flake above (STOP), none or unspecified, and a function call the model wrote
# badly. MAX_TOKENS (thinking used the whole budget) and the safety stops would only repeat at the price of a full call.
RETRY_FINISH = frozenset({"STOP", "FINISH_REASON_UNSPECIFIED", "MALFORMED_FUNCTION_CALL"})


def finish_name(finish) -> str | None:
    """types.FinishReason (a str enum) or a raw string -> 'STOP', 'MAX_TOKENS', ...; None when the stream gave none."""
    if finish is None:
        return None
    return str(getattr(finish, "name", None) or finish).upper()


def retry_empty(finish) -> bool:
    name = finish_name(finish)
    return name is None or name in RETRY_FINISH


def tool_written_as_text(text: str | None) -> str | None:
    """The tool name when a reply's text is a tool call written out as text, else None."""
    s = str(text or "")
    m = TOOL_AS_TEXT.match(s) or TOOL_CALL_ANYWHERE.search(s)
    return m.group(1) if m else None


class GeminiBrain:
    kind = "gemini"

    def __init__(self, svc):
        self.svc = svc
        self.prompts = PromptBuilder(svc.bridge, svc.settings)
        self.no_thoughts: set[str] = set()     # models that rejected include_thoughts in this process

    def thoughts_on(self, model: str) -> bool:
        return bool(getattr(self.svc.settings, "agent_thoughts", True)) and model not in self.no_thoughts

    def _packs_on(self) -> frozenset:
        """The packs that are on (the propose_concepts wording follows pack `freeform`); none when unknown."""
        fn = getattr(self.prompts, "packs_on", None)
        try:
            return frozenset(fn()) if callable(fn) else frozenset()
        except Exception:  # noqa: BLE001
            return frozenset()

    def _config(self, allowed: list[str], thoughts: bool = False, images: bool = False, wish: str | None = None):
        from google.genai import types

        s = self.svc.settings
        kw = dict(
            system_instruction=with_rules(self.prompts.system_prompt(wish), images),   # image rules only with images
            temperature=1.0,
            max_output_tokens=32768,
            tools=[types.Tool(function_declarations=declarations(allowed, self._packs_on()))],
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
        """One model call. Streams text deltas; returns (all parts, function calls, text message or None, finish
        reason or None)."""
        client = get_client(self.svc.settings)
        parts, calls = [], []
        msg_id = None
        text = ""
        t0 = time.time()
        last = None
        finish = None
        stream = await client.aio.models.generate_content_stream(model=model, contents=contents, config=config)
        async for chunk in stream:
            if getattr(chunk, "usage_metadata", None) is not None:
                last = chunk
            for cand in chunk.candidates or []:
                finish = getattr(cand, "finish_reason", None) or finish
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
        if not calls and not text.strip():
            log.warning("model %s replied with no tool call and no text: finish=%s, %d parts (%d thought)", model,
                        finish, len(parts), sum(1 for x in parts if x.thought))
        msg = None
        if msg_id and text.strip():
            msg = {**make_message("assistant", "text", lint(text)), "id": msg_id}
        return parts, calls, msg, finish

    async def _call_with_retry(self, turn, model: str, contents, allowed: list[str], wish: str | None = None):
        last = None
        plain = False                               # True after a 400 with thought summaries on: retry without them
        for attempt in range(3):
            thoughts = self.thoughts_on(model) and not plain
            try:
                config = self._config(allowed, thoughts, images=getattr(turn, "images", False), wish=wish)
                out = await self._stream(turn, model, contents, config)
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
        wish = wish_of(p)  # picks the worked examples closest to the user's own words (CATALOG-PLAN E3)
        text_calls = 0
        empty = 0
        step = 0
        owed = False                                    # the last tool results asked the model to fix something
        while step < MAX_STEPS:
            try:
                parts, calls, msg, finish = await self._call_with_retry(turn, model, contents, allowed, wish)
            except Exception as exc:  # noqa: BLE001
                code = getattr(exc, "code", None)
                if code == 404 and model != s.model_fast:
                    log.warning("model %s not available, falling back to %s", model, s.model_fast)
                    model = s.model_fast
                    continue                            # the same step on the fallback model
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
            if not calls and not msg:
                if (turn.posted or turn.build_n is not None) and not owed:
                    return                              # it answered (say) and has nothing to add: the turn is done
                if empty < EMPTY_RETRIES and retry_empty(finish):
                    empty += 1                          # an empty reply (see EMPTY_RETRIES): ask the same thing again
                    log.warning("asking %s again after an empty reply (finish=%s, retry %d of %d)", model,
                                finish_name(finish), empty, EMPTY_RETRIES)
                    await turn.status("Thinking again")
                    await asyncio.sleep(EMPTY_RETRY_DELAY_S * empty)
                    continue                            # not a step: MAX_STEPS counts real replies only
            step += 1
            if not parts:
                await turn.fail("The assistant returned nothing. Please try again.", code="empty", retryable=True)
                return
            contents.append(types.Content(role="model", parts=parts))
            if not calls:
                if retry:
                    text_calls += 1
                    log.warning("model wrote %s as text; asking for a real call (%d)", leaked, text_calls)
                    contents.append(types.Content(role="user", parts=[types.Part(text=TOOL_TEXT_NUDGE.format(name=leaked))]))
                    owed = True                         # the nudge asks for a call: an empty reply is asked again
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
            wish = wish_of(p)                           # an image brief recorded this round now picks the examples
            owed = any(not r.function_response.response.get("ok") for r in responses)
            await turn.status("Fixing the design" if owed else "Thinking")
        await turn.fail("I went round in circles on that one. Could you say it another way?", code="too_many_steps")
