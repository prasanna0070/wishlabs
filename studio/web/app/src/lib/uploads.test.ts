// Start from an image: which files the composers take, paste and drop handling, the chat body (an image alone is a
// message), the share gate, the first message carried from Home, the upload queue, and the upload call itself
// against an in-memory fake of the contract (imageContract.fake.ts).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  altFromName,
  chatBody,
  clipboardHasText,
  clipboardImages,
  dragHasFiles,
  hasImages,
  hiddenImages,
  IMAGE_MAX_BYTES,
  imageProblem,
  ImageQueue,
  imageRoom,
  imagesShown,
  isSupportedImage,
  looksLikeImage,
  messageAttachments,
  pastedName,
  projectImages,
  savePending,
  sortFiles,
  storedImageCount,
  takePending,
  uploadUrl,
} from "./uploads";
import { FakeXHR, gifBytes, heicBytes, ImageContractFake, jpegBytes, MB, pngBytes, sniff } from "./imageContract.fake";
import { api, ApiError } from "./api";
import { useAuth } from "../store/auth";
import type { Upload } from "./types";

const f = (name: string, type: string, size = 1000) => ({ name, type, size });
const file = (bytes: Uint8Array, name: string, type: string) => new File([bytes], name, { type });

describe("which files are images we take", () => {
  it("takes JPEG, PNG, WebP and HEIC by type, and HEIC with no type by its extension", () => {
    for (const t of ["image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"]) expect(isSupportedImage(f("x", t))).toBe(true);
    expect(isSupportedImage(f("IMG_0042.HEIC", ""))).toBe(true); // Windows gives HEIC no type
    expect(isSupportedImage(f("photo.jpg", "application/octet-stream"))).toBe(true);
    expect(isSupportedImage(f("anim.gif", "image/gif"))).toBe(false);
    expect(isSupportedImage(f("notes.txt", "text/plain"))).toBe(false);
  });

  it("explains a refusal in plain words", () => {
    expect(imageProblem(f("anim.gif", "image/gif"))).toMatch(/anim\.gif: GIF images are not supported\. Use a JPEG, PNG, WebP or HEIC/);
    expect(imageProblem(f("logo.svg", "image/svg+xml"))).toMatch(/SVG images are not supported/);
    expect(imageProblem(f("big.png", "image/png", IMAGE_MAX_BYTES + 1))).toMatch(/big\.png is larger than 15 MB/);
    expect(imageProblem(f("empty.png", "image/png", 0))).toMatch(/empty/);
    expect(imageProblem(f("ok.png", "image/png", IMAGE_MAX_BYTES))).toBeNull();
  });

  it("sorts a mixed pick into images, text files and errors, and keeps to 4 an message", () => {
    const list = [f("a.png", "image/png"), f("spec.md", "text/markdown"), f("b.jpg", "image/jpeg"), f("c.gif", "image/gif"), f("d.webp", "image/webp")];
    const r = sortFiles(list, 2);
    expect(r.images.map((x) => x.name)).toEqual(["a.png", "b.jpg"]);
    expect(r.texts.map((x) => x.name)).toEqual(["spec.md"]);
    expect(r.errors[0]).toMatch(/GIF/);
    expect(r.errors[1]).toBe("Up to 4 images per message: d.webp was not added.");
    expect(sortFiles([f("e.png", "image/png")], 0, true).errors[0]).toBe("A project can hold up to 12 images: e.png was not added.");
  });

  it("works out the room left: 4 a message, 12 a project", () => {
    expect(imageRoom(0, 0)).toEqual({ room: 4, projectFull: false });
    expect(imageRoom(3, 0)).toEqual({ room: 1, projectFull: false });
    expect(imageRoom(0, 10)).toEqual({ room: 2, projectFull: true });
    expect(imageRoom(1, 11)).toEqual({ room: 0, projectFull: true });
  });

  it("knows an image from other files", () => {
    expect(looksLikeImage(f("x.HEIC", ""))).toBe(true);
    expect(looksLikeImage(f("x.gif", ""))).toBe(true);
    expect(looksLikeImage(f("x.csv", "text/csv"))).toBe(false);
  });
});

describe("paste and drop", () => {
  const now = new Date(2026, 10, 20, 15, 4, 9);
  it("takes the pasted image files and names a screenshot so it can be told apart", () => {
    const shot = new File([pngBytes()], "image.png", { type: "image/png" });
    const got = clipboardImages({ files: [shot], items: [], types: ["Files"] }, now);
    expect(got).toHaveLength(1);
    expect(got[0].name).toBe("pasted-image-150409.png");
    expect(got[0].type).toBe("image/png");
    expect(pastedName({ name: "concept.png", type: "image/png" }, now)).toBe("concept.png");
    expect(pastedName({ name: "", type: "image/jpeg" }, now)).toBe("pasted-image-150409.jpg");
  });

  it("falls back to the item list when the browser puts the image only there, and ignores pasted text", () => {
    const blob = new File([jpegBytes()], "", { type: "image/jpeg" });
    const items = [
      { kind: "string", type: "text/plain", getAsFile: () => null },
      { kind: "file", type: "image/jpeg", getAsFile: () => blob },
    ];
    expect(clipboardImages({ files: [], items }, now).map((x) => x.name)).toEqual(["pasted-image-150409.jpg"]);
    expect(clipboardImages({ files: [], items: [items[0]] }, now)).toEqual([]);
    expect(clipboardImages(null)).toEqual([]);
    expect(clipboardHasText({ types: ["text/plain", "Files"] })).toBe(true);
    expect(clipboardHasText({ types: ["Files"] })).toBe(false);
  });

  it("only treats a drag with files as a drop of images", () => {
    expect(dragHasFiles({ types: ["Files"] })).toBe(true);
    expect(dragHasFiles({ types: ["text/plain", "text/uri-list"] })).toBe(false);
    expect(dragHasFiles(null)).toBe(false);
  });
});

describe("the chat body", () => {
  it("sends images by id, with or without words; an image alone is a message", () => {
    expect(chatBody("A lamp", [], [])).toEqual({ text: "A lamp" });
    expect(chatBody("", [], [{ id: "u_aaaaaaaaaa" }])).toEqual({ text: "", attachments: [{ id: "u_aaaaaaaaaa" }] });
    expect(chatBody("  Make this ", ["Attached file a.md:\n```\nx\n```"], [{ id: "u_1" }, { id: "u_2" }])).toEqual({
      text: "Make this\n\nAttached file a.md:\n```\nx\n```",
      attachments: [{ id: "u_1" }, { id: "u_2" }],
    });
    expect(chatBody("   ", [], [])).toBeNull();
  });
});

describe("image URLs and the share gate", () => {
  const owner = { pid: "p_1" };
  const share = { pid: "p_1", share: "s_tok" };
  it("builds owner and share URLs from the id", () => {
    expect(uploadUrl(owner, "u_abc")).toBe("/api/projects/p_1/uploads/u_abc");
    expect(uploadUrl(owner, "u_abc", true)).toBe("/api/projects/p_1/uploads/u_abc?thumb=1");
    expect(uploadUrl(share, "u_abc", true)).toBe("/api/share/s_tok/uploads/u_abc?thumb=1");
  });

  it("shows raw images on a share link only when the owner turned share_uploads on (missing means off)", () => {
    expect(imagesShown(owner, { share_uploads: false })).toBe("yes");
    expect(imagesShown(share, { share_uploads: false })).toBe("no");
    expect(imagesShown(share, { share_uploads: true })).toBe("yes");
    expect(imagesShown(share, {})).toBe("no");
  });

  it("reads the hidden-image count a share link gives instead of the attachments", () => {
    expect(hiddenImages({ attachments_hidden: 2 })).toBe(2);
    expect(hiddenImages({ attachments_hidden: 0 })).toBe(0);
    expect(hiddenImages({})).toBe(0);
    expect(hiddenImages({ attachments_hidden: "x" as any })).toBe(0);
  });

  it("counts the project's stored images without the composer's own (12 a project)", () => {
    const chat: any[] = [{ id: "m_1", role: "user", kind: "text", text: "", payload: null, ts: "", attachments: [{ id: "u_1" }, { id: "u_2" }] }];
    const uploads: any[] = ["u_1", "u_2", "u_3", "u_4"].map((id) => ({ id }));
    expect(storedImageCount({ chat })).toBe(2);
    expect(storedImageCount({ chat, uploads })).toBe(4); // u_3, u_4 uploaded but never sent still take a slot
    expect(storedImageCount({ chat, uploads }, ["u_4"])).toBe(3);
  });

  it("reads a message's images and the project's, once each", () => {
    const a = { id: "u_1", name: "sheet.png" };
    const chat: any[] = [
      { id: "m_1", role: "user", kind: "text", text: "x", payload: null, ts: "", attachments: [a] },
      { id: "m_2", role: "assistant", kind: "text", text: "y", payload: null, ts: "", attachments: [{ id: "u_9" }] },
      { id: "m_3", role: "user", kind: "text", text: "z", payload: { attachments: [a, { id: "u_2" }, { nope: 1 }] }, ts: "" },
    ];
    expect(messageAttachments(chat[0])).toEqual([a]);
    expect(projectImages({ chat }).map((x) => x.id)).toEqual(["u_1", "u_2"]);
    expect(hasImages({ chat })).toBe(true);
    expect(hasImages({ chat: [], image_brief: null })).toBe(false);
    expect(hasImages({ chat: [], image_brief: { title: "x" } })).toBe(true);
  });

  it("makes alt text from a file name", () => {
    expect(altFromName("acme-concept_sheet.PNG")).toBe("acme concept sheet");
    expect(altFromName("")).toBe("Your image");
  });
});

describe("the first message from Home", () => {
  const store = () => {
    const m = new Map<string, string>();
    return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k), m };
  };
  it("carries words and images to the project page once", () => {
    const s = store();
    expect(savePending("p_1", "", [{ id: "u_1", name: "sheet.png", mime: "image/jpeg", width: 1200, height: 1600 }], s)).toBe(true);
    expect(takePending("p_1", s)).toEqual({ text: "", attachments: [{ id: "u_1" }] });
    expect(takePending("p_1", s)).toBeNull();
    expect(s.m.size).toBe(0);
  });

  it("still reads the plain words Home and the voice chat always left", () => {
    const s = store();
    s.setItem("gs.pending.p_2", "A cloud lamp");
    expect(takePending("p_2", s)).toEqual({ text: "A cloud lamp" });
    s.setItem("gs.pending.att.p_3", "{not json");
    expect(takePending("p_3", s)).toBeNull();
  });
});

describe("the upload queue", () => {
  let fake: ImageContractFake;
  const upload = vi.fn(async (pid: string, blob: Blob, name: string, opts: { onProgress?: (n: number) => void; signal?: AbortSignal }) => {
    opts.onProgress?.(0.5);
    await new Promise((r) => setTimeout(r, 0));
    if (opts.signal?.aborted) throw new ApiError(0, "aborted", "Upload cancelled.");
    const r = fake.upload(pid, new Uint8Array(await blob.arrayBuffer()), name);
    if (r.status !== 201) throw new ApiError(r.status, r.body.error.code, `${name}: ${r.body.error.message}`);
    return r.body as Upload;
  });
  beforeEach(() => {
    fake = new ImageContractFake();
    fake.addProject("p_1");
    upload.mockClear();
  });

  it("waits for the project, then uploads one at a time and hands back every upload", async () => {
    const q = new ImageQueue({ upload });
    const seen: string[] = [];
    q.subscribe(() => seen.push(q.items.map((x) => `${x.name}:${x.state}`).join(",")));
    q.add([file(pngBytes(), "sheet.png", "image/png"), file(jpegBytes(), "photo.jpg", "image/jpeg")]);
    await new Promise((r) => setTimeout(r, 5));
    expect(upload).not.toHaveBeenCalled(); // no project yet (the home page before Start)
    const done = q.whenSettled();
    q.setProject("p_1");
    const ups = await done;
    expect(ups.map((u) => u.name)).toEqual(["sheet.png", "photo.jpg"]);
    expect(ups[0]).toMatchObject({ mime: "image/jpeg", width: 1200, height: 1600, url: `/api/projects/p_1/uploads/${ups[0].id}`, thumb_url: `/api/projects/p_1/uploads/${ups[0].id}?thumb=1` });
    expect(ups[0].id).toMatch(/^u_[a-z0-9]{10}$/);
    expect(seen).toContain("sheet.png:uploading,photo.jpg:waiting"); // never two at once
    expect(q.busy).toBe(false);
  });

  it("marks a refused image with the server's words, lets it be retried or removed", async () => {
    const q = new ImageQueue({ upload });
    q.setProject("p_1");
    q.add([file(heicBytes(), "IMG_1.HEIC", "image/heic")]);
    await expect(q.whenSettled()).rejects.toThrow("IMG_1.HEIC: HEIC is not supported yet, please export as JPEG");
    expect(q.items[0].state).toBe("error");
    fake.heicSupported = true;
    q.retry(q.items[0].key);
    expect((await q.whenSettled())[0].name).toBe("IMG_1.HEIC");
    q.remove(q.items[0].key);
    expect(q.items).toEqual([]);
  });

  it("deletes an uploaded image the user removes before sending, but not the ones a sent message took", async () => {
    const discard = vi.fn();
    const q = new ImageQueue({ upload, discard });
    q.setProject("p_1");
    q.add([file(pngBytes(), "a.png", "image/png"), file(pngBytes(), "b.png", "image/png")]);
    const ups = await q.whenSettled();
    q.remove(q.items[0].key);
    expect(discard).toHaveBeenCalledWith("p_1", ups[0].id);
    q.clear(); // after a send: the message carries b.png now
    expect(discard).toHaveBeenCalledTimes(1);
  });

  it("cancels the upload of an image removed while it goes up", async () => {
    const q = new ImageQueue({ upload });
    q.setProject("p_1");
    q.add([file(pngBytes(), "a.png", "image/png"), file(pngBytes(), "b.png", "image/png")]);
    q.remove(q.items[0].key);
    const ups = await q.whenSettled();
    expect(ups.map((u) => u.name)).toEqual(["b.png"]);
  });

  it("puts back images that were uploaded before (a message the studio did not take)", () => {
    const q = new ImageQueue({ upload });
    q.restore([{ id: "u_1", name: "sheet.png", thumb_url: "/api/projects/p_1/uploads/u_1?thumb=1" }]);
    expect(q.items[0]).toMatchObject({ state: "done", preview: "/api/projects/p_1/uploads/u_1?thumb=1", file: null });
    expect(q.uploads().map((u) => u.id)).toEqual(["u_1"]);
  });
});

describe("api.uploadImage against the contract", () => {
  let fake: ImageContractFake;
  const orig = (globalThis as any).XMLHttpRequest;
  beforeEach(() => {
    fake = new ImageContractFake();
    fake.addProject("p_1");
    FakeXHR.fake = fake;
    FakeXHR.sent = [];
    FakeXHR.authed = true;
    FakeXHR.net = false;
    (globalThis as any).XMLHttpRequest = FakeXHR;
  });
  afterEach(() => {
    (globalThis as any).XMLHttpRequest = orig;
  });

  it("posts multipart field `file` and resolves with the 201 body, reporting progress", async () => {
    const progress: number[] = [];
    const up = await api.uploadImage("p_1", file(pngBytes(800, 600), "sketch.png", "image/png"), "sketch.png", { onProgress: (n) => progress.push(n) });
    expect(FakeXHR.sent[0]).toMatchObject({ url: "/api/projects/p_1/uploads", field: "file", name: "sketch.png" });
    expect(up).toMatchObject({ name: "sketch.png", mime: "image/jpeg", width: 800, height: 600 });
    expect(progress.length).toBeGreaterThanOrEqual(3);
    expect(progress[progress.length - 1]).toBe(1);
    expect(progress.slice(0, -1).every((n) => n > 0 && n < 1)).toBe(true);
  });

  it("sniffs bytes, not names: a GIF called .png is refused with a friendly 415", async () => {
    expect(sniff(gifBytes())).toBeNull();
    const e = await api.uploadImage("p_1", file(gifBytes(), "fake.png", "image/png"), "fake.png").catch((x) => x);
    expect(e).toBeInstanceOf(ApiError);
    expect(e.status).toBe(415);
    expect(e.message).toBe("fake.png: That file is not a JPEG, PNG, WebP or HEIC image.");
  });

  it("passes on 413 (over 15 MB) and 409 (12 a project) with the server's sentence", async () => {
    const big = new Uint8Array(15 * MB + 10);
    big.set(jpegBytes());
    const e1 = await api.uploadImage("p_1", new File([big], "huge.jpg", { type: "image/jpeg" }), "huge.jpg").catch((x) => x);
    expect([e1.status, e1.message]).toEqual([413, "huge.jpg: That image is larger than 15 MB. Please export a smaller JPEG."]);
    for (let i = 0; i < 12; i++) await api.uploadImage("p_1", file(jpegBytes(), `p${i}.jpg`, "image/jpeg"), `p${i}.jpg`);
    const e2 = await api.uploadImage("p_1", file(jpegBytes(), "13.jpg", "image/jpeg"), "13.jpg").catch((x) => x);
    expect([e2.status, e2.message]).toEqual([409, "13.jpg: This project already has 12 images, the most it can hold."]);
  });

  it("says plainly when the connection dropped, and sends a signed-out user to sign in", async () => {
    FakeXHR.net = true;
    const e = await api.uploadImage("p_1", file(jpegBytes(), "a.jpg", "image/jpeg"), "a.jpg").catch((x) => x);
    expect([e.status, e.code]).toEqual([0, "network"]);
    expect(e.message).toMatch(/a\.jpg did not upload: the connection dropped/);
    useAuth.setState({ authed: true } as any);
    FakeXHR.authed = false;
    const e2 = await api.uploadImage("p_1", file(jpegBytes(), "a.jpg", "image/jpeg"), "a.jpg").catch((x) => x);
    expect(e2.status).toBe(401);
    expect((useAuth.getState() as any).authed).toBe(false);
  });

  it("stops when cancelled", async () => {
    const ctl = new AbortController();
    const p = api.uploadImage("p_1", file(jpegBytes(), "a.jpg", "image/jpeg"), "a.jpg", { signal: ctl.signal });
    ctl.abort();
    await expect(p).rejects.toMatchObject({ code: "aborted" });
  });
});

describe("the fake follows the contract for reads, sharing and chat", () => {
  it("serves images to the owner, and to a share link only with share_uploads on", () => {
    const fake = new ImageContractFake();
    fake.addProject("p_1", "s_tok");
    const up = fake.upload("p_1", jpegBytes(), "a.jpg").body as Upload;
    expect(fake.get(`/api/projects/p_1/uploads/${up.id}?thumb=1`).status).toBe(200);
    expect(fake.get(`/api/share/s_tok/uploads/${up.id}`).status).toBe(404);
    expect(fake.patch("p_1", { share_uploads: true }).body.share_uploads).toBe(true);
    expect(fake.get(`/api/share/s_tok/uploads/${up.id}?thumb=1`).status).toBe(404); // shared, but not sent yet
    expect(fake.chat("p_1", { text: "", attachments: [{ id: up.id }] }).status).toBe(200);
    expect(fake.get(`/api/share/s_tok/uploads/${up.id}?thumb=1`).status).toBe(200);
  });

  it("stores an image-only message with the stand-in words and checks the ids", () => {
    const fake = new ImageContractFake();
    fake.addProject("p_1");
    fake.addProject("p_2");
    const up = fake.upload("p_1", jpegBytes(), "a.jpg").body as Upload;
    const msg = fake.chat("p_1", chatBody("", [], [up])!).body;
    expect(msg).toMatchObject({ text: "Here is what I have in mind.", attachments: [{ id: up.id, name: "a.jpg", mime: "image/jpeg" }] });
    expect(fake.chat("p_2", { text: "", attachments: [{ id: up.id }] }).status).toBe(422);
  });
});
