// The project composer with images, mounted without a DOM (react-reconciler and a tiny in-memory host, as in
// working-render.test.tsx): a pasted screenshot and a dropped photo upload through the real api.uploadImage to an
// in-memory fake of the contract, Send waits for them, and a message of images alone goes out with its attachments.
import { createElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeXHR, gifBytes, ImageContractFake, jpegBytes, pngBytes } from "../../lib/imageContract.fake";

(globalThis as any).window = globalThis;

const h = vi.hoisted(() => ({ posts: [] as any[] }));
vi.mock("../../lib/sse", () => ({
  postStream: vi.fn(async (_url: string, body: unknown, onEvent: (e: any) => void) => {
    h.posts.push(body);
    onEvent({ event: "done", data: {} });
  }),
  openEventStream: vi.fn(() => () => {}),
}));

import { useProject } from "../../store/project";
import { api } from "../../lib/api";
import { Composer } from "./Composer";

type HostNode = { type: string; props: any; children: HostNode[]; text?: string; style: Record<string, string>; value?: string; scrollHeight: number };
const at = (p: HostNode, c: HostNode) => p.children.indexOf(c);
const node = (type: string, props: any): HostNode => ({ type, props, children: [], style: {}, value: props?.value, scrollHeight: 46 });
const host = {
  supportsMutation: true,
  supportsPersistence: false,
  supportsHydration: false,
  isPrimaryRenderer: false,
  supportsMicrotasks: true,
  scheduleMicrotask: queueMicrotask,
  createInstance: (type: string, props: any): HostNode => node(type, props),
  createTextInstance: (text: string): HostNode => ({ ...node("#text", {}), text }),
  appendInitialChild: (p: HostNode, c: HostNode) => void p.children.push(c),
  appendChild: (p: HostNode, c: HostNode) => void p.children.push(c),
  appendChildToContainer: (p: HostNode, c: HostNode) => void p.children.push(c),
  insertBefore: (p: HostNode, c: HostNode, b: HostNode) => void p.children.splice(at(p, b), 0, c),
  insertInContainerBefore: (p: HostNode, c: HostNode, b: HostNode) => void p.children.splice(at(p, b), 0, c),
  removeChild: (p: HostNode, c: HostNode) => void p.children.splice(at(p, c), 1),
  removeChildFromContainer: (p: HostNode, c: HostNode) => void p.children.splice(at(p, c), 1),
  finalizeInitialChildren: () => false,
  prepareUpdate: () => true,
  commitUpdate: (n: HostNode, _u: unknown, _t: string, _o: unknown, props: any) => {
    n.props = props;
    n.value = props?.value;
  },
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

const all = (n: HostNode, pred: (x: HostNode) => boolean, out: HostNode[] = []): HostNode[] => {
  if (pred(n)) out.push(n);
  n.children.forEach((c) => all(c, pred, out));
  return out;
};
const one = (n: HostNode, pred: (x: HostNode) => boolean) => all(n, pred)[0];
const textOf = (n: HostNode): string => (n.text ?? "") + n.children.map(textOf).join("");
const cls = (c: string) => (x: HostNode) => typeof x.props?.className === "string" && x.props.className.split(" ").includes(c);
const tick = () => new Promise((r) => setTimeout(r, 0));
async function until(ok: () => boolean, what: string) {
  for (let i = 0; i < 200; i++) {
    if (ok()) return;
    await tick();
  }
  throw new Error(`timed out waiting for ${what}`);
}

let fake: ImageContractFake;
const origXHR = (globalThis as any).XMLHttpRequest;
let r: any;
let root: any;
let container: HostNode;

async function mount() {
  if (!r) {
    const pkg = "react-reconciler"; // no types installed: loaded by name
    const Reconciler = (await import(/* @vite-ignore */ pkg)).default;
    r = Reconciler(host); // one renderer for the file (a new one per test trips React's "multiple renderers" warning)
  }
  container = node("root", {});
  root = r.createContainer(container, 0, null, false, null, "", console.error, null);
  r.updateContainer(createElement(MemoryRouter, null, createElement(Composer, { placeholder: "Describe what you want to make..." })), root, null, null);
  await tick();
}

beforeEach(async () => {
  h.posts = [];
  fake = new ImageContractFake();
  fake.addProject("p_1");
  FakeXHR.fake = fake;
  FakeXHR.sent = [];
  FakeXHR.net = false;
  FakeXHR.authed = true;
  (globalThis as any).XMLHttpRequest = FakeXHR;
  useProject.getState().reset();
  useProject.setState({
    ctx: { pid: "p_1" },
    project: { id: "p_1", name: "Untitled gadget", created: "", updated: "", stage: "clarify", mode: "fast", chat: [], concepts: [], versions: [] } as any,
  });
  vi.spyOn(api, "project").mockImplementation(async () => structuredClone(useProject.getState().project!) as any);
  await mount();
});
afterEach(() => {
  r?.updateContainer(null, root, null, null);
  (globalThis as any).XMLHttpRequest = origXHR;
  vi.restoreAllMocks();
});

const textarea = () => one(container, (x) => x.type === "textarea");
const form = () => one(container, (x) => x.type === "form");
const sendBtn = () => one(container, cls("send-btn"));

describe("composer with images", () => {
  it("pastes a screenshot, uploads it, and sends it alone as an image message", async () => {
    const prevent = vi.fn();
    const shot = new File([pngBytes(1440, 900)], "image.png", { type: "image/png" });
    textarea().props.onPaste({ clipboardData: { files: [shot], items: [], types: ["Files"] }, preventDefault: prevent });
    expect(prevent).toHaveBeenCalled(); // nothing else to paste: the image is the paste
    await until(() => all(container, cls("img-draft")).length === 1, "the thumbnail");
    expect(sendBtn().props.disabled).toBe(true); // still uploading
    await until(() => !!one(container, (x) => cls("img-draft")(x) && cls("done")(x)), "the upload");
    expect(FakeXHR.sent[0]).toMatchObject({ url: "/api/projects/p_1/uploads", field: "file" });
    expect(FakeXHR.sent[0].name).toMatch(/^pasted-image-\d{6}\.png$/);
    expect(sendBtn().props.disabled).toBe(false); // no words needed
    expect(textarea().props.placeholder).toBe("Add a line about it (optional)");
    form().props.onSubmit({ preventDefault: () => {} });
    await until(() => h.posts.length === 1, "the chat post");
    const id = [...fake.uploads.keys()][0];
    expect(h.posts[0]).toEqual({ mode: "fast", follow_build: false, text: "", attachments: [{ id }] });
    expect(fake.chat("p_1", h.posts[0]).body.text).toBe("Here is what I have in mind."); // the contract's stand-in words
    await until(() => all(container, cls("img-draft")).length === 0, "the composer to clear");
  });

  it("keeps pasted words: an image pasted together with text does not swallow the text", async () => {
    const prevent = vi.fn();
    const pic = new File([jpegBytes()], "photo.jpg", { type: "image/jpeg" });
    textarea().props.onPaste({ clipboardData: { files: [pic], items: [], types: ["text/plain", "Files"] }, preventDefault: prevent });
    expect(prevent).not.toHaveBeenCalled();
    await until(() => all(container, cls("img-draft")).length === 1, "the thumbnail");
    textarea().props.onPaste({ clipboardData: { files: [], items: [], types: ["text/plain"] }, preventDefault: prevent });
    expect(prevent).not.toHaveBeenCalled(); // plain text pastes as always
  });

  it("drops photos and a spec together: images upload, the text file joins the words, both go in one message", async () => {
    const prevent = vi.fn();
    const files = [new File([jpegBytes()], "front.jpg", { type: "image/jpeg" }), new File(["Size: 10 cm"], "spec.md", { type: "text/markdown" })];
    form().props.onDrop({ dataTransfer: { files, types: ["Files"] }, preventDefault: prevent });
    expect(prevent).toHaveBeenCalled();
    await until(() => !!one(container, (x) => cls("img-draft")(x) && cls("done")(x)) && textOf(container).includes("spec.md"), "the image and the file chip");
    textarea().props.onChange({ target: { value: "Make it smaller" } });
    await tick();
    form().props.onSubmit({ preventDefault: () => {} });
    await until(() => h.posts.length === 1, "the chat post");
    expect(h.posts[0].text).toBe("Make it smaller\n\nAttached file spec.md:\n```\nSize: 10 cm\n```");
    expect(h.posts[0].attachments).toHaveLength(1);
  });

  it("refuses a GIF and a fifth image with friendly words, and shows the server's reason for a refused upload", async () => {
    form().props.onDrop({ dataTransfer: { files: [new File([gifBytes()], "anim.gif", { type: "image/gif" })], types: ["Files"] }, preventDefault: () => {} });
    await until(() => textOf(container).includes("GIF images are not supported"), "the GIF error");
    const five = [1, 2, 3, 4, 5].map((i) => new File([jpegBytes()], `p${i}.jpg`, { type: "image/jpeg" }));
    form().props.onDrop({ dataTransfer: { files: five, types: ["Files"] }, preventDefault: () => {} });
    await until(() => textOf(container).includes("Up to 4 images per message: p5.jpg was not added."), "the limit error");
    expect(all(container, cls("img-draft"))).toHaveLength(4);
    await until(() => all(container, (x) => cls("img-draft")(x) && cls("done")(x)).length === 4, "four uploads");
    // a file called .jpg that is not an image: the server sniffs it and says no
    for (const b of all(container, cls("img-draft-x"))) b.props.onClick();
    form().props.onDrop({ dataTransfer: { files: [new File([gifBytes()], "renamed.jpg", { type: "image/jpeg" })], types: ["Files"] }, preventDefault: () => {} });
    await until(() => !!one(container, (x) => cls("img-draft")(x) && cls("error")(x)), "the refused upload");
    expect(textOf(container)).toContain("renamed.jpg: That file is not a JPEG, PNG, WebP or HEIC image.");
    expect(sendBtn().props.disabled).toBe(true);
  });
});
