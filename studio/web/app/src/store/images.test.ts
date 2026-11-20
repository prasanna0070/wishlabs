// The project store with images (start from an image): an image-only message goes out with its attachments, the
// "image_brief" SSE event lands on the project at once (either shape), a refused message gives the images back to the
// composer, Retry sends the images again, and an image-only message that did arrive is not reported as lost.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

(globalThis as any).window = globalThis;

const h = vi.hoisted(() => ({
  project: null as any,
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
      turnStatus: vi.fn(async () => ({ running: false, state: "done", turn_id: null })),
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
import { retryBody, sentArrived } from "../lib/working";

const att = { id: "u_sheet00001", name: "sheet.png", mime: "image/jpeg", width: 1200, height: 1600 };
const imgMsg = { id: "m_1", role: "user", kind: "text", text: "Here is what I have in mind.", payload: null, ts: "2026-11-20T10:00:00Z", attachments: [att] };
const reply = { id: "m_2", role: "assistant", kind: "text", text: "I read your concept sheet.", payload: null, ts: "2026-11-20T10:01:00Z" };
const brief = { upload_ids: [att.id], kind: "concept_sheet", title: "Bike helmet", features: [{ text: "Voice alerts", status: "buildable", catalog_parts: ["MIC_INMP441"] }], created: "2026-11-20T10:00:30Z" };
const base = (o: any = {}) => ({ id: "p_1", name: "Untitled gadget", stage: "clarify", mode: "fast", chat: [], concepts: [], versions: [], current_version: null, turn: null, ...o });
const flush = async () => {
  for (let i = 0; i < 40; i++) await Promise.resolve();
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
  h.project = base();
  h.stream = null;
  h.posts = [];
  useProject.getState().reset();
});
afterEach(() => {
  useProject.getState().reset();
  vi.useRealTimers();
});

describe("images in the project store", () => {
  it("sends an image-only first message with its attachments and shows the image brief as soon as it streams", async () => {
    await useProject.getState().load("p_1");
    let mid: any = null;
    h.stream = async (on) => {
      on({ event: "turn.start", data: { turn_id: "t_1", started: new Date().toISOString(), user_message: imgMsg } });
      on({ event: "tool.start", data: { call_id: "c1", name: "record_image_brief", label: "Reading your image" } });
      on({ event: "image_brief", data: { seq: 3, ...brief } });
      mid = structuredClone(useProject.getState().project);
      on({ event: "message.done", data: { message: reply } });
      on({ event: "done", data: {} });
    };
    h.project = base({ chat: [imgMsg, reply], image_brief: brief });
    await useProject.getState().send({ text: "", attachments: [{ id: att.id }] });
    expect(h.posts[0]).toMatchObject({ text: "", attachments: [{ id: att.id }], follow_build: false, mode: "fast" });
    expect(mid.image_brief).toEqual(brief); // before the turn ended
    expect(mid.chat[0].attachments).toEqual([att]);
    const st = useProject.getState();
    expect(st.project?.image_brief?.title).toBe("Bike helmet");
    expect(st.sendError).toBeNull();
    expect(st.unsent).toBeNull();
  });

  it("takes the event wrapped as {image_brief: {...}} too", async () => {
    await useProject.getState().load("p_1");
    h.stream = async (on) => {
      on({ event: "image_brief", data: { seq: 1, image_brief: brief } });
      on({ event: "done", data: {} });
    };
    h.project = base({ image_brief: brief });
    await useProject.getState().send({ text: "A lamp like this", attachments: [{ id: att.id }] });
    expect(useProject.getState().project?.image_brief).toEqual(brief);
  });

  it("gives the images back to the composer when the studio refuses the message", async () => {
    h.project = base({ chat: [reply] });
    await useProject.getState().load("p_1");
    h.stream = async () => {
      throw new ApiError(409, "conflict", "New concepts can be shown once the build finishes.");
    };
    await useProject.getState().send({ text: "", attachments: [{ id: att.id }] });
    await flush();
    expect(useProject.getState().unsent).toEqual({ text: "", attachments: [{ id: att.id }] });
    await useProject.getState().send({ text: "like this", attachments: [{ id: att.id }] });
    await flush();
    expect(useProject.getState().unsent).toEqual({ text: "like this", attachments: [{ id: att.id }] });
  });

  it("Retry sends the stored images again, by id", () => {
    expect(retryBody(imgMsg as any)).toEqual({ text: "Here is what I have in mind.", attachments: [{ id: att.id }] });
    expect(retryBody({ ...imgMsg, attachments: undefined, payload: { attachments: [att] } } as any)).toEqual({ text: "Here is what I have in mind.", attachments: [{ id: att.id }] });
    expect(retryBody({ ...imgMsg, attachments: [], text: "A lamp" } as any)).toEqual({ text: "A lamp" });
  });

  it("an image-only message stored with the stand-in words counts as arrived", () => {
    const before = new Set<string>();
    expect(sentArrived([imgMsg as any], before, { text: "", attachments: [{ id: att.id }] })).toBe(true);
    expect(sentArrived([imgMsg as any], before, { text: "", attachments: [{ id: "u_other00001" }] })).toBe(false);
    expect(sentArrived([imgMsg as any], new Set(["m_1"]), { text: "", attachments: [{ id: att.id }] })).toBe(false);
    expect(sentArrived([{ ...imgMsg, attachments: [] } as any], before, { text: "Here is what I have in mind." })).toBe(true);
  });
});
