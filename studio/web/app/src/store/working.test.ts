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
}));

vi.mock("../lib/api", async (orig) => {
  const real: any = await orig();
  return {
    ...real,
    api: {
      ...real.api,
      project: vi.fn(async () => structuredClone(h.project)),
      turnStatus: vi.fn(async () => {
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

import { ApiError } from "../lib/api";
import { useProject } from "./project";

const user = { id: "m_1", role: "user", kind: "text", text: "A bedtime robot", payload: null, ts: "2026-11-20T06:00:00Z" };
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
const ended = () => running({ running: false, state: "done" });

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
    h.stream = async (on) => {
      on({ event: "turn.start", data: { turn_id: "t_1", started: new Date().toISOString(), user_message: user } });
      on({ event: "status", data: { text: "Thinking" } });
      on({ event: "thinking.delta", data: { text: "**Reading the wish**\n\nA calm robot.\n\n" } });
      on({ event: "thinking.delta", data: { text: "**Choosing questions**\n\nAge first.\n\n" } });
      mid = structuredClone(useProject.getState().turn);
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
    expect(vi.mocked((await import("../lib/api")).api.turnStatus).mock.calls.length).toBe(1); // no more polling
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
