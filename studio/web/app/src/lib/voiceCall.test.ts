// VoiceCall's guards for a weak venue network and Safari, with the browser's audio, microphone and WebSocket faked:
// the call gives up when Gemini Live is not ready in time, microphone frames are dropped (not queued) while the
// socket is backed up, and the audio context made in the Talk click is the one the call uses.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CONNECT_TIMEOUT_MS, MAX_BUFFERED, VoiceCall, primeVoiceAudio, type FailKind } from "./voiceCall";

const node = () => ({ connect: vi.fn(), disconnect: vi.fn() });
let contexts: FakeAudioContext[] = [];
let sockets: FakeSocket[] = [];
let processor: any = null;

class FakeAudioContext {
  sampleRate = 48000;
  state = "suspended";
  currentTime = 0;
  destination = {};
  resumes = 0;
  constructor() {
    contexts.push(this);
  }
  resume() {
    this.resumes += 1;
    this.state = "running";
    return Promise.resolve();
  }
  close() {
    this.state = "closed";
    return Promise.resolve();
  }
  createMediaStreamSource() {
    return node();
  }
  createGain() {
    return { ...node(), gain: { value: 1 } };
  }
  createAnalyser() {
    return { ...node(), fftSize: 512, getFloatTimeDomainData: vi.fn() };
  }
  createScriptProcessor() {
    processor = { ...node(), onaudioprocess: null };
    return processor;
  }
}

class FakeSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  readyState = 0;
  bufferedAmount = 0;
  binaryType = "";
  sent: any[] = [];
  closed: number | null = null;
  onopen: any = null;
  onmessage: any = null;
  onclose: any = null;
  onerror: any = null;
  constructor(public url: string) {
    sockets.push(this);
  }
  send(d: any) {
    this.sent.push(d);
  }
  close(code: number) {
    this.closed = code;
    this.readyState = 3;
    this.onclose?.({});
  }
  open() {
    this.readyState = 1;
    this.onopen?.({});
  }
  event(obj: unknown) {
    this.onmessage?.({ data: JSON.stringify(obj) });
  }
}

function micChunk(samples = 4800) {
  // 100 ms at 48 kHz: one 16 kHz frame after downsampling
  processor.onaudioprocess({ inputBuffer: { getChannelData: () => new Float32Array(samples).fill(0.1) } });
}

async function started() {
  const fails: [FailKind, string][] = [];
  const call = new VoiceCall({ onEvent: () => {}, onLevel: () => {}, onFail: (k, m) => void fails.push([k, m]) });
  await call.start({ mode: "fast", keepAudio: false });
  return { call, fails, ws: sockets[sockets.length - 1] };
}

beforeEach(() => {
  contexts = [];
  sockets = [];
  processor = null;
  vi.useFakeTimers();
  vi.stubGlobal("window", { AudioContext: FakeAudioContext, setTimeout, clearTimeout, setInterval, clearInterval });
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: () => Promise.resolve({ getTracks: () => [] }) } });
  vi.stubGlobal("location", { protocol: "https:", host: "wish.example" });
  vi.stubGlobal("WebSocket", FakeSocket);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("VoiceCall on a weak network", () => {
  it("gives up once, with a message, when Gemini Live is not ready in time", async () => {
    const { fails, ws } = await started();
    expect(ws.url).toBe("wss://wish.example/api/voice/live");
    ws.open();
    vi.advanceTimersByTime(CONNECT_TIMEOUT_MS - 100);
    expect(fails).toEqual([]);
    vi.advanceTimersByTime(200);
    expect(fails.map((f) => f[0])).toEqual(["socket"]);
    expect(ws.closed).toBe(1000); // the socket is closed, so the server frees the slot
    vi.advanceTimersByTime(CONNECT_TIMEOUT_MS * 2);
    expect(fails).toHaveLength(1);
  });

  it("does not time out once ready, nor after the server already said why it stopped", async () => {
    const a = await started();
    a.ws.open();
    a.ws.event({ type: "ready", model: "m", max_seconds: 180, recording_allowed: true });
    vi.advanceTimersByTime(CONNECT_TIMEOUT_MS * 2);
    expect(a.fails).toEqual([]);
    a.call.close();

    const b = await started();
    b.ws.open();
    b.ws.event({ type: "error", code: "rate_limited", message: "limit" });
    vi.advanceTimersByTime(CONNECT_TIMEOUT_MS * 2);
    expect(b.fails).toEqual([]);
    b.call.close();
  });

  it("drops microphone frames while the socket is backed up, and sends again once it drains", async () => {
    const { call, ws } = await started();
    ws.open();
    ws.event({ type: "ready", model: "m", max_seconds: 180, recording_allowed: true });
    const binary = () => ws.sent.filter((d) => d instanceof ArrayBuffer).length;
    micChunk();
    expect(binary()).toBe(1);
    ws.bufferedAmount = MAX_BUFFERED + 1;
    micChunk();
    micChunk();
    expect(binary()).toBe(1);
    expect(call.dropped).toBe(2);
    ws.bufferedAmount = 0;
    micChunk();
    expect(binary()).toBe(2);
    call.close();
  });
});

describe("VoiceCall audio start (Safari)", () => {
  it("uses the audio context made in the Talk click, and resumes it once the microphone is live", async () => {
    primeVoiceAudio();
    expect(contexts).toHaveLength(1);
    const { call } = await started();
    expect(contexts).toHaveLength(1); // no second context outside the gesture
    expect(contexts[0].resumes).toBeGreaterThanOrEqual(1);
    call.close();
    expect(contexts[0].state).toBe("closed");
    await started(); // no primed context left: the call makes its own
    expect(contexts).toHaveLength(2);
  });
});
