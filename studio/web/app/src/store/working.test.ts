// The project store's working state, with the API and the SSE transport mocked: thinking streams into the live turn,
// a reload finds a running turn and follows it, a dropped stream hands over to the server's record, a 409 becomes a
// friendly card that follows the running turn, and a stale lock offers Retry.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

(globalThis as any).window = globalThis; // the store schedules its polls with window.setTimeout

const h = vi.hoisted(() => ({
  project: null as any,
  views: [] as any[],
  stream: null as null | ((onEvent: (e: any) => void) => Promise<void>),
  posts: [] as any[],
  projectDelay: null as null | Promise<void>, // GET /api/projects/{pid} stalls until this settles (venue Wi-Fi)
  stall: 0, // this many GET /turn calls never answer (until their abort signal fires)
}));

vi.mock("../lib/api", async (orig) => {
  const real: any = await orig();
  return {
    ...real,
    api: {
      ...real.api,
      project: vi.fn(async () => {
        if (h.projectDelay) await h.projectDelay;
        return structuredClone(h.project);
      }),
      turnStatus: vi.fn(async (_pid: string, signal?: AbortSignal) => {
        if (h.stall > 0) {
          h.stall -= 1;
          return new Promise((_, reject) => signal?.addEventListener("abort", () => reject(new DOMException("The operation was aborted.", "AbortError"))));
        }
        const v = h.views.length > 1 ? h.views.shift() : h.views[0];
        if (v instanceof Error) throw v;
        return structuredClone(v);
      }),
    },
  };
});

vi.mock("../lib/sse", () => ({
  postStream: vi.fn(async (_url: string, body: unknown, onEvent: (e: any) => void) => {
    h.posts.push(body);
    if (h.stream) await h.stream(onEvent);
  }),
  openEventStream: vi.fn(() => () => {}),
}));

import { ApiError, api } from "../lib/api";
import { useProject } from "./project";

const user = { id: "m_1", role: "user", kind: "text", text: "A bedtime robot", payload: null, ts: "2026-11-20T06:00:00Z" };
const answer = { id: "m_2", role: "assistant", kind: "text", text: "Here you go", payload: null, ts: "2026-11-20T06:01:00Z" };
const baseProject = (o: any = {}) => ({
  id: "p_1",
  name: "Robot",
  stage: "clarify",
  mode: "pro",
  chat: [],
  concepts: [],
  versions: [],
  current_version: null,
  turn: null,
  ...o,
});
const running = (o: any = {}) => ({
  running: true,
  stale: false,
  state: "running",
  turn_id: "t_1",
  started: new Date(Date.now() - 65_000).toISOString(),
  age_s: 65,
  step: "Designing concepts",
  steps: [
    { label: "Reading your message", ts: null },
    { label: "Thinking", ts: null },
    { label: "Designing concepts", ts: null },
  ],
  thoughts: "**Sketching shapes**\n\nSix shapes in three tiers.\n\n",
  user_message_id: "m_1",
  action: "text",
  mode: "pro",
  updated: null,
  quiet_s: 1,
  ...o,
});
const ended = (o: any = {}) => running({ running: false, state: "done", ...o });
const lockedAt = (msAgo: number) => ({ id: "t_1", started: new Date(Date.now() - msAgo).toISOString() });
const polls = () => vi.mocked(api.turnStatus).mock.calls.length;

const flush = async () => {
  for (let i = 0; i < 40; i++) await Promise.resolve();
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
  h.project = baseProject();
  h.views = [];
  h.stream = null;
  h.posts = [];
  h.projectDelay = null;
  h.stall = 0;
  useProject.getState().reset();
});
afterEach(() => {
  useProject.getState().reset();
  vi.useRealTimers();
});

describe("live turn", () => {
  it("streams thought summaries into the working turn and keeps them after it finishes", async () => {
    let mid: any = null;
    let counter = "";
    let repairSteps: string[] = [];
    h.stream = async (on) => {
      on({ event: "turn.start", data: { turn_id: "t_1", started: new Date().toISOString(), user_message: user } });
      on({ event: "status", data: { text: "Thinking" } });
      on({ event: "thinking.delta", data: { text: "**Reading the wish**\n\nA calm robot.\n\n" } });
      on({ event: "thinking.delta", data: { text: "**Choosing questions**\n\nAge first.\n\n" } });
      mid = structuredClone(useProject.getState().turn);
      on({ event: "repair", data: { round: 1, errors: ["Moon Bot: wall too thin"] } });
      on({ event: "status", data: { text: "Fixing the design" } }); // the same step, not a second line
      repairSteps = (useProject.getState().turn?.steps || []).map((s) => s.label);
      on({ event: "status", data: { text: "Rendering 6 concepts" } });
      on({ event: "concepts.progress", data: { concept_id: "c_1", index: 0, title: "A", state: "ready" } });
      on({ event: "concepts.progress", data: { concept_id: "c_2", index: 1, title: "B", state: "photo" } });
      const steps = useProject.getState().turn?.steps || [];
      counter = steps[steps.length - 1]?.label || "";
      on({ event: "done", data: {} });
    };
    await useProject.getState().load("p_1");
    h.project = baseProject({ chat: [user] });
    await useProject.getState().send({ text: "A bedtime robot" });
    expect(counter).toBe("Rendering concept photos: 1 of 6 done");
    expect(repairSteps).toEqual(["Reading your message", "Thinking", "Fixing the design (round 1)"]);
    expect(mid.running).toBe(true);
    expect(mid.thoughts).toContain("Reading the wish");
    expect(mid.thoughts).toContain("Choosing questions");
    const st = useProject.getState();
    expect(st.turn).toBeNull();
    expect(st.turns.m_1.thoughts).toContain("Choosing questions");
    expect(st.project?.chat.map((m) => m.text).join(" ")).not.toContain("Reading the wish");
  });
});

describe("a turn running on the server", () => {
  it("is shown working after a reload, polled every 2 s, then the project reloads when it ends", async () => {
    h.project = baseProject({ chat: [user], turn: { id: "t_1", started: new Date(Date.now() - 65_000).toISOString() } });
    h.views = [running(), running({ step: "Checking the concepts against the parts catalog" }), ended()];
    await useProject.getState().load("p_1");
    await flush();
    let t = useProject.getState().turn!;
    expect(t.running && t.remote).toBe(true);
    expect(t.userMsgId).toBe("m_1");
    expect(t.status).toBe("Designing concepts");
    expect(t.thoughts).toContain("Sketching shapes");
    // the composer must not send while it runs (no 409)
    await useProject.getState().send({ text: "hello?" });
    expect(h.posts).toEqual([]);
    await vi.advanceTimersByTimeAsync(2000);
    t = useProject.getState().turn!;
    expect(t.status).toBe("Checking the concepts against the parts catalog");
    h.project = baseProject({ chat: [user, { id: "m_2", role: "assistant", kind: "concepts", text: "Pick one", payload: {}, ts: "" }], stage: "concepts" });
    await vi.advanceTimersByTimeAsync(2000);
    await flush();
    const st = useProject.getState();
    expect(st.turn).toBeNull();
    expect(st.turns.m_1.running).toBe(false);
    expect(st.turns.m_1.thoughts).toContain("Sketching shapes");
    expect(st.project?.stage).toBe("concepts"); // reloaded: the concepts appear
  });

  it("keeps the card through a flaky poll", async () => {
    h.project = baseProject({ chat: [user], turn: { id: "t_1", started: new Date().toISOString() } });
    h.views = [running(), new TypeError("Failed to fetch"), running({ step: "Thinking" })];
    await useProject.getState().load("p_1");
    await flush();
    await vi.advanceTimersByTimeAsync(2000);
    expect(useProject.getState().turn?.running).toBe(true);
    await vi.advanceTimersByTimeAsync(4000);
    expect(useProject.getState().turn?.status).toBe("Thinking");
  });

  it("follows the turn on the server when the chat stream drops", async () => {
    h.stream = async (on) => {
      on({ event: "turn.start", data: { turn_id: "t_1", started: new Date().toISOString(), user_message: user } });
      on({ event: "thinking.delta", data: { text: "**Reading the wish**\n\nA calm robot for bedtime, simple to hold and safe.\n\n" } });
      h.project = baseProject({ chat: [user], turn: { id: "t_1", started: new Date().toISOString() } });
      throw new TypeError("network error"); // the connection dropped mid-turn
    };
    h.views = [running({ thoughts: "**Reading the wish**\n\nA calm robot for bedtime, simple to hold and safe.\n\n**Sketching shapes**\n\nSix.\n\n" })];
    await useProject.getState().load("p_1");
    await useProject.getState().send({ text: "A bedtime robot" });
    await flush();
    const st = useProject.getState();
    expect(st.sendError).toBeNull(); // no red error: it is still working
    expect(st.turn?.running && st.turn.remote).toBe(true);
    expect(st.turn?.thoughts).toBe("**Reading the wish**\n\nA calm robot for bedtime, simple to hold and safe.\n\n**Sketching shapes**\n\nSix.\n\n");
    expect(st.turn?.status).toBe("Designing concepts");
  });

  it("turns a 409 into a friendly note and shows the running turn", async () => {
    h.project = baseProject({ chat: [user] });
    await useProject.getState().load("p_1");
    h.stream = async () => {
      h.project = baseProject({ chat: [user], turn: { id: "t_1", started: new Date().toISOString() } });
      throw new ApiError(409, "conflict", "The assistant is still answering the previous message.");
    };
    h.views = [running()];
    await useProject.getState().send({ text: "again" });
    await flush();
    const st = useProject.getState();
    expect(st.sendErrorCode).toBe("conflict");
    expect(st.sendError).toMatch(/still working on your previous message/);
    expect(st.turn?.running && st.turn.remote).toBe(true);
    expect(st.turn?.userMsgId).toBe("m_1");
  });

  it("offers Retry when the lock is stale, and Retry sends the same message again", async () => {
    h.project = baseProject({ chat: [user], turn: { id: "t_1", started: new Date(Date.now() - 16 * 60_000).toISOString() } });
    h.views = [running({ running: false, stale: true, state: "stale", age_s: 960 })];
    await useProject.getState().load("p_1");
    await flush();
    let t = useProject.getState().turn!;
    expect(t.running).toBe(false);
    expect(t.stale).toBe(true);
    await vi.advanceTimersByTimeAsync(6000);
    expect(polls()).toBe(1); // a stale turn is only checked every 15 s
    h.stream = async (on) => {
      on({ event: "turn.start", data: { turn_id: "t_2", started: new Date().toISOString(), user_message: { ...user, id: "m_9" } } });
      on({ event: "done", data: {} });
    };
    h.project = baseProject({ chat: [user, { ...user, id: "m_9" }] });
    await useProject.getState().retryLast();
    expect(h.posts[h.posts.length - 1]).toMatchObject({ text: "A bedtime robot" });
    t = useProject.getState().turns.m_9;
    expect(t.running).toBe(false);
    expect(useProject.getState().turn).toBeNull();
  });
});

describe("review fixes", () => {
  it("H1: leaving a project whose server turn it followed, then reopening it, never leaves the composer locked", async () => {
    h.project = baseProject({ chat: [user], turn: lockedAt(65_000) });
    h.views = [running()];
    await useProject.getState().load("p_1"); // reload mid-turn: follows the server's turn
    await flush();
    expect(useProject.getState().turn?.remote).toBe(true);
    useProject.getState().stopFollow(); // the project page closes (back to the project list)
    expect(useProject.getState().turn).toBeNull();
    // the turn finishes while the list is open, then the same project opens again (same id: no reset)
    h.project = baseProject({ chat: [user, answer] });
    h.views = [ended()];
    await useProject.getState().load("p_1");
    await flush();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(useProject.getState().turn?.running ?? false).toBe(false);
  });

  it("H1: reopened while the followed turn still runs, it is followed again until it ends", async () => {
    h.project = baseProject({ chat: [user], turn: lockedAt(65_000) });
    h.views = [running()];
    await useProject.getState().load("p_1");
    await flush();
    useProject.getState().stopFollow();
    const n = polls();
    await vi.advanceTimersByTimeAsync(6000);
    expect(polls()).toBe(n); // nobody polls while the page is closed
    h.views = [running({ step: "Thinking" }), ended()];
    await useProject.getState().load("p_1");
    await flush();
    expect(useProject.getState().turn?.running && useProject.getState().turn?.remote).toBe(true);
    expect(useProject.getState().turn?.status).toBe("Thinking");
    h.project = baseProject({ chat: [user, answer] });
    await vi.advanceTimersByTimeAsync(2000);
    await flush();
    expect(useProject.getState().turn).toBeNull();
    expect(useProject.getState().turns.m_1.running).toBe(false);
    expect(useProject.getState().project?.chat.map((m) => m.id)).toEqual(["m_1", "m_2"]);
  });

  it("M1: `done` ends the turn at once, even when the project reload after it stalls", async () => {
    await useProject.getState().load("p_1");
    let release!: () => void;
    h.stream = async (on) => {
      on({ event: "turn.start", data: { turn_id: "t_1", started: new Date().toISOString(), user_message: user } });
      on({ event: "project", data: { project: baseProject({ chat: [user, answer] }) } });
      on({ event: "done", data: {} });
      h.projectDelay = new Promise<void>((r) => (release = r)); // GET /api/projects/{pid} stalls
    };
    const sending = useProject.getState().send({ text: "A bedtime robot" });
    await flush();
    await vi.advanceTimersByTimeAsync(30_000);
    const st = useProject.getState();
    expect(st.turn).toBeNull(); // the composer is free again
    expect(st.turns.m_1.running).toBe(false);
    expect(st.project?.chat.map((m) => m.id)).toEqual(["m_1", "m_2"]); // from the `project` event
    release();
    await sending;
  });

  it("M2: no false 'never reached the studio' when the stream dropped but the studio has the message", async () => {
    await useProject.getState().load("p_1");
    h.stream = async () => {
      h.project = baseProject({ chat: [user, answer] }); // stored and answered; turn.start never arrived here
      throw new TypeError("network error");
    };
    vi.mocked(api.project).mockImplementationOnce(async () => {
      throw new TypeError("Failed to fetch"); // and the reload right after failed too: follow the server's turn
    });
    h.views = [ended({ turn_id: "t_1", user_message_id: "m_1" })];
    await useProject.getState().send({ text: "A bedtime robot" });
    await flush();
    await vi.advanceTimersByTimeAsync(2000);
    await flush();
    const st = useProject.getState();
    expect(st.sendError).toBeNull();
    expect(st.unsent).toBeNull();
    expect(st.project?.chat.map((m) => m.id)).toEqual(["m_1", "m_2"]);
    // the same without the failed reload: the chat check alone clears it
    h.stream = async () => {
      h.project = baseProject({ chat: [user, answer, { ...user, id: "m_3", text: "Make it pink" }] });
      throw new TypeError("network error");
    };
    await useProject.getState().send({ text: "Make it pink" });
    await flush();
    expect(useProject.getState().sendError).toBeNull();
  });

  it("M2: a message that really never arrived says so and gives the typed words back", async () => {
    h.project = baseProject({ chat: [user, answer] });
    await useProject.getState().load("p_1");
    h.stream = async () => {
      throw new TypeError("network error"); // dropped before the studio stored anything
    };
    vi.mocked(api.project).mockImplementationOnce(async () => {
      throw new TypeError("Failed to fetch");
    });
    h.views = [ended({ turn_id: "t_0", user_message_id: "m_1" })]; // the studio's last turn is the older one
    await useProject.getState().send({ text: "Make it pink" });
    await flush();
    await vi.advanceTimersByTimeAsync(2000);
    await flush();
    let st = useProject.getState();
    expect(st.sendErrorCode).toBe("network");
    expect(st.sendError).toMatch(/dropped before your message reached the studio/);
    expect(st.unsent).toEqual({ text: "Make it pink" });
    // a button's action has no words to give back
    await useProject.getState().send({ action: "more_concepts" });
    await flush();
    st = useProject.getState();
    expect(st.sendErrorCode).toBe("network");
    expect(st.unsent).toBeNull();
  });

  it("M3: the busy note gives the words back and goes away once the turn it waited for ends", async () => {
    h.project = baseProject({ chat: [user] });
    await useProject.getState().load("p_1");
    h.stream = async () => {
      h.project = baseProject({ chat: [user], turn: lockedAt(0) });
      throw new ApiError(409, "conflict", "The assistant is still answering the previous message.");
    };
    h.views = [running(), ended()];
    await useProject.getState().send({ text: "again" });
    await flush();
    let st = useProject.getState();
    expect(st.sendErrorCode).toBe("conflict");
    expect(st.unsent).toEqual({ text: "again" });
    h.project = baseProject({ chat: [user, answer] });
    await vi.advanceTimersByTimeAsync(2000);
    await flush();
    st = useProject.getState();
    expect(st.sendError).toBeNull();
    expect(st.sendErrorCode).toBeNull();
    expect(st.turn).toBeNull();
  });

  it("M3: the busy note also goes when the page comes back to the project after the turn ended", async () => {
    h.project = baseProject({ chat: [user] });
    await useProject.getState().load("p_1");
    h.stream = async () => {
      h.project = baseProject({ chat: [user], turn: lockedAt(0) });
      throw new ApiError(409, "conflict", "The assistant is still answering the previous message.");
    };
    h.views = [running()];
    await useProject.getState().send({ text: "again" });
    await flush();
    expect(useProject.getState().sendErrorCode).toBe("conflict");
    useProject.getState().stopFollow(); // to the project list while the turn runs
    h.project = baseProject({ chat: [user, answer] }); // it ends meanwhile
    await useProject.getState().load("p_1");
    await flush();
    const st = useProject.getState();
    expect(st.sendError).toBeNull();
    expect(st.turn).toBeNull();
  });

  it("M3: a 409 that is not the turn lock keeps the server's reason, without Show progress", async () => {
    h.project = baseProject({ stage: "building", versions: [{ n: 1, state: "running" }], current_version: 1 });
    await useProject.getState().load("p_1");
    h.stream = async () => {
      throw new ApiError(409, "conflict", "New concepts can be shown once the build finishes.");
    };
    await useProject.getState().send({ action: "regenerate_concepts" });
    const st = useProject.getState();
    expect(st.sendError).toBe("New concepts can be shown once the build finishes.");
    expect(st.sendErrorCode).toBe("refused");
    expect(st.turn).toBeNull();
    expect(st.unsent).toBeNull();
    expect(polls()).toBe(0); // nothing to follow
  });

  it("M3: a 409 for a turn that ended before the reload says it can be sent again", async () => {
    await useProject.getState().load("p_1");
    h.stream = async () => {
      throw new ApiError(409, "conflict", "The assistant is still answering the previous message.");
    };
    await useProject.getState().send({ text: "again" });
    const st = useProject.getState();
    expect(st.sendErrorCode).toBe("refused");
    expect(st.sendError).toMatch(/done now, so you can send it again/);
    expect(st.unsent).toEqual({ text: "again" });
    expect(st.turn).toBeNull();
  });

  it("M4: a stale turn is still checked every 15 s, and its result loads if it ends after all", async () => {
    h.project = baseProject({ chat: [user], turn: lockedAt(16 * 60_000) });
    h.views = [running({ running: false, stale: true, state: "stale", age_s: 960 }), ended()];
    await useProject.getState().load("p_1");
    await flush();
    expect(useProject.getState().turn?.stale).toBe(true);
    h.project = baseProject({ chat: [user, answer], stage: "concepts" });
    await vi.advanceTimersByTimeAsync(15_000);
    await flush();
    expect(polls()).toBe(2);
    expect(useProject.getState().turn).toBeNull();
    expect(useProject.getState().project?.stage).toBe("concepts");
  });

  it("M4: a followed turn with no progress for a minute is marked quiet", async () => {
    h.project = baseProject({ chat: [user], turn: lockedAt(5 * 60_000) });
    h.views = [running({ quiet: true, quiet_s: 75 })];
    await useProject.getState().load("p_1");
    await flush();
    expect(useProject.getState().turn?.quiet).toBe(true);
    expect(useProject.getState().turn?.running).toBe(true);
  });

  it("gives up on a stalled GET /turn after 10 s and asks again", async () => {
    h.project = baseProject({ chat: [user], turn: lockedAt(30_000) });
    h.stall = 1;
    h.views = [running({ step: "Thinking" })];
    await useProject.getState().load("p_1");
    await flush();
    expect(polls()).toBe(1);
    await vi.advanceTimersByTimeAsync(10_000); // aborted: a failed poll, tried again 4 s later
    await vi.advanceTimersByTimeAsync(4_000);
    await flush();
    expect(polls()).toBe(2);
    expect(useProject.getState().turn?.status).toBe("Thinking");
  });

  it("loads the message of a turn started in another tab, once, so its card has a place", async () => {
    h.project = baseProject({ chat: [user], turn: lockedAt(30_000) });
    h.views = [running(), running({ turn_id: "t_2", user_message_id: "m_3", step: "Thinking" })];
    await useProject.getState().load("p_1");
    await flush();
    const loads = vi.mocked(api.project).mock.calls.length;
    h.project = baseProject({ chat: [user, answer, { ...user, id: "m_3", text: "Make it pink" }], turn: { id: "t_2", started: new Date().toISOString() } });
    await vi.advanceTimersByTimeAsync(2000);
    await flush();
    expect(vi.mocked(api.project).mock.calls.length).toBe(loads + 1);
    expect(useProject.getState().turn?.userMsgId).toBe("m_3");
    expect(useProject.getState().project?.chat.some((m) => m.id === "m_3")).toBe(true);
    await vi.advanceTimersByTimeAsync(2000);
    await flush();
    expect(vi.mocked(api.project).mock.calls.length).toBe(loads + 1);
  });
});
