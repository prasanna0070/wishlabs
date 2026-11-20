"""Progress of the running chat turn, kept in its own object: projects/<pid>/turn-progress.json.

Only the browser tab that sent a message holds the turn's SSE stream. A reload or a dropped connection (which can land
on another Cloud Run instance) reads this compact record instead: turn id, start time, the current step, a short step
list with timestamps, and the latest thought summaries (the last ~3 KB). It is deliberately NOT part of project.json:
the turn already writes that object, and extra writes there would race with chat and concept updates.

Writes are throttled (at most one per MIN_INTERVAL_S, plus a heartbeat every HEARTBEAT_S so a reader can tell the turn
is alive), run off the event loop, and never fail the turn. The record is marked done or error when the turn ends.
It is read only by GET /api/projects/{pid}/turn (signed-in); share links never see it.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import re
import time
from datetime import datetime, timezone

from .util import now_iso, parse_iso

log = logging.getLogger("gstudio.progress")

SCHEMA = "gstudio-turn-progress/1"
TURN_STALE_S = 15 * 60          # the turn lock in project.json blocks a new turn this long (agent/turn.py)
MIN_INTERVAL_S = 2.0            # at most one write per this many seconds while the turn runs
HEARTBEAT_S = 20.0              # rewrite at least this often, so `updated` shows the turn is alive
MAX_THOUGHTS = 3000             # characters of thought summaries kept (the newest)
MAX_STEPS = 24
MAX_LABEL = 160
MAX_DETAIL = 240
CONCEPT_DONE = ("ready", "failed")


def progress_key(pid: str) -> str:
    return f"projects/{pid}/turn-progress.json"


def seconds_since(iso: str | None) -> float:
    t = parse_iso(iso)
    if not t:
        return 1e9
    return (datetime.now(timezone.utc) - t).total_seconds()


def _clip(text, n: int) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def append_thought(buf: str, text: str, limit: int = MAX_THOUGHTS) -> str:
    """Add one streamed thought summary, keeping only the newest `limit` characters, cut at a paragraph start so a
    bold heading is never shown half."""
    text = str(text or "")
    if not text.strip():
        return buf
    if buf and not buf.endswith("\n") and text.lstrip().startswith("**"):
        buf += "\n\n"
    buf += text
    if len(buf) > limit:
        tail = buf[-limit:]
        cut = tail.find("\n\n**")                      # prefer a paragraph that starts with its heading
        if not 0 <= cut < limit // 2:
            cut = tail.find("\n\n")
        buf = tail[cut + 2:] if 0 <= cut < limit // 2 else tail
    return buf


def concept_step_label(states: dict, total: int = 0) -> str:
    """Concepts render in parallel: one counter step, not a line per concept (the SPA words it the same way)."""
    vals = list(states.values())
    n = max(total, len(vals))
    done = sum(1 for s in vals if s in CONCEPT_DONE)
    failed = sum(1 for s in vals if s == "failed")
    return f"Rendering concept photos: {done} of {n} done" + (f", {failed} failed" if failed else "")


def repair_reason(data: dict) -> str:
    errs = [str(e) for e in data.get("errors") or [] if str(e).strip()]
    if not errs:
        return ""
    more = f" (+{len(errs) - 1} more)" if len(errs) > 1 else ""
    return _clip(errs[0], MAX_DETAIL - len(more)) + more


class TurnProgress:
    """Folds the turn's SSE events into the record and writes it in the background, throttled."""

    def __init__(self, store, pid: str, turn_id: str, *, started: str | None = None, action: str | None = None,
                 mode: str | None = None, user_message_id: str | None = None,
                 min_interval: float = MIN_INTERVAL_S, heartbeat: float = HEARTBEAT_S):
        self.store, self.pid = store, pid
        self.key = progress_key(pid)
        self.min_interval, self.heartbeat = min_interval, heartbeat
        t = started or now_iso()
        self.rec: dict = {"schema": SCHEMA, "turn_id": turn_id, "state": "running", "started": t, "updated": None,
                          "ended": None, "action": action, "mode": mode, "user_message_id": user_message_id,
                          "step": "Reading your message", "steps": [{"label": "Reading your message", "ts": t}],
                          "thoughts": "", "error": None}
        self.writes = 0
        self._concepts: dict[str, str] = {}
        self._concept_total = 0
        self._wake = asyncio.Event()
        self._closing = asyncio.Event()
        self._closed = False
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------ record edits (sync, cheap)
    def step(self, label, detail: str | None = None) -> None:
        label = _clip(label, MAX_LABEL)
        if not label:
            return
        steps = self.rec["steps"]
        # the same step again, or its plain form right after it ("Fixing the design" after "... (round 1)")
        if steps and not detail and (steps[-1]["label"] == label or steps[-1]["label"].startswith(label + " (")):
            return
        s = {"label": label, "ts": now_iso()}
        if detail:
            s["detail"] = _clip(detail, MAX_DETAIL)
        steps.append(s)
        if len(steps) > MAX_STEPS:
            del steps[1:len(steps) - MAX_STEPS + 1]     # keep the first step (when the turn began) and the newest
        self.rec["step"] = label
        self._touch()

    def _relabel(self, label: str) -> None:
        """Update the current step's label in place (counters such as 'k of n' must not add a step each)."""
        steps = self.rec["steps"]
        if steps and steps[-1]["label"].startswith("Rendering"):
            steps[-1]["label"] = label
            self.rec["step"] = label
            self._touch()
        else:
            self.step(label)

    def thought(self, text: str) -> None:
        new = append_thought(self.rec["thoughts"], text)
        if new != self.rec["thoughts"]:
            self.rec["thoughts"] = new
            self._touch()

    def observe(self, event: str, data: dict) -> None:
        """Fold one turn event into the record. Never raises: progress must not break a turn."""
        try:
            self._observe(event, data or {})
        except Exception:  # noqa: BLE001
            log.exception("progress: could not record %s", event)

    def _observe(self, event: str, d: dict) -> None:
        if event == "status":
            m = re.fullmatch(r"Rendering (\d+) concepts?", str(d.get("text") or ""))
            if m:
                self._concepts, self._concept_total = {}, int(m.group(1))
                self.step(concept_step_label({}, self._concept_total))
            else:
                self.step(d.get("text"))
        elif event == "tool.start":
            self.step(d.get("label") or d.get("name"))
        elif event == "repair":
            kind = "Fixing the concept rules" if d.get("kind") == "concept_rules" else "Fixing the design"
            self.step(f"{kind} (round {d.get('round') or 1})", repair_reason(d))
        elif event == "concepts.progress":
            cid = str(d.get("concept_id") or d.get("index"))
            self._concepts[cid] = str(d.get("state") or "")
            self._relabel(concept_step_label(self._concepts, self._concept_total))
        elif event == "thinking.delta":
            self.thought(d.get("text"))
        elif event == "build.started":
            n = (d.get("version") or {}).get("n")
            self.step(f"Starting the build of v{n}" if n else "Starting the build")
        elif event == "error":
            self.rec["error"] = _clip(d.get("message"), MAX_DETAIL)
            self._touch()

    # ------------------------------------------------------------------ writing (background, throttled)
    def start(self) -> None:
        """Write the first record now and keep it fresh until finish()."""
        if self._task is None:
            self._wake.set()
            self._task = asyncio.create_task(self._run())

    def _touch(self) -> None:
        if not self._closed:
            self._wake.set()

    async def _run(self) -> None:
        last = None
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.heartbeat)
            except asyncio.TimeoutError:
                pass                                    # heartbeat: nothing new, rewrite `updated`
            if self._closed:
                return
            if last is not None:
                gap = self.min_interval - (time.monotonic() - last)
                if gap > 0:
                    try:
                        await asyncio.wait_for(self._closing.wait(), timeout=gap)
                        return                          # finish() writes the final record itself
                    except asyncio.TimeoutError:
                        pass
            self._wake.clear()
            await self._write()
            last = time.monotonic()

    async def _write(self) -> None:
        self.rec["updated"] = now_iso()
        data = copy.deepcopy(self.rec)                  # snapshot on the loop thread; the write runs in a worker
        try:
            await asyncio.to_thread(self.store.write_json, self.key, data)
            self.writes += 1
        except Exception as exc:  # noqa: BLE001 - progress is best effort
            log.warning("progress write failed for %s: %s", self.pid, str(exc)[:200])

    async def finish(self, state: str = "done") -> None:
        """Mark the record done (or error) and write it once more. Waits for a write already in flight, so the final
        record is always the last one written by this turn."""
        if self._closed:
            return
        self._closed = True
        self.rec["state"] = state if state in ("done", "error") else "done"
        self.rec["ended"] = now_iso()
        self._closing.set()
        self._wake.set()
        if self._task is not None:
            try:
                await self._task
            except Exception:  # noqa: BLE001
                log.exception("progress writer failed")
        await self._write()


def turn_view(lock: dict | None, rec: dict | None, stale_s: float = TURN_STALE_S) -> dict:
    """GET /api/projects/{pid}/turn: whether a turn runs (the lock in project.json) and what it is doing (the record).
    A lock older than stale_s is reported as stale: the turn stopped unexpectedly and a new message may be sent."""
    lock = lock if isinstance(lock, dict) and lock.get("id") else None
    rec = rec if isinstance(rec, dict) and rec.get("schema") == SCHEMA else None
    out = {"running": False, "stale": False, "state": "idle", "turn_id": None, "started": None, "age_s": None,
           "step": None, "steps": [], "thoughts": "", "user_message_id": None, "action": None, "mode": None,
           "updated": None, "quiet_s": None, "error": None}

    def from_rec(r: dict) -> None:
        out.update(step=r.get("step"), steps=list(r.get("steps") or [])[-MAX_STEPS:],
                   thoughts=str(r.get("thoughts") or "")[-MAX_THOUGHTS:], user_message_id=r.get("user_message_id"),
                   action=r.get("action"), mode=r.get("mode"), updated=r.get("updated"), error=r.get("error"))
        if r.get("updated"):
            out["quiet_s"] = round(seconds_since(r["updated"]), 1)

    if lock:
        age = seconds_since(lock.get("started"))
        stale = age >= stale_s
        out.update(running=not stale, stale=stale, state="stale" if stale else "running", turn_id=lock["id"],
                   started=lock.get("started"), age_s=round(min(age, 1e7), 1))
        if rec and rec.get("turn_id") == lock["id"]:
            from_rec(rec)
        else:                                           # the lock is newer than the record: the turn is just starting
            out.update(step="Reading your message",
                       steps=[{"label": "Reading your message", "ts": lock.get("started")}])
    elif rec:                                           # nothing running: the last turn's record, for reference
        from_rec(rec)
        out.update(state=rec.get("state") if rec.get("state") in ("done", "error") else "ended",
                   turn_id=rec.get("turn_id"), started=rec.get("started"))
    return out
