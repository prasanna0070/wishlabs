"""One chat turn: classify the request, store the user message, run the brain (Gemini or scripted) or a direct
action, execute tool calls with validation + repair rounds, and emit SSE events. Used by both brains."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import defaultdict
from typing import Any, Awaitable, Callable

from .. import changes as ch
from .. import concept_rules
from ..brief import assemble_brief, glb_bbox_mm, parts_from_spec, same_geometry, size_from_spec, user_texts
from ..builds import BuildBusy
from ..concepts import render_concepts
from ..progress import TURN_STALE_S, TurnProgress
from ..projects import find_concept, find_version, last_message, make_message, vkey
from ..usage import apply_turn, sum_records
from ..util import new_id, now_iso, parse_iso
from .tools import LABELS, TIERS, allowed_tools
from .voice import lint

log = logging.getLogger("gstudio.turn")

Emit = Callable[[str, dict], Awaitable[None]]
MAX_CONCEPTS = 6
CONCEPT_RULE_ROUNDS = 2


class TurnError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class ToolResult:
    def __init__(self, response: dict, terminal: bool):
        self.response, self.terminal = response, terminal


def _as_spec(x: Any) -> Any:
    if isinstance(x, str):
        try:
            x = json.loads(x)
        except json.JSONDecodeError:
            return x
    if isinstance(x, dict) and set(x) == {"spec"}:
        return _as_spec(x["spec"])
    return x


def _plain(x: Any) -> Any:
    """Proto-ish values from function-call args -> plain JSON (ints stay ints when whole)."""
    if isinstance(x, dict):   # keys trimmed of stray quotes/spaces: the model sometimes emits 'center"'
        return {str(k).strip().strip('"\'').strip(): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, float) and x.is_integer() and abs(x) < 1e12:
        return int(x)
    return x


class Turn:
    def __init__(self, svc, pid: str, emit: Emit):
        self.svc = svc
        self.pid = pid
        self._emit = emit
        self.id = new_id("t")
        self.started: str | None = None         # the lock's start time (set by begin)
        self.progress: TurnProgress | None = None  # turn-progress.json for a page that reloads (set by run)
        self.failed = False
        self.repairs: dict[str, int] = defaultdict(int)
        self.build_n: int | None = None
        self.instruction: str | None = None
        self.forced: str | None = None
        self.direct: str | None = None
        self.action: str | None = None
        self.mode: str | None = None
        self.user_message: dict | None = None
        self.follow_build = True
        self.scope: dict | None = None          # edit_part: {component, label, spec_path}
        self.scope_warned = False
        self.request_text: str = ""             # the user's words for this turn (named paths, MAJ-34)
        self.unsupported: list[dict] = []       # asked-for properties with no parameter (MAJ-34)
        self.usage: list[dict] = []
        self.concept_pool: dict[str, dict] = {}  # title.lower() -> {item, result}
        self.concept_message = ""
        self.concept_defaults: list[str] = []
        self.posted = False

    # ------------------------------------------------------------------ helpers
    async def emit(self, event: str, data: dict) -> None:
        """Every turn event goes to the SSE stream and is folded into the progress record (throttled writes)."""
        if self.progress is not None:
            self.progress.observe(event, data)
        await self._emit(event, data)

    async def thought(self, text: str) -> None:
        """A thought summary from the model: streamed as thinking.delta and kept (the newest ~3 KB) in the progress
        record. Never part of a chat message, the stored answer or a share link."""
        if str(text or "").strip():
            await self.emit("thinking.delta", {"text": str(text)})

    async def project(self) -> dict:
        return await self.svc.repo.aget(self.pid)

    async def update(self, fn) -> dict:
        return await self.svc.repo.aupdate(self.pid, fn)

    async def add_message(self, msg: dict, extra=None) -> dict:
        if msg.get("role") == "assistant":
            self.posted = True

        def mut(p):
            p["chat"].append(msg)
            if extra:
                extra(p)
        await self.update(mut)
        return msg

    async def status(self, text: str) -> None:
        await self.emit("status", {"text": text})

    def record_usage(self, rec: dict) -> None:
        self.usage.append(rec)

    async def fail(self, text: str, code: str = "agent_error", retryable: bool = True) -> None:
        self.failed = True
        msg = make_message("assistant", "error", text, {"code": code, "retryable": retryable})
        await self.add_message(msg)
        await self.emit("message.done", {"message": msg})
        await self.emit("error", {"code": code, "message": text, "retryable": retryable})

    async def say_text(self, text: str) -> dict:
        msg = make_message("assistant", "text", lint(text))
        await self.add_message(msg)
        await self.emit("message.done", {"message": msg})
        return msg

    async def maybe_name(self, name: Any) -> None:
        """The agent names the project once (unless the user renamed it)."""
        name = re.sub(r"\s+", " ", str(name or "")).strip().strip('"')[:60]
        if not name or concept_rules.borrowed(name):     # never another product's name (no third-party branding)
            return
        renamed = {}

        def mut(p):
            if p.get("name_source") in ("user", "agent"):
                return
            p["name"], p["name_source"] = name, "agent"
            renamed["name"] = name
        await self.update(mut)
        if renamed:
            await self.emit("project.renamed", {"name": name})

    async def validate(self, spec: Any) -> dict:
        spec = _as_spec(spec)
        if not isinstance(spec, dict):
            return {"ok": False, "errors": ["spec must be a JSON object (the full gadget-spec/1 document)"],
                    "warnings": [], "normalized": None}
        try:
            return await self.svc.builder.validate(_plain(spec))
        except Exception as exc:  # noqa: BLE001
            log.exception("validate failed")
            return {"ok": False, "errors": [f"validator unavailable: {exc}"[:300]], "warnings": [],
                    "normalized": None, "infra": True}

    def _repair_or_fail(self, tool: str, errors: list[str], instruction: str | None = None,
                        limit: int | None = None) -> ToolResult | None:
        """None when out of repair rounds (caller reports failure to the user)."""
        if self.repairs[tool] >= (self.svc.settings.max_repair_rounds if limit is None else limit):
            return None
        self.repairs[tool] += 1
        return ToolResult({"ok": False, "round": self.repairs[tool], "errors": errors[:30],
                           "instruction": instruction or f"The spec did not validate. Fix every error and call {tool} "
                                                         "again with the complete corrected spec. Do not explain; "
                                                         "just call the tool."},
                          terminal=False)

    # ------------------------------------------------------------------ begin: classify + lock + store message
    async def begin(self, body: dict) -> dict:
        p = await self.project()
        t = p.get("turn")
        if t and _age_s(t.get("started")) < TURN_STALE_S:
            raise TurnError(409, "conflict", "The assistant is still answering the previous message.")
        self.follow_build = bool(body.get("follow_build", True))
        mode = body.get("mode")
        if mode is not None and mode not in ("pro", "fast"):
            raise TurnError(422, "invalid", "mode must be pro or fast")
        action = body.get("action")
        side: list[Callable[[dict], None]] = []
        payload: dict = {}
        if action is None and ("answers" in body or body.get("skipped")):
            action = "answers"
        self.action = action
        if action is None:
            text = str(body.get("text") or "").strip()
            if not text:
                raise TurnError(422, "invalid", "Say something first.")
            if len(text) > 4000:
                raise TurnError(422, "invalid", "That message is too long (4000 characters at most).")
            msg_text = text
            self.request_text = text
            chm = last_message(p, "choices")
            if chm and not (chm.get("payload") or {}).get("answered"):
                side.append(lambda pp, mid=chm["id"]: _mark_answered(pp, mid, {"free_text": text}))
            if p["stage"] == "building":
                self.instruction = "A build is running. Answer briefly; do not start another build."
        elif action == "answers":
            chm = last_message(p, "choices")
            if not chm:
                raise TurnError(422, "invalid", "There is no question to answer.")
            qs = {q["id"]: q for q in (chm.get("payload") or {}).get("questions") or []}
            answers = body.get("answers") or []
            skipped = bool(body.get("skipped"))
            if not isinstance(answers, list) or (not answers and not skipped):
                raise TurnError(422, "invalid", "answers must be a non-empty list (or skipped: true)")
            vals, mapping = [], []
            for a in answers:
                if not isinstance(a, dict) or not str(a.get("value") or "").strip():
                    raise TurnError(422, "invalid", "each answer needs a question_id and a value")
                v = str(a["value"]).strip()[:300]
                vals.append(v)
                q = qs.get(a.get("question_id"))
                mapping.append(f"{q['question'] if q else a.get('question_id')} -> {v}")
            if skipped and not vals:
                msg_text = "My choices:\nUse your suggestions"
                self.instruction = "The user skipped the questions: use your sensible defaults and say which."
            else:
                msg_text = "My choices:\n" + ", ".join(vals)
                self.instruction = "The user answered: " + "; ".join(mapping) + "." + (
                    " Use defaults for anything unanswered." if skipped else "")
            payload = {"answers": answers, "skipped": skipped}
            side.append(lambda pp, mid=chm["id"], ans=answers, sk=skipped:
                        _mark_answered(pp, mid, {"answers": ans, "skipped": sk}))
        elif action in ("select_concept", "skip_concepts"):
            if p["stage"] not in ("concepts", "brief", "clarify", "ready", "error"):
                raise TurnError(409, "conflict", "Concepts cannot be picked while a version is building.")
            if action == "skip_concepts" or body.get("concept_id") is None:
                c = _default_concept(p)
                if not c:
                    raise TurnError(422, "invalid", "There are no concepts to continue from.")
                msg_text = "My choices:\nContinue without an image"
                self.instruction = (f"The user continued without choosing an image. Start from {c['title']} "
                                    f"({c['id']}) as the base shape, adapt it to everything they said, and call "
                                    "write_brief now.")
            else:
                c = find_concept(p, body.get("concept_id"))
                if not c:
                    raise TurnError(404, "not_found", "That concept does not exist.")
                if c.get("state") != "ready":
                    raise TurnError(422, "invalid", "That concept could not be rendered; pick another one.")
                msg_text = f"My choices:\n{c['title']}"
                self.instruction = (f"The user picked the concept {c['title']} ({c['id']}). Call write_brief now for "
                                    "it, using everything they told you.")
                payload = {"concept_id": c["id"], "image_url": c.get("photo_url") or c.get("render_url")}
            self.forced = "write_brief"
            side.append(lambda pp, cid=c["id"]: _select(pp, cid))
        elif action == "approve_brief":
            b = p.get("brief")
            if not b:
                raise TurnError(422, "invalid", "There is no brief to approve yet.")
            if p["stage"] != "brief":
                raise TurnError(409, "conflict", "The brief is not waiting for approval.")
            msg_text = "My choices:\nBuild this"
            self.direct = "approve_brief"
        elif action in ("regenerate_concepts", "more_concepts"):
            if p["stage"] == "building":
                raise TurnError(409, "conflict", "New concepts can be shown once the build finishes.")
            if action == "regenerate_concepts" and p["stage"] not in ("clarify", "concepts", "brief") and \
                    not p.get("versions"):
                raise TurnError(409, "conflict", "New concepts are only offered before the first build.")
            msg_text = "Show me different concepts" if action == "regenerate_concepts" else "Show new concepts"
            self.instruction = ("Propose new concepts that are clearly different from the earlier ones."
                                + (" The current design is already built; a concept the user picks becomes a new "
                                   "brief and a new version." if p.get("versions") else ""))
            self.forced = "propose_concepts"
        elif action == "retry_build":
            v = find_version(p, p.get("current_version"))
            if not v or v["state"] != "error":
                raise TurnError(409, "conflict", "There is no failed build to retry.")
            msg_text = "Try the build again"
            self.direct = "retry_build"
        elif action == "edit_part":
            msg_text = await self._begin_edit_part(p, body)
            payload = dict(self.scope or {})
        else:
            raise TurnError(422, "invalid", f"unknown action {action!r}")

        user_msg = make_message("user", "text", msg_text,
                                ({"action": action, **payload} if action else None))
        self.user_message = user_msg
        self.started = now_iso()

        def mut(pp):
            t2 = pp.get("turn")
            if t2 and _age_s(t2.get("started")) < TURN_STALE_S:
                raise TurnError(409, "conflict", "The assistant is still answering the previous message.")
            pp["turn"] = {"id": self.id, "started": self.started}
            if mode:
                pp["mode"] = mode
            for f in side:
                f(pp)
            pp["chat"].append(user_msg)
            if len(pp["chat"]) == 1 and pp.get("name_source", "auto") == "auto" and pp["name"] == "Untitled gadget":
                pp["name"] = _title_from_prompt(msg_text)
        p = await self.update(mut)
        self.mode = p.get("mode")
        return user_msg

    async def _begin_edit_part(self, p: dict, body: dict) -> str:
        comp = str(body.get("component") or "").strip()
        instr = str(body.get("instruction") or "").strip()
        if not comp or not instr:
            raise TurnError(422, "invalid", "component and instruction are required")
        if len(instr) > 2000:
            raise TurnError(422, "invalid", "That instruction is too long (2000 characters at most).")
        n = p.get("current_version")
        v = find_version(p, n)
        if not v or v.get("state") != "done":
            raise TurnError(409, "conflict", "Parts can be edited on a finished version.")
        comps = await asyncio.to_thread(self.svc.store.read_json, vkey(self.pid, n, "cad/components.json")) or {}
        if isinstance(comps, dict) and isinstance(comps.get("components"), (dict, list)):
            comps = comps["components"]
        if isinstance(comps, list):
            comps = {c.get("name"): c for c in comps if isinstance(c, dict)}
        info = comps.get(comp) if isinstance(comps, dict) else None
        if info is None and "#decor" in comp and isinstance(comps, dict):
            # REVIEW-1 MAJ-35: a decor region picked in the viewer (PRINT_Shell#decor[i]) is scoped to its own spec path
            base = comp.split("#decor", 1)[0]
            info = next((d for d in ((comps.get(base) or {}).get("decor") or [])
                         if isinstance(d, dict) and d.get("name") == comp), None)
        spec = await asyncio.to_thread(self._input_spec, n) or {}
        if info is None and not comp.startswith(("PRINT_", "PURCHASE_")):
            raise TurnError(404, "not_found", f"No part named {comp} in version {n}.")
        info = info or {}
        spec_path = info.get("spec_path") or _guess_spec_path(comp, info, spec)
        label = info.get("label") or comp.split("_", 1)[-1]
        named_all = ch.named_paths(instr, spec) if spec else set()
        named = sorted(named_all - {spec_path, "colors"})
        allowed = scope_allowed(spec_path, named_all)
        self.scope = {"component": comp, "label": label, "spec_path": spec_path, "named": named,
                      "allowed": sorted(allowed)}
        self.request_text = instr
        self.forced = "update_design"
        targets = set(named) | ({spec_path} if spec_path else set())
        self.unsupported = ch.unsupported(instr, spec, targets, await asyncio.to_thread(self.svc.bridge.decor_fields))
        where = f"`{spec_path}`" if spec_path else "no single spec path (decide the smallest change)"
        also = (f" The request names {', '.join(f'`{x}`' for x in named)}, so only "
                f"{', '.join(f'`{x}`' for x in sorted(allowed))} may change." if named else "")
        note = (" " + ch.unsupported_note(self.unsupported) + " Say this in your message and do not claim it."
                if self.unsupported else "")
        hints = ch.shape_hints(instr, spec, targets, await asyncio.to_thread(self.svc.bridge.decor_fields)) if spec else []
        if hints:
            note += " " + " ".join(hints)
        self.instruction = (f"The user selected the part {comp} ({label}), which maps to {where} in the current "
                            f"spec, and asked: \"{instr}\". Call update_design changing only that part of the spec "
                            f"(colors may change if they ask for a colour).{also} Keep everything else identical, "
                            f"including the body.{note}")
        return f"Edit {label}: {instr}"

    def _input_spec(self, n: int) -> dict | None:
        inp = self.svc.store.read_json(f"projects/{self.pid}/inputs/v{n}.json") or {}
        return inp.get("spec") or self.svc.store.read_json(vkey(self.pid, n, "spec.json"))

    async def end(self) -> None:
        rec = {"turn_id": self.id, "ts": now_iso(), "action": self.action or "text", "mode": self.mode,
               "calls": self.usage, **sum_records(self.usage)}

        def mut(pp):
            if (pp.get("turn") or {}).get("id") == self.id:
                pp["turn"] = None
            if self.usage:
                try:
                    apply_turn(pp, rec)
                except Exception:  # noqa: BLE001 - usage logging must never keep the turn locked
                    log.exception("usage logging failed")
        try:
            await self.update(mut)
        except Exception:  # noqa: BLE001
            log.exception("could not clear turn marker")
        if self.progress is not None:          # after the lock is cleared: a reader then reloads the finished project
            await self.progress.finish("error" if self.failed else "done")
        if self.usage:
            log.info("turn %s usage: %s", self.id, {k: rec[k] for k in ("input_tokens", "output_tokens",
                                                                       "thinking_tokens", "images", "cost_usd_est")})

    # ------------------------------------------------------------------ run
    async def run(self) -> None:
        self.progress = TurnProgress(self.svc.store, self.pid, self.id, started=self.started,
                                     action=self.action or "text", mode=self.mode,
                                     user_message_id=(self.user_message or {}).get("id"))
        self.progress.start()
        await self.emit("turn.start", {"turn_id": self.id, "started": self.started or now_iso(),
                                       "user_message": self.user_message})
        try:
            if self.direct == "approve_brief":
                await self.approve_brief()
            elif self.direct == "retry_build":
                await self.retry_build()
            else:
                await self.svc.brain.run(self)
                if not self.posted and self.build_n is None:
                    await self.fail("I could not finish that step. Please try again or say it another way.",
                                    code="no_result", retryable=True)
            if self.build_n is not None and self.follow_build:
                await self.status("Building")
                async for ev, data in self.svc.builds.watch(self.pid, self.build_n):
                    await self.emit(ev, data)
        except Exception as exc:  # noqa: BLE001 - report, never leave the stream hanging
            log.exception("turn failed")
            await self.fail(f"Something went wrong on my side: {type(exc).__name__}. Please try again.",
                            code="internal", retryable=True)
        finally:
            await self.end()
        p = await self.project()
        await self.emit("project", {"project": p})

    def allowed(self, p: dict) -> list[str]:
        return allowed_tools(p["stage"], max(0, 3 - int(p.get("questions_asked") or 0)),
                             bool(p.get("selected_concept")), bool(p.get("versions")), self.forced)

    # ------------------------------------------------------------------ direct actions
    async def approve_brief(self) -> None:
        p = await self.project()
        b = p["brief"]
        await self._build(b["spec"], b, label=f"Build from brief v{b['version']}", source="build", approve=True)

    async def retry_build(self) -> None:
        p = await self.project()
        n = p["current_version"]
        inp = await asyncio.to_thread(self.svc.store.read_json, f"projects/{self.pid}/inputs/v{n}.json") or {}
        if not inp.get("spec"):
            await self.fail("I could not find the design for the failed version.", code="missing_spec")
            return
        await self._build(inp["spec"], inp.get("brief"), label=f"Retry of v{n}", source="build", base=n)

    async def _build(self, spec: dict, brief: dict | None, *, label: str, source: str, base: int | None = None,
                     approve: bool = False, pre_message: str | None = None, changes: list[dict] | None = None,
                     change_text: str | None = None) -> bool:
        if approve and brief:
            brief = {**brief, "approved": True, "approved_at": now_iso(), "spec": spec}

            def mut(pp, b=brief):
                pp["brief"] = b
                for i, h in enumerate(pp.get("brief_history") or []):
                    if h.get("version") == b["version"]:
                        pp["brief_history"][i] = b
            await self.update(mut)
        extra = [make_message("assistant", "text", lint(pre_message))] if pre_message and pre_message.strip() else []
        try:
            v, msg = await self.svc.builds.start(self.pid, spec, source=source, label=label, brief=brief,
                                                 base_version=base, extra_messages=extra, changes=changes,
                                                 change_text=change_text)
        except BuildBusy as exc:
            await self.fail(f"Version {exc.n} is still building. I will be able to make changes once it finishes.",
                            code="busy")
            return False
        for m in extra:
            await self.emit("message.done", {"message": m})
        self.build_n = v["n"]
        await self.emit("build.started", {"version": v, "message": msg})
        return True

    # ------------------------------------------------------------------ tools
    async def call_tool(self, name: str, args: dict) -> ToolResult:
        p = await self.project()
        if name not in self.allowed(p):
            return ToolResult({"ok": False, "error": f"{name} is not available in stage {p['stage']}. "
                               f"Use one of: {', '.join(self.allowed(p))}."}, terminal=False)
        call_id = new_id("call", 8)
        await self.emit("tool.start", {"call_id": call_id, "name": name, "label": LABELS.get(name, name)})
        args = _plain(args or {})
        try:
            res = await getattr(self, f"tool_{name}")(args)
        except Exception as exc:  # noqa: BLE001
            log.exception("tool %s crashed", name)
            res = ToolResult({"ok": False, "error": f"tool crashed: {type(exc).__name__}"}, terminal=True)
            await self.fail("I hit a problem while working on that. Please try again.", code="tool_error")
        await self.emit("tool.done", {"call_id": call_id, "name": name, "ok": bool(res.response.get("ok")),
                                      "summary": res.response.get("summary", ""),
                                      **({"errors": res.response["errors"]} if res.response.get("errors") else {})})
        return res

    async def tool_say(self, args: dict) -> ToolResult:
        text = str(args.get("text") or "").strip()
        if text:
            await self.say_text(text)
        return ToolResult({"ok": True, "summary": "said"}, terminal=False)

    async def tool_ask_user(self, args: dict) -> ToolResult:
        p = await self.project()
        left = max(0, 3 - int(p.get("questions_asked") or 0))
        if left == 0:
            return ToolResult({"ok": False, "error": "The question limit (3) is used up. Do not ask more; continue "
                               "with sensible defaults."}, terminal=False)
        qs = []
        for i, q in enumerate(args.get("questions") or []):
            if not isinstance(q, dict) or not str(q.get("question") or "").strip():
                continue
            opts = [str(o).strip()[:60] for o in q.get("options") or [] if str(o).strip()][:4]
            qs.append({"id": str(q.get("id") or f"q{i + 1}")[:40], "question": lint(str(q["question"]))[:240],
                       "consequence": lint(str(q.get("consequence") or ""))[:240], "options": opts,
                       "allow_free_text": True if not opts else bool(q.get("allow_free_text", True))})
        if not qs:
            return ToolResult({"ok": False, "error": "no valid questions"}, terminal=False)
        if any(not q["consequence"] for q in qs):
            return ToolResult({"ok": False, "error": "every question needs its design consequence sentence"},
                              terminal=False)
        qs = qs[:left]
        await self.maybe_name(args.get("project_name"))
        defaults = [str(d).strip()[:80] for d in args.get("defaults") or [] if str(d).strip()][:5]
        lead = lint(str(args.get("message") or ""))
        text = clarify_text(lead, qs)
        msg = make_message("assistant", "choices", text, {"questions": qs, "answered": False, "defaults": defaults})

        def mut(pp):
            pp["questions_asked"] = int(pp.get("questions_asked") or 0) + len(qs)
        await self.add_message(msg, mut)
        await self.emit("choices", {"message": msg})
        await self.emit("message.done", {"message": msg})
        return ToolResult({"ok": True, "summary": f"asked {len(qs)} question(s)", "shown": len(qs)}, terminal=True)

    async def tool_propose_concepts(self, args: dict) -> ToolResult:
        items = [c for c in args.get("concepts") or [] if isinstance(c, dict)]
        if args.get("message") and not self.concept_message:
            self.concept_message = lint(str(args["message"]))
        for c in items:
            old = str(c.get("replaces") or "").strip().lower()
            if old and old != str(c.get("title") or "").strip().lower():
                self.concept_pool.pop(old, None)
        if args.get("defaults") and not self.concept_defaults:
            self.concept_defaults = [str(d).strip()[:80] for d in args["defaults"] if str(d).strip()][:5]
        await self.maybe_name(args.get("project_name"))
        await self.status("Checking the concepts against the parts catalog")
        results = await asyncio.gather(*(self.validate(c.get("spec")) for c in items))
        for k, (c, r) in enumerate(zip(items, results)):
            title = str(c.get("title") or f"Concept {len(self.concept_pool) + 1}").strip()[:60]
            key = title.lower()
            tier = c.get("tier") if c.get("tier") in TIERS else (self.concept_pool.get(key, {}).get("tier")
                                                                  or TIERS[min(len(self.concept_pool) // 2, 2)])
            self.concept_pool[key] = {"title": title, "tier": tier, "blurb": str(c.get("blurb") or "")[:300],
                                      "spec": _plain(_as_spec(c.get("spec"))), "result": r,
                                      "seq": len(self.concept_pool) + k + 100 * (
                                          self.repairs["propose_concepts"] + self.repairs["concept_rules"] + 1)}
        bad = {e["title"]: e["result"].get("errors") or ["invalid"] for e in self.concept_pool.values()
               if not e["result"].get("ok")}
        good = [e for e in self.concept_pool.values() if e["result"].get("ok")]
        infra = any(r.get("infra") for r in results)
        if bad:
            flat = [f"{t}: {e}" for t, es in bad.items() for e in es]
            await self.emit("repair", {"round": self.repairs["propose_concepts"] + 1, "errors": flat[:20]})
            r = None if infra else self._repair_or_fail(
                "propose_concepts", flat,
                "These concepts did not validate. Call propose_concepts again with ONLY the corrected concepts "
                f"({', '.join(bad)}), same titles and tiers, complete specs. The valid ones are kept.")
            if r:
                r.response["errors_by_concept"] = bad
                r.response["kept"] = [e["title"] for e in good]
                return r
        if len(good) < 2:
            r = None if infra else self._repair_or_fail(
                "propose_concepts", ["fewer than 2 valid concepts"],
                "Propose more concepts (6 in total: 2 expected, 2 different, 2 wildcard). The valid ones are kept.")
            if r and len(self.concept_pool) < MAX_CONCEPTS:
                return r
        if not good:
            await self.fail("I could not turn these ideas into shapes we can build yet. Could you tell me a bit more "
                            "about the look you want?", code="concepts_invalid")
            return ToolResult({"ok": False, "summary": "no valid concepts", "errors": list(bad)}, terminal=True)
        good = pick_set(good)
        # MAJ-28: the set keeps the user's subject, every concept has a face, and no two share a layout
        p = await self.project()
        subject = concept_rules.subject_from_text(first_request(p))
        names = await asyncio.to_thread(self.svc.bridge.module_names)
        set_fails = concept_rules.check_set(good, subject, names)
        if set_fails and not infra:
            lines = concept_rules.failure_lines(set_fails)
            fix = concept_rules.titles_to_fix(set_fails)
            # the concept rules have their own repair budget, so fit-rule repairs cannot use it up (MAJ-28)
            await self.emit("repair", {"round": self.repairs["concept_rules"] + 1, "errors": lines[:20],
                                       "kind": "concept_rules"})
            r = self._repair_or_fail(
                "concept_rules", lines,
                "The concepts are valid specs but break the concept rules. Call propose_concepts again with ONLY "
                f"these concepts redesigned ({', '.join(fix)}), same tiers, complete specs; keep the title or name the "
                "old title in `replaces`. The other concepts are kept.", limit=CONCEPT_RULE_ROUNDS)
            if r:
                r.response["set_failures"] = set_fails
                r.response["kept"] = [e["title"] for e in good if e["title"] not in fix]
                return r
            log.warning("concept set still breaks rules after repairs: %s", lines)
        await self.status(f"Rendering {len(good)} concepts")

        async def progress(d: dict) -> None:
            await self.emit("concepts.progress", d)

        rendered = await render_concepts(self.svc, self.pid, [
            {"title": e["title"], "blurb": e["blurb"], "tier": e["tier"], "spec": e["spec"],
             "warnings": e["result"].get("warnings") or []} for e in good], progress, on_usage=self.record_usage)
        message = self.concept_message or "Choose the shape you prefer from the concepts."
        if bad:
            message += f"\n\n({len(bad)} other idea{'s' if len(bad) != 1 else ''} could not be made buildable, " \
                       "so they are not shown.)"
        tiers = {t: [c["id"] for c in rendered if c.get("tier") == t] for t in TIERS}
        msg = make_message("assistant", "concepts", message,
                           {"concept_ids": [c["id"] for c in rendered], "tiers": tiers, "selected": None,
                            "defaults": self.concept_defaults, "subject": (subject or {}).get("noun"),
                            "set_check": {"ok": not set_fails, "failures": set_fails,
                                          "rule_rounds": self.repairs["concept_rules"],
                                          "repair_rounds": self.repairs["propose_concepts"]}})

        def mut(pp):
            pp["concepts"] = (pp.get("concepts") or []) + rendered
            pp["selected_concept"] = None
            if not pp.get("versions"):
                pp["stage"] = "concepts"
        await self.add_message(msg, mut)
        await self.emit("concepts", {"message": msg, "concepts": rendered})
        await self.emit("message.done", {"message": msg})
        return ToolResult({"ok": True, "summary": f"{len(rendered)} concepts",
                           "concepts": [{"id": c["id"], "title": c["title"], "state": c["state"]} for c in rendered]},
                          terminal=True)

    async def tool_write_brief(self, args: dict) -> ToolResult:
        p = await self.project()
        sel = find_concept(p, p.get("selected_concept"))
        prev = p.get("brief")
        if args.get("spec"):
            spec = _as_spec(args["spec"])
        elif prev and sel and prev.get("concept_id") == sel["id"]:
            spec = prev["spec"]
        elif sel:
            spec = sel["spec"]
        elif prev:
            spec = prev["spec"]
        else:
            return ToolResult({"ok": False, "error": "No concept is selected; pass the spec."}, terminal=False)
        r = await self.validate(spec)
        if not r.get("ok"):
            rr = self._repair_or_fail("write_brief", r.get("errors") or ["invalid spec"])
            if rr:
                await self.emit("repair", {"round": self.repairs["write_brief"], "errors": r.get("errors")})
                return rr
            await self.fail("I could not fit those changes into a buildable design. The concept as shown can still "
                            "be built; tell me what matters most and I will try again.", code="brief_invalid")
            return ToolResult({"ok": False, "summary": "invalid spec"}, terminal=True)
        names = await asyncio.to_thread(self.svc.bridge.module_names)
        spec = _plain(spec)
        version = len(p.get("brief_history") or []) + 1
        measured = None
        cid = sel["id"] if sel else (prev or {}).get("concept_id")
        csel = find_concept(p, cid)
        if csel and same_geometry(csel.get("spec"), spec):
            glb = await asyncio.to_thread(self.svc.store.read_bytes, f"projects/{self.pid}/concepts/{cid}/preview.glb")
            measured = glb_bbox_mm(glb)
        src = "the CAD preview of this concept" if measured else None
        b = assemble_brief(args.get("brief") or {}, spec, cid, version, names, texts=user_texts(p),
                           subject=concept_rules.subject_from_text(first_request(p)), measured=measured,
                           measured_from=src)
        if r.get("normalized"):
            parts = parts_from_spec(r["normalized"], names)
            if len(parts) >= len(b["parts"]):
                for x in parts:
                    x.setdefault("source", "suggested")
                    x.setdefault("evidence", "Added automatically for the design" if x.get("implied")
                                 else "From the parts catalog")
                b["parts"] = parts
            b["size"] = size_from_spec(r["normalized"], measured, src)
        text = lint(str(args.get("message") or "")) or \
            f"{b['title']}'s brief is ready for review. Choose **Build this** to proceed."
        msg = make_message("assistant", "brief", text, {"brief_version": version})

        def mut(pp):
            pp["brief"] = b
            pp["brief_history"] = (pp.get("brief_history") or []) + [b]
            if not pp.get("versions") or pp["stage"] in ("concepts", "clarify", "brief") or self.forced == "write_brief":
                pp["stage"] = "brief"
        await self.add_message(msg, mut)
        await self.emit("brief", {"message": msg, "brief": b})
        await self.emit("message.done", {"message": msg})
        return ToolResult({"ok": True, "summary": f"brief v{version}"}, terminal=True)

    async def tool_build_design(self, args: dict) -> ToolResult:
        p = await self.project()
        b = p.get("brief")
        spec = _as_spec(args.get("spec")) if args.get("spec") else (b or {}).get("spec")
        if not spec:
            return ToolResult({"ok": False, "error": "no spec to build"}, terminal=False)
        r = await self.validate(spec)
        if not r.get("ok"):
            rr = self._repair_or_fail("build_design", r.get("errors") or ["invalid spec"])
            if rr:
                await self.emit("repair", {"round": self.repairs["build_design"], "errors": r.get("errors")})
                return rr
            await self.fail("The design did not pass the checks, so I did not start a build.", code="build_invalid")
            return ToolResult({"ok": False, "summary": "invalid spec"}, terminal=True)
        label = f"Build from brief v{b['version']}" if b else "First build"
        ok = await self._build(_plain(spec), b, label=label, source="build", approve=bool(b),
                               pre_message=args.get("message"))
        return ToolResult({"ok": ok, "summary": f"version {self.build_n}" if ok else "busy"}, terminal=True)

    async def tool_update_design(self, args: dict) -> ToolResult:
        p = await self.project()
        spec = _as_spec(args.get("spec"))
        r = await self.validate(spec)
        if not r.get("ok"):
            rr = self._repair_or_fail("update_design", r.get("errors") or ["invalid spec"])
            if rr:
                await self.emit("repair", {"round": self.repairs["update_design"], "errors": r.get("errors")})
                return rr
            await self.fail("I could not make that change fit the design rules, so the current version is "
                            "unchanged. " + "; ".join((r.get("errors") or [])[:2]), code="update_invalid")
            return ToolResult({"ok": False, "summary": "invalid spec"}, terminal=True)
        spec = _plain(spec)
        summary = str(args.get("change_summary") or "Edit").strip()[:80]
        raw_base = await asyncio.to_thread(self._input_spec, p.get("current_version")) or {}
        base = await asyncio.to_thread(self.svc.bridge.with_defaults, raw_base)
        full = await asyncio.to_thread(self.svc.bridge.with_defaults, spec)
        allowed: set[str] | None = None
        if self.scope and self.scope.get("spec_path"):
            allowed = set(self.scope.get("allowed") or {self.scope["spec_path"], "colors"})
            outside = [x for x in ch.changed_paths(base, full) if not ch.in_scope(x, allowed)]
            if outside and not self.scope_warned:
                self.scope_warned = True
                return ToolResult({"ok": False, "errors": [f"changed outside the selected part: {', '.join(outside[:8])}"],
                                   "instruction": f"Only {', '.join(sorted(allowed))} may change for this part edit. "
                                                  "Call update_design again with every other value exactly as in "
                                                  "the current spec."}, terminal=False)
            if outside:
                # second scope violation: stop and ask instead of building something nobody asked for
                items = [i for i in ch.describe(base, full, allowed) if not i["requested"]]
                await self.say_text(
                    f"To change the {self.scope['label']} that way I would also change "
                    f"{', '.join(i['text'] for i in items[:4]) or ', '.join(outside[:4])}, which you did not ask "
                    "for. Should I change that too? Say yes to include it, or tell me what to keep as it is.")
                return ToolResult({"ok": False, "summary": "asked before changing parts outside the selection"},
                                  terminal=True)
        elif self.request_text:
            allowed = ch.named_paths(self.request_text, base) or None
            if allowed and not self.unsupported:
                self.unsupported = ch.unsupported(self.request_text, base, allowed,
                                                  await asyncio.to_thread(self.svc.bridge.decor_fields))
        items = ch.describe(base, full, allowed)
        if not items:
            await self.say_text("That request would not change the design, so I did not make a new version. "
                                + (ch.unsupported_note(self.unsupported) if self.unsupported else ""))
            return ToolResult({"ok": False, "summary": "no change"}, terminal=True)
        what = ch.changes_text(items)
        message = lint(str(args.get("message") or ""))
        if self.unsupported and (not message or ch.claims(message, self.unsupported)):
            message = f"{ch.unsupported_note(self.unsupported)} I changed: {what}."
        elif message:
            message = f"{message}\n\nChanged: {what}."
        name = self.scope["label"] if self.scope else None
        label = f"Edit {name}: {what}" if name else f"Edit: {what}"
        ok = await self._build(spec, p.get("brief"), label=label, source="update",
                               base=p.get("current_version"), pre_message=message, changes=items,
                               change_text=f"{summary} ({what})" if summary and summary != "Edit" else what)
        return ToolResult({"ok": ok, "summary": f"version {self.build_n}" if ok else "busy",
                           "changes": [i["text"] for i in items]}, terminal=True)


# ---------------------------------------------------------------------- small pure helpers
def _age_s(iso: str | None) -> float:
    from datetime import datetime, timezone

    t = parse_iso(iso)
    if not t:
        return 1e9
    return (datetime.now(timezone.utc) - t).total_seconds()


def _mark_answered(p: dict, mid: str, extra: dict) -> None:
    for m in p["chat"]:
        if m["id"] == mid:
            m["payload"] = {**(m.get("payload") or {}), "answered": True, **extra}


def _select(p: dict, cid: str) -> None:
    """Picking a concept (also after a build, MIN-43) leads to a new brief and, once approved, a new version."""
    p["selected_concept"] = cid
    for m in p["chat"]:
        if m["kind"] == "concepts" and cid in (m.get("payload") or {}).get("concept_ids", []):
            m["payload"]["selected"] = cid
    p["stage"] = "brief"


def _default_concept(p: dict) -> dict | None:
    """'Continue without an image': the first ready Expected concept of the latest set."""
    m = last_message(p, "concepts")
    ids = (m.get("payload") or {}).get("concept_ids", []) if m else []
    cs = [c for c in (find_concept(p, i) for i in ids) if c and c.get("state") == "ready"]
    exp = [c for c in cs if c.get("tier") == "expected"]
    return (exp or cs or [None])[0]


def _title_from_prompt(text: str) -> str:
    words = text.strip().split()
    t = " ".join(words[:6])
    return (t[:60] + ("..." if len(words) > 6 else "")) or "Untitled gadget"


def _guess_spec_path(comp: str, info: dict, spec: dict) -> str | None:
    fid = info.get("feature_id")
    feats = spec.get("features") or []
    if fid:
        for i, f in enumerate(feats):
            if f.get("id") == fid:
                return f"features[{i}]"
    if comp in ("PRINT_Shell", "PRINT_Body") or comp.endswith("Shell"):
        return "body"
    return None


def changed_paths(a: Any, b: Any, path: str = "") -> list[str]:
    return ch.changed_paths(a, b, path)


def out_of_scope(base: dict, new: dict, spec_path: str, named: list[str] | None = None) -> list[str]:
    """Changed paths outside the selected part, colours and whatever else the user named in the request. A body
    selection no longer lets decor or feature positions change unless the request names them (MAJ-34)."""
    allowed = {spec_path, "colors", *(named or [])}
    return [p for p in ch.changed_paths(base, new) if not ch.in_scope(p, allowed)]


def pick_set(good: list[dict], per_tier: int = 2) -> list[dict]:
    """Up to 2 concepts per tier, the newest first (a repaired or renamed concept replaces an older one), then any
    remaining newest to fill 6, in tier order."""
    newest = sorted(good, key=lambda e: -int(e.get("seq") or 0))
    chosen: list[dict] = []
    for t in TIERS:
        chosen += [e for e in newest if e["tier"] == t][:per_tier]
    for e in newest:
        if len(chosen) >= MAX_CONCEPTS:
            break
        if e not in chosen:
            chosen.append(e)
    chosen = chosen[:MAX_CONCEPTS]
    return sorted(chosen, key=lambda e: (TIERS.index(e["tier"]), int(e.get("seq") or 0)))


def scope_allowed(spec_path: str | None, named: set[str]) -> set[str]:
    """What a part edit may change: the selected path, colours and the parts the request names. A shell click whose
    request names other parts ('make the ears rounder') does not license body changes unless the body is named."""
    allowed = {"colors", *named}
    if spec_path and not (spec_path == "body" and (named - {"colors"}) and "body" not in named):
        allowed.add(spec_path)
    return allowed


def clarify_text(lead: str, qs: list[dict]) -> str:
    """MIN-42: each consequence sits directly under its own question, never joined into one paragraph."""
    blocks = [lead] if lead else []
    for q in qs:
        blocks.append(f"**{q['question']}**\n{q['consequence']}" if q.get("consequence") else f"**{q['question']}**")
    return "\n\n".join(blocks)


def first_request(p: dict) -> str:
    """The user's first typed message: the request the subject comes from."""
    for m in p.get("chat") or []:
        if m.get("role") == "user" and m.get("kind") == "text" and not (m.get("payload") or {}).get("action"):
            return str(m.get("text") or "")
    return ""
