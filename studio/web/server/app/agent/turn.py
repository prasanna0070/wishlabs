"""One chat turn: classify the request, store the user message, run the brain (Gemini or scripted) or a direct
action, execute tool calls with validation + repair rounds, and emit SSE events. Used by both brains."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import defaultdict
from typing import Any, Awaitable, Callable

from ..brief import assemble_brief, parts_from_spec, size_from_spec
from ..builds import BuildBusy
from ..concepts import render_concepts
from ..projects import find_concept, find_version, last_message, make_message, vkey
from ..usage import apply_turn, sum_records
from ..util import new_id, now_iso, parse_iso
from .tools import LABELS, TIERS, allowed_tools

log = logging.getLogger("gstudio.turn")

Emit = Callable[[str, dict], Awaitable[None]]
TURN_STALE_S = 15 * 60
MAX_CONCEPTS = 6


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
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, float) and x.is_integer() and abs(x) < 1e12:
        return int(x)
    return x


class Turn:
    def __init__(self, svc, pid: str, emit: Emit):
        self.svc = svc
        self.pid = pid
        self.emit = emit
        self.id = new_id("t")
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
        self.usage: list[dict] = []
        self.concept_pool: dict[str, dict] = {}  # title.lower() -> {item, result}
        self.concept_message = ""
        self.concept_defaults: list[str] = []
        self.posted = False

    # ------------------------------------------------------------------ helpers
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
        msg = make_message("assistant", "error", text, {"code": code, "retryable": retryable})
        await self.add_message(msg)
        await self.emit("message.done", {"message": msg})
        await self.emit("error", {"code": code, "message": text, "retryable": retryable})

    async def say_text(self, text: str) -> dict:
        msg = make_message("assistant", "text", text.strip())
        await self.add_message(msg)
        await self.emit("message.done", {"message": msg})
        return msg

    async def maybe_name(self, name: Any) -> None:
        """The agent names the project once (unless the user renamed it)."""
        name = re.sub(r"\s+", " ", str(name or "")).strip().strip('"')[:60]
        if not name:
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

    def _repair_or_fail(self, tool: str, errors: list[str], instruction: str | None = None) -> ToolResult | None:
        """None when out of repair rounds (caller reports failure to the user)."""
        if self.repairs[tool] >= self.svc.settings.max_repair_rounds:
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
            ch = last_message(p, "choices")
            if ch and not (ch.get("payload") or {}).get("answered"):
                side.append(lambda pp, mid=ch["id"]: _mark_answered(pp, mid, {"free_text": text}))
            if p["stage"] == "building":
                self.instruction = "A build is running. Answer briefly; do not start another build."
        elif action == "answers":
            ch = last_message(p, "choices")
            if not ch:
                raise TurnError(422, "invalid", "There is no question to answer.")
            qs = {q["id"]: q for q in (ch.get("payload") or {}).get("questions") or []}
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
            side.append(lambda pp, mid=ch["id"], ans=answers, sk=skipped:
                        _mark_answered(pp, mid, {"answers": ans, "skipped": sk}))
        elif action in ("select_concept", "skip_concepts"):
            if p["stage"] not in ("concepts", "brief", "clarify"):
                raise TurnError(409, "conflict", "Concepts can be picked before the first build only.")
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
        elif action == "regenerate_concepts":
            if p["stage"] not in ("clarify", "concepts", "brief"):
                raise TurnError(409, "conflict", "New concepts are only offered before the first build.")
            msg_text = "Show me different concepts"
            self.instruction = "Propose new concepts that are clearly different from the earlier ones."
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

        def mut(pp):
            t2 = pp.get("turn")
            if t2 and _age_s(t2.get("started")) < TURN_STALE_S:
                raise TurnError(409, "conflict", "The assistant is still answering the previous message.")
            pp["turn"] = {"id": self.id, "started": now_iso()}
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
        spec = await asyncio.to_thread(self._input_spec, n) or {}
        if info is None and not comp.startswith(("PRINT_", "PURCHASE_")):
            raise TurnError(404, "not_found", f"No part named {comp} in version {n}.")
        info = info or {}
        spec_path = info.get("spec_path") or _guess_spec_path(comp, info, spec)
        label = info.get("label") or comp.split("_", 1)[-1]
        self.scope = {"component": comp, "label": label, "spec_path": spec_path}
        self.forced = "update_design"
        where = f"`{spec_path}`" if spec_path else "no single spec path (decide the smallest change)"
        self.instruction = (f"The user selected the part {comp} ({label}), which maps to {where} in the current "
                            f"spec, and asked: \"{instr}\". Call update_design changing only that part of the spec "
                            "(colors may change if they ask for a colour). Keep everything else identical.")
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
        if self.usage:
            log.info("turn %s usage: %s", self.id, {k: rec[k] for k in ("input_tokens", "output_tokens",
                                                                       "thinking_tokens", "images", "cost_usd_est")})

    # ------------------------------------------------------------------ run
    async def run(self) -> None:
        await self.emit("turn.start", {"turn_id": self.id, "started": now_iso(), "user_message": self.user_message})
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
                     approve: bool = False, pre_message: str | None = None) -> bool:
        if approve and brief:
            brief = {**brief, "approved": True, "approved_at": now_iso(), "spec": spec}

            def mut(pp, b=brief):
                pp["brief"] = b
                for i, h in enumerate(pp.get("brief_history") or []):
                    if h.get("version") == b["version"]:
                        pp["brief_history"][i] = b
            await self.update(mut)
        extra = [make_message("assistant", "text", pre_message.strip())] if pre_message and pre_message.strip() else []
        try:
            v, msg = await self.svc.builds.start(self.pid, spec, source=source, label=label, brief=brief,
                                                 base_version=base, extra_messages=extra)
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
            qs.append({"id": str(q.get("id") or f"q{i + 1}")[:40], "question": str(q["question"]).strip()[:240],
                       "consequence": str(q.get("consequence") or "").strip()[:240], "options": opts,
                       "allow_free_text": True if not opts else bool(q.get("allow_free_text", True))})
        if not qs:
            return ToolResult({"ok": False, "error": "no valid questions"}, terminal=False)
        if any(not q["consequence"] for q in qs):
            return ToolResult({"ok": False, "error": "every question needs its design consequence sentence"},
                              terminal=False)
        qs = qs[:left]
        await self.maybe_name(args.get("project_name"))
        defaults = [str(d).strip()[:80] for d in args.get("defaults") or [] if str(d).strip()][:5]
        lead = str(args.get("message") or "").strip()
        text = " ".join(q["consequence"] for q in qs)
        if lead:
            text = lead + "\n\n" + text
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
            self.concept_message = str(args["message"]).strip()
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
                                      "spec": _plain(_as_spec(c.get("spec"))), "result": r}
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
        good = sorted(good, key=lambda e: TIERS.index(e["tier"]))[:MAX_CONCEPTS]
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
                            "defaults": self.concept_defaults})

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
        b = assemble_brief(args.get("brief") or {}, spec, sel["id"] if sel else (prev or {}).get("concept_id"),
                           version, names)
        if r.get("normalized"):
            parts = parts_from_spec(r["normalized"], names)
            if len(parts) >= len(b["parts"]):
                for x in parts:
                    x.setdefault("source", "suggested")
                    x.setdefault("evidence", "Added automatically for the design" if x.get("implied")
                                 else "From the parts catalog")
                b["parts"] = parts
            b["size"] = size_from_spec(r["normalized"])
        text = str(args.get("message") or "").strip() or \
            f"{b['title']}'s brief is ready for review. Choose **Build this** to proceed."
        msg = make_message("assistant", "brief", text, {"brief_version": version})

        def mut(pp):
            pp["brief"] = b
            pp["brief_history"] = (pp.get("brief_history") or []) + [b]
            if not pp.get("versions") or pp["stage"] in ("concepts", "clarify", "brief"):
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
        if self.scope and self.scope.get("spec_path"):
            base = await asyncio.to_thread(self._input_spec, p.get("current_version")) or {}
            outside = out_of_scope(base, spec, self.scope["spec_path"])
            if outside and not self.scope_warned:
                self.scope_warned = True
                return ToolResult({"ok": False, "errors": [f"changed outside the selected part: {', '.join(outside[:8])}"],
                                   "instruction": f"Only {self.scope['spec_path']} (and colors) may change for this "
                                                  "part edit. Call update_design again with every other value "
                                                  "exactly as in the current spec, unless a change elsewhere is "
                                                  "truly required for the part to fit."}, terminal=False)
        label = f"Edit {self.scope['label']}: {summary}" if self.scope else f"Edit: {summary}"
        ok = await self._build(spec, p.get("brief"), label=label, source="update",
                               base=p.get("current_version"), pre_message=args.get("message"))
        return ToolResult({"ok": ok, "summary": f"version {self.build_n}" if ok else "busy"}, terminal=True)


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
    p["selected_concept"] = cid
    for m in p["chat"]:
        if m["kind"] == "concepts" and cid in (m.get("payload") or {}).get("concept_ids", []):
            m["payload"]["selected"] = cid
    if not p.get("versions"):
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
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k == "derived":
                continue
            p = f"{path}.{k}" if path else k
            if k not in a or k not in b:
                out.append(p)
            else:
                out += changed_paths(a[k], b[k], p)
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b) and all(isinstance(x, dict) for x in a + b):
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += changed_paths(x, y, f"{path}[{i}]")
        return out
    return [] if a == b else [path]


def out_of_scope(base: dict, new: dict, spec_path: str) -> list[str]:
    allowed = [spec_path, "colors"]
    extra_re = re.compile(r"^(decor(\[\d+\])?(\..*)?|features\[\d+\]\.at)$") if spec_path == "body" else None

    def ok(p: str) -> bool:
        if any(p == a or p.startswith(a + ".") or p.startswith(a + "[") for a in allowed):
            return True
        return bool(extra_re and extra_re.match(p))

    return [p for p in changed_paths(base, new) if not ok(p)]
