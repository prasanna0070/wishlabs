// One live voice call in the browser: the microphone (16 kHz PCM up) and Gemini Live's speech (24 kHz PCM down) over
// the studio's WebSocket /api/voice/live. The server holds the Vertex AI credentials and the limits.
import { Downsampler, LIVE_RATE, MIC_RATE, level, liveSocketUrl, pcm16ToFloat, type VoiceTurn } from "./voice";

export type VoiceEvent =
  | { type: "ready"; model: string; max_seconds: number; recording_allowed: boolean }
  | { type: "caption"; i: number; role: "user" | "assistant"; text: string; t: number }
  | { type: "turn_complete" }
  | { type: "interrupted" }
  | { type: "submitting" }
  | { type: "submitted"; project_id: string; name?: string; text: string; client_turn: boolean }
  | { type: "ending" }
  | { type: "time_left"; seconds: number }
  | { type: "error"; code: string; message: string }
  | { type: "ended"; reason: string; project_id: string | null; transcript: VoiceTurn[]; user_text: string };

export type FailKind = "mic_denied" | "mic_missing" | "unsupported" | "socket";

export interface VoiceHandlers {
  onEvent: (ev: VoiceEvent) => void;
  /** Microphone and speaker loudness, 0..1, about ten times a second. */
  onLevel: (mic: number, speaker: number) => void;
  onFail: (kind: FailKind, message: string) => void;
}

export interface StartOptions {
  mode: "pro" | "fast";
  keepAudio: boolean;
  text?: string; // a wish already typed in the box: Live starts from it
  projectId?: string; // talk into this still-empty project instead of a new one
}

// The microphone tap: forwards raw samples to the page in ~40 ms chunks (runs on the audio thread).
const TAP = `class GsMicTap extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Float32Array(2048); this.n = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) for (let i = 0; i < ch.length; i++) {
      this.buf[this.n++] = ch[i];
      if (this.n === this.buf.length) { this.port.postMessage(this.buf.slice(0)); this.n = 0; }
    }
    return true;
  }
}
registerProcessor("gs-mic-tap", GsMicTap);`;

const FRAME_SAMPLES = MIC_RATE / 10; // send 100 ms per WebSocket frame

export class VoiceCall {
  private ws: WebSocket | null = null;
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private nodes: AudioNode[] = [];
  private sources = new Set<AudioBufferSourceNode>();
  private playHead = 0;
  private pending: Int16Array[] = [];
  private pendingLen = 0;
  private ready = false;
  private closed = false;
  private micLevel = 0;
  private speakerAnalyser: AnalyserNode | null = null;
  private meterTimer: number | null = null;
  private down: Downsampler | null = null;
  private muted = false; // after "Done": the microphone stays open for the meter but nothing more is sent
  private final = false; // the server said how the call ended (ended or error): a close after that is expected

  constructor(private h: VoiceHandlers) {}

  /** Call from a click handler: the audio context must be created inside the user's gesture. */
  async start(opts: StartOptions): Promise<void> {
    const AC: typeof AudioContext | undefined = window.AudioContext || (window as any).webkitAudioContext;
    if (!AC || !navigator.mediaDevices?.getUserMedia || typeof WebSocket === "undefined") {
      this.h.onFail("unsupported", "This browser cannot do a voice call here (it needs a secure page and a microphone). Type your wish instead.");
      return;
    }
    const ctx = new AC();
    this.ctx = ctx;
    void ctx.resume().catch(() => {});
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (e: any) {
      const name = e?.name || "";
      if (name === "NotFoundError" || name === "OverconstrainedError")
        this.h.onFail("mic_missing", "No microphone was found. Type your wish instead.");
      else
        this.h.onFail("mic_denied", "The microphone is blocked. Allow it from the icon in the address bar and try again, or type your wish.");
      this.close();
      return;
    }
    if (this.closed) return this.stopMic(); // cancelled while the browser asked for the microphone
    try {
      await this.wireMic(ctx);
    } catch {
      this.h.onFail("unsupported", "The microphone could not be opened in this browser. Type your wish instead.");
      this.close();
      return;
    }
    if (this.closed) return this.stopMic();
    const out = ctx.createAnalyser();
    out.fftSize = 512;
    out.connect(ctx.destination);
    this.speakerAnalyser = out;
    this.meterTimer = window.setInterval(() => this.h.onLevel(this.micLevel, this.speakerLevel()), 100);
    this.open(opts);
  }

  private async wireMic(ctx: AudioContext) {
    const src = ctx.createMediaStreamSource(this.stream!);
    this.down = new Downsampler(ctx.sampleRate, MIC_RATE);
    const onChunk = (f: Float32Array) => {
      this.micLevel = level(f);
      if (!this.ready || this.closed || this.muted) return;
      this.queue(this.down!.push(f));
    };
    // a silent sink keeps the graph pulling samples without playing the microphone back
    const mute = ctx.createGain();
    mute.gain.value = 0;
    mute.connect(ctx.destination);
    if (ctx.audioWorklet && typeof AudioWorkletNode !== "undefined") {
      const url = URL.createObjectURL(new Blob([TAP], { type: "application/javascript" }));
      try {
        await ctx.audioWorklet.addModule(url);
      } finally {
        URL.revokeObjectURL(url);
      }
      const tap = new AudioWorkletNode(ctx, "gs-mic-tap", { numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1 });
      tap.port.onmessage = (e) => onChunk(e.data as Float32Array);
      src.connect(tap);
      tap.connect(mute);
      this.nodes.push(src, tap, mute);
    } else {
      const sp = ctx.createScriptProcessor(2048, 1, 1);
      sp.onaudioprocess = (e) => onChunk(new Float32Array(e.inputBuffer.getChannelData(0)));
      src.connect(sp);
      sp.connect(mute);
      this.nodes.push(src, sp, mute);
    }
  }

  private open(opts: StartOptions) {
    let ws: WebSocket;
    try {
      ws = new WebSocket(liveSocketUrl());
    } catch {
      this.h.onFail("socket", "Voice is not available right now. Type your wish instead.");
      this.close();
      return;
    }
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      ws.send(
        JSON.stringify({
          type: "start",
          mode: opts.mode,
          keep_audio: opts.keepAudio,
          client_turn: true,
          ...(opts.text ? { text: opts.text } : {}),
          ...(opts.projectId ? { project_id: opts.projectId } : {}),
        }),
      );
    };
    ws.onmessage = (e) => {
      if (typeof e.data !== "string") {
        this.play(e.data as ArrayBuffer);
        return;
      }
      let ev: VoiceEvent;
      try {
        ev = JSON.parse(e.data);
      } catch {
        return;
      }
      if (ev.type === "ready") this.ready = true;
      if (ev.type === "interrupted") this.flushSpeech();
      if (ev.type === "submitted" && ev.client_turn) this.send({ type: "claimed" });
      if (ev.type === "ended" || ev.type === "error") this.final = true;
      this.h.onEvent(ev);
    };
    ws.onclose = () => {
      const wasOpen = !this.closed;
      this.ready = false;
      this.stopMic();
      if (wasOpen && !this.final) this.h.onFail("socket", "The voice call dropped. Type your wish instead, or try again.");
    };
    ws.onerror = () => {
      /* onclose follows and reports it */
    };
  }

  private queue(pcm: Int16Array) {
    this.pending.push(pcm);
    this.pendingLen += pcm.length;
    if (this.pendingLen < FRAME_SAMPLES) return;
    const frame = new Int16Array(this.pendingLen);
    let o = 0;
    for (const p of this.pending) {
      frame.set(p, o);
      o += p.length;
    }
    this.pending = [];
    this.pendingLen = 0;
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(frame.buffer);
  }

  private play(buf: ArrayBuffer) {
    const ctx = this.ctx;
    if (!ctx || this.closed || !this.speakerAnalyser) return;
    const f = pcm16ToFloat(buf);
    if (!f.length) return;
    const ab = ctx.createBuffer(1, f.length, LIVE_RATE);
    ab.getChannelData(0).set(f);
    const s = ctx.createBufferSource();
    s.buffer = ab;
    s.connect(this.speakerAnalyser);
    const at = Math.max(ctx.currentTime + 0.04, this.playHead);
    s.start(at);
    this.playHead = at + ab.duration;
    this.sources.add(s);
    s.onended = () => this.sources.delete(s);
  }

  /** Seconds of Live's speech still queued to play. */
  speechLeft(): number {
    return this.ctx ? Math.max(0, this.playHead - this.ctx.currentTime) : 0;
  }

  private flushSpeech() {
    for (const s of this.sources) {
      try {
        s.stop();
      } catch {
        /* already stopped */
      }
    }
    this.sources.clear();
    this.playHead = 0;
  }

  private speakerLevel(): number {
    const a = this.speakerAnalyser;
    if (!a || !this.sources.size) return 0;
    const d = new Float32Array(a.fftSize);
    a.getFloatTimeDomainData(d);
    return level(d);
  }

  private send(obj: unknown) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(obj));
  }

  /** "I'm done": Live wraps up and hands the wish over (or the call ends without one). */
  end() {
    this.muted = true;
    this.send({ type: "end" });
  }

  /** Discard the call: nothing is created. */
  cancel() {
    this.send({ type: "cancel" });
    this.close();
  }

  setKeepAudio(v: boolean) {
    this.send({ type: "keep_audio", value: v });
  }

  private stopMic() {
    for (const n of this.nodes) {
      try {
        n.disconnect();
      } catch {
        /* ignore */
      }
    }
    this.nodes = [];
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
  }

  /** Stop everything: microphone, speech, socket, audio context. Safe to call twice. */
  close() {
    if (this.closed) return;
    this.closed = true;
    this.ready = false;
    if (this.meterTimer != null) window.clearInterval(this.meterTimer);
    this.meterTimer = null;
    this.stopMic();
    this.flushSpeech();
    const ws = this.ws;
    this.ws = null;
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      try {
        ws.close(1000);
      } catch {
        /* ignore */
      }
    }
    void this.ctx?.close().catch(() => {});
    this.ctx = null;
  }
}
