import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanWish, rememberWish, searchWithoutWish, storedWish, takeWish, wishFromSearch, WISH_MAX } from "./wish";
import { shareUrl, STUDIO_BASE } from "./urls";

function fakeStorage() {
  const m = new Map<string, string>();
  return {
    getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
    setItem: (k: string, v: string) => void m.set(k, String(v)),
    removeItem: (k: string) => void m.delete(k),
    clear: () => m.clear(),
    key: () => null,
    length: 0,
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("landing wish prefill", () => {
  it("decodes ?wish= and trims it", () => {
    expect(wishFromSearch("?wish=%20a%20cloud%20lamp%20that%20glows%20blue%20")).toBe("a cloud lamp that glows blue");
    expect(wishFromSearch("?wish=a+heart+that+glows")).toBe("a heart that glows");
    expect(wishFromSearch("?other=1")).toBe("");
    expect(wishFromSearch("")).toBe("");
  });
  it("caps the wish at 500 characters without splitting an emoji", () => {
    expect(WISH_MAX).toBe(500);
    expect(cleanWish("x".repeat(900))).toHaveLength(500);
    const w = cleanWish("🙂".repeat(600));
    expect(Array.from(w)).toHaveLength(500);
    expect(w.endsWith("\uD83D")).toBe(false);
  });
  it("drops control characters but keeps line breaks", () => {
    expect(cleanWish("a\u0000b\u0007c\r\nd\te")).toBe("abc\nd\te");
  });
  it("removes only the wish parameter from the query", () => {
    expect(searchWithoutWish("?wish=abc")).toBe("");
    expect(searchWithoutWish("?a=1&wish=abc&b=2")).toBe("?a=1&b=2");
  });
  it("keeps the wish through sign-in and hands it over once", () => {
    vi.stubGlobal("sessionStorage", fakeStorage());
    expect(rememberWish("?wish=a%20desk%20robot")).toBe("a desk robot");
    expect(storedWish()).toBe("a desk robot");
    // after sign-in the router state may have lost the query: storage still has it
    expect(takeWish("")).toBe("a desk robot");
    expect(takeWish("")).toBe("");
  });
  it("prefers the address bar over a stored wish", () => {
    vi.stubGlobal("sessionStorage", fakeStorage());
    rememberWish("?wish=old");
    expect(takeWish("?wish=new")).toBe("new");
    expect(storedWish()).toBe("");
  });
  it("works without sessionStorage", () => {
    vi.stubGlobal("sessionStorage", undefined);
    expect(rememberWish("?wish=lamp")).toBe("lamp");
    expect(storedWish()).toBe("");
    expect(takeWish("?wish=lamp")).toBe("lamp");
  });
});

describe("studio base", () => {
  it("mounts the SPA under /studio and builds share links there", () => {
    expect(STUDIO_BASE).toBe("/studio");
    expect(shareUrl("s_abc", "https://wish.plugbook.si")).toBe("https://wish.plugbook.si/studio/s/s_abc");
  });
});
