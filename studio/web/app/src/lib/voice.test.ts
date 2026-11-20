import { describe, expect, it } from "vitest";
import { Downsampler, clock, floatToPcm16, hasVoiceWish, level, liveModelName, liveSocketUrl, pcm16ToFloat, voiceOf, voiceUrl } from "./voice";
import type { Project } from "./types";
import { CONNECT_TIMEOUT_MS, MAX_BUFFERED, roomToSend } from "./voiceCall";

const base = { id: "p_x", name: "Moon light", created: "", updated: "", stage: "concepts", chat: [], concepts: [], versions: [] } as unknown as Project;

describe("voice record", () => {
  it("is found only when the project carries a spoken wish", () => {
    expect(voiceOf(base)).toBeNull();
    expect(hasVoiceWish(base)).toBe(false);
    expect(voiceOf({ ...base, voice: { turns: [] } } as Project)).toBeNull();
    const p = { ...base, voice: { wish: "a moon light", turns: [{ role: "user", text: "a moon light", t: 1 }, { role: "assistant", text: " ", t: 2 }] } } as Project;
    expect(hasVoiceWish(p, null)).toBe(true);
    expect(voiceOf(p)!.turns).toHaveLength(1); // blank caption turns are dropped
  });

  it("sends stored files through the share link on a shared view", () => {
    expect(voiceUrl("/api/projects/p_x/voice/wish.wav", "s_tok")).toBe("/api/share/s_tok/voice/wish.wav");
    expect(voiceUrl("/api/share/s_tok/voice/wish.wav", "s_tok")).toBe("/api/share/s_tok/voice/wish.wav");
    expect(voiceUrl("/api/projects/p_x/voice/wish.wav", null)).toBe("/api/projects/p_x/voice/wish.wav");
    expect(voiceUrl(null, "s_tok")).toBeNull();
  });

  it("names the Live model and formats times", () => {
    expect(liveModelName("gemini-live-2.5-flash-native-audio")).toBe("Gemini Live 2.5 Flash (native audio)");
    expect(liveModelName("gemini-3.8-live")).toBe("Gemini 3.8 Live");
    expect(liveModelName(undefined)).toBe("Gemini Live");
    expect(clock(83)).toBe("1:23");
    expect(clock(5.4)).toBe("0:05");
    expect(liveSocketUrl({ protocol: "https:", host: "wish.plugbook.si" })).toBe("wss://wish.plugbook.si/api/voice/live");
    expect(liveSocketUrl({ protocol: "http:", host: "127.0.0.1:9201" })).toBe("ws://127.0.0.1:9201/api/voice/live");
  });
});

describe("audio helpers", () => {
  it("downsamples 48 kHz to 16 kHz across chunk boundaries", () => {
    const d = new Downsampler(48000, 16000);
    const chunk = new Float32Array(2048).fill(0.5);
    let n = 0;
    for (let i = 0; i < 30; i++) n += d.push(chunk).length;
    expect(Math.abs(n - (30 * 2048) / 3)).toBeLessThanOrEqual(1);
    expect(d.push(new Float32Array(300).fill(0.5))[0]).toBe(Math.round(0.5 * 0x7fff));
  });

  it("downsamples 44.1 kHz to about the right length", () => {
    const d = new Downsampler(44100, 16000);
    let n = 0;
    for (let i = 0; i < 100; i++) n += d.push(new Float32Array(2048)).length;
    expect(Math.abs(n - (100 * 2048 * 16000) / 44100)).toBeLessThan(100);
  });

  it("round-trips 16-bit PCM and clips out-of-range samples", () => {
    const pcm = floatToPcm16(new Float32Array([0, 0.5, -0.5, 1.5, -1.5]));
    expect(Array.from(pcm)).toEqual([0, 16384, -16384, 32767, -32768]);
    const back = pcm16ToFloat(pcm.buffer as ArrayBuffer);
    expect(back[1]).toBeCloseTo(0.5, 3);
    expect(back[4]).toBe(-1);
  });

  it("measures level between 0 and 1", () => {
    expect(level(new Float32Array(100))).toBe(0);
    expect(level(new Float32Array(100).fill(1))).toBe(1);
    const v = level(new Float32Array(100).fill(0.05));
    expect(v).toBeGreaterThan(0.3);
    expect(v).toBeLessThan(0.6);
  });
});

describe("voice call on a slow uplink", () => {
  it("drops microphone frames once about 2 s of audio is waiting in the socket", () => {
    expect(MAX_BUFFERED).toBe(64_000);
    expect(roomToSend(0)).toBe(true);
    expect(roomToSend(MAX_BUFFERED)).toBe(true);
    expect(roomToSend(MAX_BUFFERED + 1)).toBe(false);
    expect(roomToSend(undefined)).toBe(true);
  });
  it("gives up on a stalled connection instead of waiting forever", () => {
    expect(CONNECT_TIMEOUT_MS).toBeGreaterThanOrEqual(10_000);
    expect(CONNECT_TIMEOUT_MS).toBeLessThanOrEqual(20_000);
  });
});
