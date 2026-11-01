// Server-Sent Events over fetch (POST /chat) and over EventSource (GET /stream). API.md §3.
import { ApiError } from "./api";
import { useAuth } from "../store/auth";

export interface SSEEvent {
  event: string;
  data: any;
}

/** Incremental SSE parser: feed text chunks, get complete events back. Pure, so it is unit-tested. */
export class SSEParser {
  private buf = "";
  push(chunk: string): SSEEvent[] {
    this.buf += chunk.replace(/\r\n/g, "\n");
    const out: SSEEvent[] = [];
    let i: number;
    while ((i = this.buf.indexOf("\n\n")) >= 0) {
      const block = this.buf.slice(0, i);
      this.buf = this.buf.slice(i + 2);
      const ev = parseBlock(block);
      if (ev) out.push(ev);
    }
    return out;
  }
  flush(): SSEEvent[] {
    const rest = this.buf.trim();
    this.buf = "";
    const ev = rest ? parseBlock(rest) : null;
    return ev ? [ev] : [];
  }
}

export function parseBlock(block: string): SSEEvent | null {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (!line || line.startsWith(":")) continue;
    const c = line.indexOf(":");
    const field = c < 0 ? line : line.slice(0, c);
    let value = c < 0 ? "" : line.slice(c + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }
  if (!data.length) return null;
  const raw = data.join("\n");
  try {
    return { event, data: JSON.parse(raw) };
  } catch {
    return { event, data: raw };
  }
}

/** POST a chat turn and dispatch each event. Resolves when the stream ends. */
export async function postStream(url: string, body: unknown, onEvent: (e: SSEEvent) => void, signal?: AbortSignal) {
  const res = await fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (res.status === 401) useAuth.getState().setAuthed(false);
  if (!res.ok || !res.body) {
    let e: any = {};
    try {
      e = (await res.json())?.error || {};
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, e.code || "error", e.message || `Request failed (${res.status})`, e.details);
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  const parser = new SSEParser();
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    let finished = false;
    for (const ev of parser.push(value)) {
      onEvent(ev);
      if (ev.event === "done") finished = true;
    }
    if (finished) {
      // `done` is the last event (API.md §3.1): close the reader instead of waiting for the server to hang up
      await reader.cancel().catch(() => {});
      return;
    }
  }
  for (const ev of parser.flush()) onEvent(ev);
}

/** GET /stream as an EventSource; returns a close function. */
export function openEventStream(url: string, events: string[], onEvent: (e: SSEEvent) => void, onEnd?: () => void): () => void {
  const es = new EventSource(url, { withCredentials: true });
  let closed = false;
  const close = () => {
    if (!closed) {
      closed = true;
      es.close();
      onEnd?.();
    }
  };
  for (const name of events) {
    es.addEventListener(name, (m: MessageEvent) => {
      let data: any = m.data;
      try {
        data = JSON.parse(m.data);
      } catch {
        /* keep raw */
      }
      onEvent({ event: name, data });
      if (name === "done") close();
    });
  }
  es.onerror = () => {
    // The server closes the stream after `done`; a network error also ends it. Callers re-open if still needed.
    close();
  };
  return close;
}
