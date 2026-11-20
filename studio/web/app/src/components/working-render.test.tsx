// The working card's clock ticks every second; the thinking panel under it (up to 30 KB of Markdown) must not be
// parsed again on each tick (review M6). The tests run without a DOM, so the card is mounted with react-reconciler
// (already installed for @react-three/fiber) and a tiny in-memory host.
import { createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TurnState } from "../lib/working";

(globalThis as any).window = globalThis; // useNow ticks with window.setInterval

const h = vi.hoisted(() => ({ md: 0 }));
vi.mock("./ui", async (orig) => {
  const real: any = await orig();
  return {
    ...real,
    Markdown: () => {
      h.md += 1; // one call per parse of the thinking
      return null;
    },
  };
});

type HostNode = { type: string; props: any; children: HostNode[]; text?: string };
const at = (p: HostNode, c: HostNode) => p.children.indexOf(c);
const host = {
  supportsMutation: true,
  supportsPersistence: false,
  supportsHydration: false,
  isPrimaryRenderer: false,
  supportsMicrotasks: true,
  scheduleMicrotask: queueMicrotask,
  createInstance: (type: string, props: any): HostNode => ({ type, props, children: [] }),
  createTextInstance: (text: string): HostNode => ({ type: "#text", props: {}, children: [], text }),
  appendInitialChild: (p: HostNode, c: HostNode) => void p.children.push(c),
  appendChild: (p: HostNode, c: HostNode) => void p.children.push(c),
  appendChildToContainer: (p: HostNode, c: HostNode) => void p.children.push(c),
  insertBefore: (p: HostNode, c: HostNode, b: HostNode) => void p.children.splice(at(p, b), 0, c),
  insertInContainerBefore: (p: HostNode, c: HostNode, b: HostNode) => void p.children.splice(at(p, b), 0, c),
  removeChild: (p: HostNode, c: HostNode) => void p.children.splice(at(p, c), 1),
  removeChildFromContainer: (p: HostNode, c: HostNode) => void p.children.splice(at(p, c), 1),
  finalizeInitialChildren: () => false,
  prepareUpdate: () => true,
  commitUpdate: (n: HostNode, _u: unknown, _t: string, _o: unknown, props: any) => void (n.props = props),
  commitTextUpdate: (n: HostNode, _o: string, text: string) => void (n.text = text),
  resetTextContent: () => {},
  shouldSetTextContent: () => false,
  getRootHostContext: () => ({}),
  getChildHostContext: (c: unknown) => c,
  getPublicInstance: (n: HostNode) => n,
  prepareForCommit: () => null,
  resetAfterCommit: () => {},
  preparePortalMount: () => {},
  clearContainer: (c: HostNode) => void (c.children = []),
  hideInstance: () => {},
  unhideInstance: () => {},
  hideTextInstance: () => {},
  unhideTextInstance: () => {},
  getCurrentEventPriority: () => 16,
  getInstanceFromNode: () => null,
  beforeActiveInstanceBlur: () => {},
  afterActiveInstanceBlur: () => {},
  prepareScopeUpdate: () => {},
  getInstanceFromScope: () => null,
  detachDeletedInstance: () => {},
  now: () => Date.now(),
  scheduleTimeout: (fn: () => void, ms: number) => setTimeout(fn, ms),
  cancelTimeout: (id: any) => clearTimeout(id),
  noTimeout: -1,
};

const textOf = (n: HostNode): string => (n.text ?? "") + n.children.map(textOf).join("");
// passive effects (useNow starting its interval) run on the scheduler's real setImmediate (a Node global the app's
// DOM-only types do not declare)
const effects = () => new Promise((r) => (globalThis as any).setImmediate(r));

beforeEach(() => {
  h.md = 0;
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"] });
});
afterEach(() => vi.useRealTimers());

describe("working card renders", () => {
  it("the 1 s clock does not parse the thinking again; new thoughts do", async () => {
    const pkg = "react-reconciler"; // no types installed: loaded by name
    const Reconciler = (await import(/* @vite-ignore */ pkg)).default;
    const { WorkingCard } = await import("./chat/Messages");
    const r = Reconciler(host);
    const container: HostNode = { type: "root", props: {}, children: [] };
    const root = r.createContainer(container, 0 /* legacy: updates render synchronously */, null, false, null, "", console.error, null);
    const t: TurnState = {
      turnId: "t_1",
      running: true,
      startedAt: Date.now(),
      userMsgId: "m_1",
      status: "Thinking",
      steps: [{ id: "s", label: "Thinking", state: "running" }],
      thoughts: "**Plan**\n\nA calm robot.\n\n",
    };
    const show = (x: TurnState) => r.updateContainer(createElement(WorkingCard, { t: x, open: true }), root, null, null);
    show(t);
    await effects();
    await effects();
    expect(h.md).toBe(1);
    expect(textOf(container)).toContain("Working on it0s");
    await vi.advanceTimersByTimeAsync(5000);
    expect(textOf(container)).toContain("Working on it5s"); // the clock ticked
    expect(h.md).toBe(1); // ... without parsing the thinking again
    show({ ...t, steps: [...t.steps, { id: "d", label: "Designing concepts", state: "running" }] });
    expect(textOf(container)).toContain("Designing concepts");
    expect(h.md).toBe(1); // a new step alone does not parse it either
    show({ ...t, thoughts: `${t.thoughts}**Next**\n\nSix shapes.\n\n` });
    expect(h.md).toBe(2); // new thoughts do
    r.updateContainer(null, root, null, null);
  });
});
