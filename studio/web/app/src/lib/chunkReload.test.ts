import { describe, expect, it } from "vitest";
import { isChunkError, reloadOnce } from "./chunkReload";

function mem() {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) };
}

describe("reload after a deploy", () => {
  it("recognises the stale-file errors of each browser", () => {
    expect(isChunkError(new TypeError("Failed to fetch dynamically imported module: https://x/studio/assets/OverviewTab-abc.js"))).toBe(true);
    expect(isChunkError(new TypeError("Importing a module script failed."))).toBe(true);
    expect(isChunkError(new TypeError("error loading dynamically imported module"))).toBe(true);
    expect(isChunkError(new Error("Unable to preload CSS for /studio/assets/x.css"))).toBe(true);
    expect(isChunkError(new Error("Cannot read properties of undefined"))).toBe(false);
    expect(isChunkError(null)).toBe(false);
  });
  it("reloads once a minute at most, so an outage cannot loop", () => {
    const s = mem();
    let n = 0;
    const reload = () => void (n += 1);
    expect(reloadOnce(1_000_000, s, reload)).toBe(true);
    expect(reloadOnce(1_030_000, s, reload)).toBe(false);
    expect(reloadOnce(1_070_000, s, reload)).toBe(true);
    expect(n).toBe(2);
  });
});
