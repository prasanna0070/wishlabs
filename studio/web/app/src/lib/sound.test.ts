import { describe, expect, it } from "vitest";
import { audioUrl, clock, hasSound, kb, khz, playable, soundOf, soundStatus, STALE_MS } from "./sound";
import type { Project } from "./types";

const cur = {
  id: "snd_abc123",
  version: 1,
  created: "2026-11-20T06:00:00Z",
  model: "lyria-002",
  prompt: "Instrumental signature theme",
  theme: { file: "theme.mp3", mime: "audio/mpeg", seconds: 30, bytes: 480000, url: "/api/projects/p_x/sound/snd_abc123/theme.mp3" },
  chime: { file: "chime.mp3", mime: "audio/mpeg", seconds: 2.4, bytes: 40000, url: "/api/projects/p_x/sound/snd_abc123/chime.mp3" },
};
const proj = (sound: any) => ({ id: "p_x", sound }) as unknown as Project;

describe("gadget sound", () => {
  it("finds a playable composition", () => {
    expect(hasSound(proj({ status: "ready", current: cur }))).toBe(true);
    expect(hasSound(proj({ status: "generating", job: { id: "snd_q", version: 1 } }))).toBe(false);
    expect(hasSound(proj(null))).toBe(false);
    expect(hasSound(null)).toBe(false);
    expect(playable(soundOf(proj({ current: { ...cur, theme: { ...cur.theme, url: "" } } })))).toBeNull();
  });
  it("reads a stale job as an error", () => {
    const now = Date.parse("2026-11-20T06:10:00Z");
    const job = (started: string) => ({ status: "generating", job: { id: "snd_q", version: 1, started } });
    expect(soundStatus(job("2026-11-20T06:09:30Z"), now)).toBe("generating");
    expect(soundStatus(job(new Date(now - STALE_MS - 1000).toISOString()), now)).toBe("error");
    expect(soundStatus({ status: "ready", current: cur as any }, now)).toBe("ready");
    expect(soundStatus(null, now)).toBe("none");
    expect(soundStatus({ status: "error", error: "x" }, now)).toBe("error");
  });
  it("routes share links through the share token", () => {
    expect(audioUrl(cur.theme.url, { pid: "p_x", share: "s_tok" })).toBe("/api/share/s_tok/sound/snd_abc123/theme.mp3");
    expect(audioUrl(cur.theme.url, { pid: "p_x" })).toBe(cur.theme.url);
  });
  it("formats times and sizes", () => {
    expect(clock(30)).toBe("0:30");
    expect(clock(75.4)).toBe("1:15");
    expect(kb(76844)).toBe("77 KB");
    expect(kb(2880044)).toBe("2.9 MB");
    expect(khz(44100)).toBe("44.1 kHz");
    expect(khz(48000)).toBe("48 kHz");
  });
});
