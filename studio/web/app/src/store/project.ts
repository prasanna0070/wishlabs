// The open project: snapshot from the API plus live state from the SSE streams (API.md §3).
import { create } from "zustand";
import { api, ApiError, clearFileCache } from "../lib/api";
import { openEventStream, postStream, type SSEEvent } from "../lib/sse";
import type { ChatMessage, Mode, Project, Status, Version } from "../lib/types";
import type { Ctx } from "../lib/urls";
import { appendThought, conceptStepLabel, CONFLICT_TEXT, POLL_MS, remoteTurn, renderingTotal, retryBody, TURN_STALE_S, watchDecision, type TurnState, type TurnStep } from "../lib/working";

export type { TurnState, TurnStep } from "../lib/working";

export interface ConceptProgress {
  index: number;
  title: string;
  state: string;
  note?: string;
}

interface ProjectState {
  ctx: Ctx | null;
  project: Project | null;
  loading: boolean;
  error: string | null;
  readOnly: boolean;
  turn: TurnState | null;
  turns: Record<string, TurnState>; // finished turns keyed by user message id (session only)
  conceptProgress: Record<string, ConceptProgress>;
  statuses: Record<number, Status>;
  saving: number;
  saveError: string | null;
  sendError: string | null;
  sendErrorCode: string | null; // "conflict" (409: a turn is still running) | "network" | an API error code
  streamOpen: boolean;

  load: (pid: string) => Promise<void>;
  loadShare: (token: string) => Promise<void>;
  reset: () => void;
  setProject: (p: Project) => void;
  send: (body: Record<string, any>) => Promise<void>;
  /** Follow a turn that runs on the server but not on this tab's stream (reload, dropped stream, 409). */
  watchTurn: () => void;
  /** Send the stopped turn's user message again (the stale card's Retry). */
  retryLast: () => Promise<void>;
  followBuild: () => void;
  stopFollow: () => void;
  rename: (name: string) => Promise<void>;
  setMode: (m: Mode) => Promise<void>;
  undo: () => Promise<void>;
  redo: () => Promise<void>;
  setCurrent: (n: number) => Promise<void>;
  restore: (n: number) => Promise<void>;
  setChecked: (track: "assembly" | "wiring", stepId: string, v: boolean) => Promise<void>;
  afterTweak: (v: Version) => void;
  refresh: () => Promise<void>;
}

let closeStream: (() => void) | null = null;
let shareTimer: number | null = null;
// the server-turn watcher (GET /turn every ~2 s while a turn runs that this tab is not streaming)
let turnTimer: number | null = null;
let turnWatch: { pid: string; fails: number } | null = null;

function stopTurnWatch() {
  if (turnTimer) window.clearTimeout(turnTimer);
  turnTimer = null;
  turnWatch = null;
}

function upsert(chat: ChatMessage[], m: ChatMessage): ChatMessage[] {
  const i = chat.findIndex((x) => x.id === m.id);
  if (i < 0) return [...chat, m];
  const out = chat.slice();
  out[i] = { ...m, draft: false };
  return out;
}

function upsertVersion(vs: Version[], v: Version): Version[] {
  const i = vs.findIndex((x) => x.n === v.n);
  if (i < 0) return [...vs, v].sort((a, b) => a.n - b.n);
  const out = vs.slice();
  out[i] = { ...out[i], ...v };
  return out;
}

export function isBuilding(p: Project | null): boolean {
  return !!p?.versions?.some((v) => v.state === "queued" || v.state === "running");
}

export const useProject = create<ProjectState>((set, get) => {
  const patchProject = (fn: (p: Project) => Project) => {
    const p = get().project;
    if (p) set({ project: fn(p) });
  };

  const handle = (ev: SSEEvent) => {
    const d = ev.data || {};
    const turn = get().turn;
    const step = (id: string, label: string, state: TurnStep["state"] = "running") => {
      const t = get().turn;
      if (!t) return;
      const steps = t.steps.map((s) => (s.state === "running" && s.id !== id ? { ...s, state: "done" as const } : s));
      const i = steps.findIndex((s) => s.id === id);
      if (i >= 0) steps[i] = { ...steps[i], label: label || steps[i].label, state };
      else steps.push({ id, label, state });
      set({ turn: { ...t, steps, status: state === "running" ? label : t.status } });
    };
    switch (ev.event) {
      case "turn.start": {
        if (d.user_message) patchProject((p) => ({ ...p, chat: upsert(p.chat, d.user_message) }));
        const started = d.started ? Date.parse(d.started) : NaN;
        if (turn) set({ turn: { ...turn, turnId: d.turn_id, userMsgId: d.user_message?.id, startedAt: Number.isNaN(started) ? turn.startedAt : Math.min(started, Date.now()) } });
        break;
      }
      case "project.renamed":
        if (d.name) patchProject((p) => ({ ...p, name: d.name, name_source: "agent" }));
        break;
      case "build.message": {
        const n = d.version_n;
        if (n == null || !d.text) break;
        const line = { text: d.text, ts: d.ts, stage: d.stage, derived: d.derived };
        const add = (st: Status | undefined): Status => {
          const base: Status = st || { state: "running" };
          const msgs = base.messages || [];
          if (msgs.some((x) => x.text === line.text && x.ts === line.ts)) return base;
          return { ...base, messages: [...msgs, line] };
        };
        set({ statuses: { ...get().statuses, [n]: add(get().statuses[n]) } });
        patchProject((p) => ({
          ...p,
          chat: p.chat.map((m) => (m.kind === "progress" && m.payload?.version === n ? { ...m, payload: { ...m.payload, status: add(m.payload?.status) } } : m)),
        }));
        break;
      }
      case "status": {
        const total = renderingTotal(d.text);
        if (total != null && turn) {
          // concepts render in parallel: one counter step for the whole set (as the server's progress record words it)
          set({ turn: { ...turn, concepts: {}, conceptTotal: total } });
          step("concepts", conceptStepLabel({}, total));
        } else step(`status:${d.text}`, d.text);
        break;
      }
      case "thinking.delta":
        if (turn && d.text) set({ turn: { ...turn, thoughts: appendThought(turn.thoughts || "", String(d.text)) } });
        break;
      case "message.start":
        patchProject((p) => ({
          ...p,
          chat: upsert(p.chat, { id: d.message_id, role: d.role || "assistant", kind: d.kind || "text", text: "", payload: null, ts: new Date().toISOString(), draft: true }),
        }));
        // upsert() clears draft; set it back for the streaming message
        patchProject((p) => ({ ...p, chat: p.chat.map((m) => (m.id === d.message_id ? { ...m, draft: true } : m)) }));
        break;
      case "text.delta":
        patchProject((p) => ({ ...p, chat: p.chat.map((m) => (m.id === d.message_id ? { ...m, text: (m.text || "") + (d.delta || "") } : m)) }));
        break;
      case "message.done":
      case "choices":
        if (d.message) patchProject((p) => ({ ...p, chat: upsert(p.chat, d.message) }));
        break;
      case "concepts":
        patchProject((p) => {
          const byId = new Map(p.concepts.map((c) => [c.id, c]));
          for (const c of d.concepts || []) byId.set(c.id, c);
          return { ...p, stage: "concepts", concepts: [...byId.values()], chat: d.message ? upsert(p.chat, d.message) : p.chat };
        });
        break;
      case "concepts.progress": {
        set({ conceptProgress: { ...get().conceptProgress, [d.concept_id]: { index: d.index, title: d.title, state: d.state, note: d.note } } });
        if (!turn) break;
        const concepts = { ...(turn.concepts || {}), [d.concept_id]: String(d.state || "") };
        set({ turn: { ...turn, concepts } });
        step("concepts", conceptStepLabel(concepts, turn.conceptTotal || 0));
        break;
      }
      case "brief":
        patchProject((p) => ({ ...p, stage: "brief", brief: d.brief || p.brief, chat: d.message ? upsert(p.chat, d.message) : p.chat }));
        break;
      case "tool.start":
        step(`tool:${d.call_id}`, d.label || d.name);
        break;
      case "tool.done":
        step(`tool:${d.call_id}`, d.summary || "", d.ok === false ? "error" : "done");
        break;
      case "repair":
        step(`repair:${d.kind || "spec"}:${d.round}`, `${d.kind === "concept_rules" ? "Fixing the concept rules" : "Fixing the design"} (round ${d.round})`);
        break;
      case "build.started":
        patchProject((p) => ({
          ...p,
          stage: "building",
          versions: d.version ? upsertVersion(p.versions, d.version) : p.versions,
          current_version: d.version?.n ?? p.current_version,
          chat: d.message ? upsert(p.chat, d.message) : p.chat,
        }));
        break;
      case "build.progress": {
        const n = d.version_n;
        let st: Status = d.status;
        if (n == null || !st) break;
        // keep narration lines that arrived as build.message events if this status has fewer
        const prev = get().statuses[n];
        if (prev?.messages?.length && (st.messages?.length || 0) < prev.messages.length) st = { ...st, messages: prev.messages };
        set({ statuses: { ...get().statuses, [n]: st } });
        patchProject((p) => ({
          ...p,
          versions: p.versions.map((v) => (v.n === n ? { ...v, state: st.state === "done" || st.state === "error" ? v.state : st.state, percent: st.percent ?? v.percent } : v)),
          chat: p.chat.map((m) => (m.kind === "progress" && m.payload?.version === n ? { ...m, payload: { ...m.payload, status: st } } : m)),
        }));
        break;
      }
      case "result":
        patchProject((p) => ({
          ...p,
          versions: d.version ? upsertVersion(p.versions, d.version) : p.versions,
          chat: d.message ? upsert(p.chat, d.message) : p.chat,
        }));
        if (d.version?.n != null) clearFileCache(`/v/${d.version.n}/`);
        break;
      case "project":
        if (d.project) get().setProject(d.project);
        break;
      case "error":
        set({ sendError: d.message || "Something went wrong.", sendErrorCode: d.code || "error" });
        break;
    }
  };

  /** Finish this tab's turn: it moves to `turns` (the "Worked for" pill under its message keeps steps and thoughts). */
  const settleTurn = () => {
    const done = get().turn;
    if (!done) return;
    const finished: TurnState = { ...done, running: false, stale: false, endedAt: Date.now(), steps: done.steps.map((s) => (s.state === "running" ? { ...s, state: "done" } : s)) };
    set({ turn: null, turns: done.userMsgId ? { ...get().turns, [done.userMsgId]: finished } : get().turns });
  };

  const pollTurn = async () => {
    const w = turnWatch;
    const ctx = get().ctx;
    if (!w || !ctx || ctx.share || ctx.pid !== w.pid) return stopTurnWatch();
    const live = get().turn;
    if (live?.running && !live.remote) return stopTurnWatch(); // this tab streams the turn itself
    let v;
    try {
      v = await api.turnStatus(w.pid);
    } catch (e: any) {
      if (turnWatch !== w) return;
      if (e instanceof ApiError && (e.status === 404 || e.status === 401)) return stopTurnWatch();
      w.fails += 1; // flaky network: keep the card, try again a little later
      turnTimer = window.setTimeout(() => void pollTurn(), Math.min(10000, POLL_MS * (1 + w.fails)));
      return;
    }
    if (turnWatch !== w || get().ctx?.pid !== w.pid) return;
    w.fails = 0;
    const cur = get().turn;
    if (cur?.running && !cur.remote) return stopTurnWatch();
    const decision = watchDecision(v);
    if (decision === "running") {
      set({ turn: remoteTurn(v, cur, get().project?.chat || []) });
      turnTimer = window.setTimeout(() => void pollTurn(), POLL_MS);
      return;
    }
    stopTurnWatch();
    if (decision === "stale") {
      set({ turn: remoteTurn(v, cur, get().project?.chat || []) });
      return;
    }
    // the turn ended: keep its steps and thinking under its message, then load what it made
    if (cur) {
      const fin = v.turn_id && v.turn_id === cur.turnId ? remoteTurn({ ...v, running: false }, cur, get().project?.chat || []) : cur;
      set({ turn: { ...fin, running: false, stale: false } });
      // a message that never reached the server (the connection dropped while sending): say so
      if (!cur.turnId && !cur.userMsgId)
        set({ sendError: "The connection dropped before your message reached the studio. Check the connection and send it again.", sendErrorCode: "network" });
    }
    settleTurn();
    try {
      await get().refresh();
    } catch {
      /* the next load shows it */
    }
    if (isBuilding(get().project)) get().followBuild();
  };

  return {
    ctx: null,
    project: null,
    loading: false,
    error: null,
    readOnly: false,
    turn: null,
    turns: {},
    conceptProgress: {},
    statuses: {},
    saving: 0,
    saveError: null,
    sendError: null,
    sendErrorCode: null,
    streamOpen: false,

    reset: () => {
      get().stopFollow();
      set({ ctx: null, project: null, error: null, turn: null, turns: {}, conceptProgress: {}, statuses: {}, sendError: null, sendErrorCode: null, readOnly: false });
    },

    setProject: (p) => {
      // keep any still-streaming draft messages that the snapshot does not have yet
      const cur = get().project;
      const drafts = cur && cur.id === p.id ? cur.chat.filter((m) => m.draft && !p.chat.some((x) => x.id === m.id)) : [];
      set({ project: { ...p, chat: [...p.chat, ...drafts] } });
    },

    load: async (pid) => {
      const same = get().project?.id === pid;
      if (!same) get().reset();
      set({ ctx: { pid }, loading: !same, error: null, readOnly: false });
      try {
        const p = await api.project(pid);
        get().setProject(p);
        set({ loading: false });
        // a turn is running on the server (this page was reloaded, or the message came from another tab or a voice
        // call): show it working instead of an idle composer
        if (p.turn && !get().turn?.running) get().watchTurn();
        if (isBuilding(p)) get().followBuild();
      } catch (e: any) {
        set({ loading: false, error: e instanceof ApiError && e.status === 404 ? "This project was not found." : e?.message || String(e) });
      }
    },

    loadShare: async (token) => {
      get().reset();
      set({ loading: true, error: null, readOnly: true });
      try {
        const p = await api.shared(token);
        set({ ctx: { pid: p.id, share: token }, project: p, loading: false });
        if (isBuilding(p)) get().followBuild();
      } catch (e: any) {
        set({ loading: false, error: e instanceof ApiError && e.status === 404 ? "This share link is no longer valid." : e?.message || String(e) });
      }
    },

    refresh: async () => {
      const { ctx } = get();
      if (!ctx) return;
      const p = ctx.share ? await api.shared(ctx.share) : await api.project(ctx.pid);
      get().setProject(p);
    },

    send: async (body) => {
      const { ctx, project, turn } = get();
      if (!ctx || !project || ctx.share || turn?.running) return;
      stopTurnWatch();
      const action = body.action || (body.answers || body.skipped ? "answers" : "text");
      const t: TurnState = { running: true, startedAt: Date.now(), action, status: "Reading your message", steps: [{ id: "start", label: "Reading your message", state: "running" }], thoughts: "" };
      set({ turn: t, sendError: null, sendErrorCode: null, conceptProgress: body.action === "regenerate_concepts" || body.action === "more_concepts" ? {} : get().conceptProgress });
      const mode = project.mode;
      let sawStart = false;
      let sawDone = false;
      let dropped = false; // the stream ended without `done` (network, reload of the server, proxy timeout)
      let conflict = false;
      try {
        // follow_build false: the turn ends once a build is queued, so the composer stays usable during a long build;
        // progress then arrives on the project event stream (followBuild below).
        await postStream(`/api/projects/${ctx.pid}/chat`, { ...(mode ? { mode } : {}), follow_build: false, ...body }, (ev) => {
          if (ev.event === "turn.start") sawStart = true;
          if (ev.event === "done") sawDone = true;
          handle(ev);
        });
        if (!sawDone) dropped = true;
      } catch (e: any) {
        if (e instanceof ApiError) {
          conflict = e.code === "conflict";
          set({ sendError: conflict ? CONFLICT_TEXT : e.message || String(e), sendErrorCode: e.code || "error" });
        } else dropped = true;
      } finally {
        let refreshed = true;
        try {
          await get().refresh();
        } catch {
          refreshed = false; /* keep what we have */
        }
        const running = !!get().project?.turn || !refreshed;
        if ((dropped || conflict) && running && get().ctx?.pid === ctx.pid) {
          // the turn goes on on the server: keep the card and follow it there (GET /turn)
          const cur = get().turn;
          if (conflict || !cur) set({ turn: null });
          else set({ turn: { ...cur, remote: true } });
          get().watchTurn();
        } else {
          settleTurn();
          if (dropped && !sawStart && !conflict)
            set({ sendError: "The connection dropped before your message reached the studio. Check the connection and send it again.", sendErrorCode: "network" });
        }
        if (isBuilding(get().project)) get().followBuild();
      }
    },

    watchTurn: () => {
      const { ctx } = get();
      if (!ctx || ctx.share) return;
      if (turnWatch?.pid === ctx.pid) return; // already following it
      stopTurnWatch();
      turnWatch = { pid: ctx.pid, fails: 0 };
      const p = get().project;
      const cur = get().turn;
      if (p?.turn && !cur?.running) {
        // show the card at once from the lock; the first poll fills in the step and the thinking (and corrects the
        // stale guess if this laptop's clock is off)
        const age = (Date.now() - Date.parse(p.turn.started)) / 1000;
        const stale = age >= TURN_STALE_S;
        set({
          turn: remoteTurn(
            { running: !stale, stale, state: stale ? "stale" : "running", turn_id: p.turn.id, started: p.turn.started, age_s: Number.isNaN(age) ? null : Math.max(0, age), step: "Working", steps: [], thoughts: "", user_message_id: null, action: null, mode: null, updated: null, quiet_s: null },
            cur,
            p.chat,
          ),
        });
      }
      void pollTurn();
    },

    retryLast: async () => {
      const { project, turn } = get();
      if (!project) return;
      const m = (turn?.userMsgId && project.chat.find((x) => x.id === turn.userMsgId)) || [...project.chat].reverse().find((x) => x.role === "user");
      const body = retryBody(m);
      if (turn && !turn.running) set({ turn: null });
      if (!body) {
        set({ sendError: "That step cannot be repeated from here. Send your message again.", sendErrorCode: "retry" });
        return;
      }
      await get().send(body);
    },

    followBuild: () => {
      const { ctx } = get();
      if (!ctx || closeStream) return;
      if (ctx.share) {
        // share links have no event stream: poll the read-only project
        if (shareTimer) return;
        shareTimer = window.setInterval(async () => {
          try {
            await get().refresh();
            if (!isBuilding(get().project) && shareTimer) {
              clearInterval(shareTimer);
              shareTimer = null;
            }
          } catch {
            /* retry next tick */
          }
        }, 5000);
        return;
      }
      set({ streamOpen: true });
      closeStream = openEventStream(`/api/projects/${ctx.pid}/stream`, ["build.progress", "build.message", "result", "message.done", "project", "done"], handle, () => {
        closeStream = null;
        set({ streamOpen: false });
        // reconnect if a build is still running (the server closes idle streams)
        window.setTimeout(() => {
          if (isBuilding(get().project) && get().ctx?.pid === ctx.pid) get().followBuild();
        }, 2000);
      });
    },

    stopFollow: () => {
      closeStream?.();
      closeStream = null;
      if (shareTimer) clearInterval(shareTimer);
      shareTimer = null;
      stopTurnWatch();
    },

    rename: async (name) => {
      const { ctx } = get();
      if (!ctx || ctx.share) return;
      set({ saving: get().saving + 1, saveError: null });
      try {
        const p = await api.patchProject(ctx.pid, { name });
        patchProject((cur) => ({ ...cur, name: p.name, updated: p.updated }));
      } catch (e: any) {
        set({ saveError: e?.message || "Could not save" });
      } finally {
        set({ saving: get().saving - 1 });
      }
    },

    setMode: async (mode) => {
      const { ctx } = get();
      patchProject((p) => ({ ...p, mode }));
      if (!ctx || ctx.share) return;
      try {
        await api.patchProject(ctx.pid, { mode });
      } catch {
        /* the mode is also sent with each turn */
      }
    },

    undo: async () => {
      const { ctx } = get();
      if (!ctx) return;
      get().setProject(await api.undo(ctx.pid));
    },
    redo: async () => {
      const { ctx } = get();
      if (!ctx) return;
      get().setProject(await api.redo(ctx.pid));
    },
    setCurrent: async (n) => {
      const { ctx } = get();
      if (!ctx) return;
      if (ctx.share) {
        patchProject((p) => ({ ...p, current_version: n }));
        return;
      }
      get().setProject(await api.setCurrent(ctx.pid, n));
    },
    restore: async (n) => {
      const { ctx } = get();
      if (!ctx) return;
      const v = await api.restore(ctx.pid, n);
      patchProject((p) => ({ ...p, versions: upsertVersion(p.versions, v), current_version: v.n }));
      await get().refresh();
      if (isBuilding(get().project)) get().followBuild();
    },

    setChecked: async (track, stepId, v) => {
      const { ctx } = get();
      patchProject((p) => ({ ...p, checklist: { ...(p.checklist || {}), [track]: { ...(p.checklist?.[track] || {}), [stepId]: v } } }));
      if (!ctx || ctx.share) return;
      set({ saving: get().saving + 1, saveError: null });
      try {
        const r = await api.checklist(ctx.pid, { [track]: { [stepId]: v } });
        if (r?.checklist) patchProject((p) => ({ ...p, checklist: r.checklist }));
      } catch (e: any) {
        set({ saveError: "Your checklist could not be saved" });
      } finally {
        set({ saving: get().saving - 1 });
      }
    },

    afterTweak: (v) => {
      patchProject((p) => ({ ...p, versions: upsertVersion(p.versions, v), current_version: v.n }));
      void get()
        .refresh()
        .catch(() => {})
        .then(() => get().followBuild());
    },
  };
});

export function currentVersion(p: Project | null): Version | null {
  if (!p || p.current_version == null) return null;
  return p.versions.find((v) => v.n === p.current_version) || null;
}

/** The newest version that finished, at or before the current one (what the tabs can show). */
export function shownVersion(p: Project | null): Version | null {
  const cur = currentVersion(p);
  if (cur?.state === "done") return cur;
  return null;
}

export function lastDoneVersion(p: Project | null): Version | null {
  if (!p) return null;
  const done = p.versions.filter((v) => v.state === "done");
  return done.length ? done[done.length - 1] : null;
}
