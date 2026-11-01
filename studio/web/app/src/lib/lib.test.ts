import { describe, expect, it } from "vitest";
import { SSEParser, parseBlock } from "./sse";
import { diffMergePatch, endLists, humanPart, inr, inrRange, dims, softBreaks } from "./format";
import { fileBase, rewriteShareUrl } from "./urls";
import { parseSerialLine } from "./serial";

describe("SSE parser", () => {
  it("splits blocks across chunks and ignores pings", () => {
    const p = new SSEParser();
    expect(p.push(": ping\n\nevent: turn.start\ndata: {\"seq\":1")).toEqual([]);
    const out = p.push("}\n\nevent: text.delta\ndata: {\"seq\":2,\"delta\":\"Hi\"}\n\n");
    expect(out).toEqual([
      { event: "turn.start", data: { seq: 1 } },
      { event: "text.delta", data: { seq: 2, delta: "Hi" } },
    ]);
  });
  it("handles CRLF and multi-line data", () => {
    const p = new SSEParser();
    expect(p.push('event: x\r\ndata: {"a":\r\ndata: 1}\r\n\r\n')).toEqual([{ event: "x", data: { a: 1 } }]);
  });
  it("returns null for comment-only blocks", () => {
    expect(parseBlock(": keepalive")).toBeNull();
  });
});

describe("format", () => {
  it("formats INR in the Indian system", () => {
    expect(inr(649999)).toBe("₹6,49,999");
    expect(inrRange(120, 180)).toBe("₹120–180");
    expect(inrRange(150, 150)).toBe("₹150");
  });
  it("humanises component names", () => {
    expect(humanPart("PRINT_RearCover")).toBe("Rear cover");
    expect(humanPart("PURCHASE_CoreBoard")).toBe("Core board");
    expect(humanPart("PURCHASE_USBSpeaker")).toBe("USB speaker");
  });
  it("formats dims", () => {
    expect(dims(180, 120.04, 200)).toBe("180 × 120 × 200 mm");
  });
  it("ends a list before a plain line", () => {
    expect(endLists("Needed:\n- a\n- b\nPlus 3 more.")).toBe("Needed:\n- a\n- b\n\nPlus 3 more.");
    expect(endLists("- a\n  continued")).toBe("- a\n  continued");
  });
  it("keeps step lines apart", () => {
    expect(softBreaks("**Use:** a\n**Tools:** b\n\n1. go\n2. stop")).toBe("**Use:** a  \n**Tools:** b\n\n1. go\n2. stop");
  });
  it("builds a merge patch", () => {
    const base = { a: 1, b: { c: 2, d: 3 }, e: [1, 2], f: 5 };
    const edited = { a: 1, b: { c: 4, d: 3 }, e: [1], g: 7 };
    expect(diffMergePatch(base, edited)).toEqual({ b: { c: 4 }, e: [1], f: null, g: 7 });
  });
});

describe("urls", () => {
  it("builds version file bases for owner and share views", () => {
    expect(fileBase({ pid: "p_1" }, 3)).toBe("/api/projects/p_1/v/3/files/");
    expect(fileBase({ pid: "p_1", share: "s_x" }, 3)).toBe("/api/share/s_x/v/3/files/");
  });
  it("rewrites project asset URLs for share links", () => {
    expect(rewriteShareUrl("/api/projects/p_1/concepts/c_1/photo.png", "s_x")).toBe("/api/share/s_x/concepts/c_1/photo.png");
    expect(rewriteShareUrl("/api/share/s_x/concepts/c_1/photo.png", "s_x")).toBe("/api/share/s_x/concepts/c_1/photo.png");
  });
});

describe("serial self-test lines", () => {
  it("parses GS lines", () => {
    expect(parseSerialLine('GS-TEST {"id":"sd","status":"pass","detail":{"mb":32}}')).toEqual({
      tag: "GS-TEST",
      data: { id: "sd", status: "pass", detail: { mb: 32 } },
    });
    expect(parseSerialLine("hello")).toBeNull();
  });
});
