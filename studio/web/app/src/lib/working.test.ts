import { describe, expect, it } from "vitest";
import type { ChatMessage } from "./types";
import {
  agoText,
  appendThought,
  conceptStepLabel,
  currentStepLabel,
  latestThought,
  mergeThoughts,
  remoteTurn,
  renderingTotal,
  retryBody,
  watchDecision,
  type TurnView,
} from "./working";

const view = (o: Partial<TurnView> = {}): TurnView => ({
  running: true,
  stale: false,
  state: "running",
  turn_id: "t_1",
  started: "2026-11-20T06:00:00Z",
  age_s: 90,
  step: "Thinking",
  steps: [
    { label: "Reading your message", ts: "2026-11-20T06:00:00Z" },
    { label: "Thinking", ts: "2026-11-20T06:00:02Z" },
  ],
  thoughts: "**Planning the robot**\n\nIts age decides the controls.\n\n",
  user_message_id: "m_1",
  action: "text",
  mode: "pro",
  updated: "2026-11-20T06:01:29Z",
  quiet_s: 1,
  ...o,
});

const msg = (o: Partial<ChatMessage>): ChatMessage => ({ id: "m_x", role: "user", kind: "text", text: "", payload: null, ts: "2026-11-20T06:00:00Z", ...o });

describe("thought summaries", () => {
  it("appends paragraphs and keeps the newest ones, starting at a heading", () => {
    let buf = "";
    for (let i = 0; i < 400; i++) buf = appendThought(buf, `**Step ${i}**\n\nWeighing option ${i} against the parts.\n\n`, 2000);
    expect(buf.length).toBeLessThanOrEqual(2000);
    expect(buf.startsWith("**Step")).toBe(true);
    expect(buf).toContain("Step 399");
    expect(appendThought("**A**\n\nfirst.", "**B**\n\nsecond.")).toBe("**A**\n\nfirst.\n\n**B**\n\nsecond.");
    expect(appendThought("x", "  ")).toBe("x");
  });

  it("shows the newest heading as the one live line", () => {
    expect(latestThought("**Reading the wish**\n\nA robot.\n\n**Choosing questions**\n\nAge first.")).toBe("Choosing questions");
    expect(latestThought("no headings here\nlast line wins")).toBe("last line wins");
    expect(latestThought("")).toBe("");
    expect(latestThought(`**${"long ".repeat(60)}**`, 40).length).toBeLessThanOrEqual(40);
  });

  it("merges a dropped stream's thoughts with the server's newest window without repeating", () => {
    const a = "**One**\n\nfirst thought, with enough words to be a probe of sixty characters or more.\n\n";
    const b = "**Two**\n\nsecond thought.\n\n";
    const c = "**Three**\n\nthird thought.\n\n";
    expect(mergeThoughts(a + b, a + b)).toBe(a + b);
    expect(mergeThoughts(a + b, b)).toBe(a + b);
    expect(mergeThoughts(a + b, a + b + c)).toBe(a + b + c);
    expect(mergeThoughts("", b)).toBe(b);
    expect(mergeThoughts(a, "")).toBe(a);
    expect(mergeThoughts("**Old**\n\nunrelated.", c)).toBe(c);
  });
});

describe("steps", () => {
  it("counts parallel concept renders in one step, worded like the server", () => {
    expect(renderingTotal("Rendering 6 concepts")).toBe(6);
    expect(renderingTotal("Thinking")).toBeNull();
    expect(conceptStepLabel({}, 6)).toBe("Rendering concept photos: 0 of 6 done");
    expect(conceptStepLabel({ a: "ready", b: "photo", c: "failed", d: "rendering" }, 6)).toBe("Rendering concept photos: 2 of 6 done, 1 failed");
  });

  it("says what it is doing now", () => {
    const steps = [
      { id: "start", label: "Reading your message", state: "done" as const },
      { id: "tool:1", label: "Designing concepts", state: "running" as const },
    ];
    expect(currentStepLabel({ steps, status: "Thinking" })).toBe("Designing concepts");
    expect(currentStepLabel({ steps: [], status: "Thinking" })).toBe("Thinking");
    expect(currentStepLabel({ steps: [], status: "" })).toBe("Working");
  });
});

describe("following a turn from the server", () => {
  const chat = [msg({ id: "m_0", text: "older" }), msg({ id: "m_1", text: "A bedtime robot" })];

  it("turns the server view into a running card under the turn's message, timed by the server's age", () => {
    const now = Date.parse("2026-11-20T06:30:00Z"); // the laptop clock is off: the age is what counts
    const t = remoteTurn(view(), null, chat, now);
    expect(t.running && t.remote).toBe(true);
    expect(t.stale).toBe(false);
    expect(t.userMsgId).toBe("m_1");
    expect(now - t.startedAt).toBe(90_000);
    expect(t.status).toBe("Thinking");
    expect(t.steps.map((s) => s.state)).toEqual(["done", "running"]);
    expect(t.thoughts).toContain("Planning the robot");
    // the next poll keeps the start time (no jumping timer) and adds the new thoughts
    const t2 = remoteTurn(view({ age_s: 93.4, step: "Designing concepts", thoughts: view().thoughts + "**Sketching**\n\nsix shapes.\n\n" }), t, chat, now + 3000);
    expect(t2.startedAt).toBe(t.startedAt);
    expect(t2.status).toBe("Designing concepts");
    expect(t2.thoughts).toContain("Sketching");
  });

  it("falls back to the newest user message and keeps what this tab streamed before the drop", () => {
    const live = { turnId: "t_1", running: true, startedAt: 1000, userMsgId: "m_1", status: "Thinking", steps: [], thoughts: "**Local**\n\nstreamed here before the connection dropped, long enough to probe.\n\n" };
    const t = remoteTurn(view({ user_message_id: null, thoughts: "**Server**\n\nnew.\n\n" }), live, chat, 5000);
    expect(t.userMsgId).toBe("m_1");
    expect(t.startedAt).toBe(1000);
    expect(t.thoughts).toBe("**Server**\n\nnew.\n\n"); // no overlap: the server's window wins
    const other = remoteTurn(view({ turn_id: "t_2", user_message_id: null }), live, chat, 5000);
    expect(other.userMsgId).toBe("m_1"); // a different turn: the newest user message, not the old card's
  });

  it("decides between polling, the stale card and reloading the project", () => {
    expect(watchDecision(view())).toBe("running");
    expect(watchDecision(view({ running: false, stale: true }))).toBe("stale");
    expect(watchDecision(view({ running: false, stale: false }))).toBe("ended");
    const stale = remoteTurn(view({ running: false, stale: true, state: "stale" }), null, chat, Date.now());
    expect(stale.running).toBe(false);
    expect(stale.stale).toBe(true);
    expect(stale.steps.every((s) => s.state === "done")).toBe(true);
  });
});

describe("retry and composer words", () => {
  it("rebuilds the request body of the stopped turn's message", () => {
    expect(retryBody(msg({ text: "A lamp for my desk" }))).toEqual({ text: "A lamp for my desk" });
    expect(retryBody(msg({ text: "My choices:\n5 yo", payload: { action: "answers", answers: [{ question_id: "age", value: "5 yo" }], skipped: false } }))).toEqual({
      answers: [{ question_id: "age", value: "5 yo" }],
    });
    expect(retryBody(msg({ text: "My choices:\nUse your suggestions", payload: { action: "answers", answers: [], skipped: true } }))).toEqual({ answers: [], skipped: true });
    expect(retryBody(msg({ payload: { action: "select_concept", concept_id: "c_1" } }))).toEqual({ action: "select_concept", concept_id: "c_1" });
    expect(retryBody(msg({ payload: { action: "approve_brief" } }))).toEqual({ action: "approve_brief" });
    expect(retryBody(msg({ text: "Edit Story button: move it up", payload: { action: "edit_part", component: "PURCHASE_StoryButton", label: "Story button" } }))).toEqual({
      action: "edit_part",
      component: "PURCHASE_StoryButton",
      instruction: "move it up",
    });
    expect(retryBody(msg({ role: "assistant", text: "hi" }))).toBeNull();
    expect(retryBody(msg({ kind: "tweak", text: "Scale 110%" }))).toBeNull();
    expect(retryBody(null)).toBeNull();
  });

  it("words the composer's wait", () => {
    expect(agoText(20)).toBe("less than a minute ago");
    expect(agoText(185)).toBe("3 min ago");
    expect(agoText(3 * 3600 + 120)).toBe("3 h 2 min ago");
  });
});
