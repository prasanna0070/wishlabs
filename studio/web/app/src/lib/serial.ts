// Web Serial device console for the ESP32-S3 (115200 baud) and the firmware's factory self-test protocol:
//   send "selftest\n"; the device prints GS-SELFTEST-BEGIN, one GS-TEST {json} per check, GS-PROMPT {json}
//   for operator actions, and GS-SELFTEST {json} at the end (engine/gstudio/firmware/.../gs_selftest.h).

export interface SerialLine {
  tag: string;
  data: any;
}

export function parseSerialLine(line: string): SerialLine | null {
  const m = /^(GS-[A-Z-]+)\s*(.*)$/.exec(line.trim());
  if (!m) return null;
  let data: any = m[2];
  try {
    data = m[2] ? JSON.parse(m[2]) : null;
  } catch {
    /* keep raw */
  }
  return { tag: m[1], data };
}

export function serialSupported(): boolean {
  return typeof navigator !== "undefined" && "serial" in navigator;
}

export class SerialConsole {
  private port: any = null;
  private reader: ReadableStreamDefaultReader<string> | null = null;
  private writer: WritableStreamDefaultWriter<Uint8Array> | null = null;
  private closing = false;
  onLine: (line: string) => void = () => {};
  onState: (s: "idle" | "open" | "closed" | "error", msg?: string) => void = () => {};

  async open(baud = 115200) {
    const serial = (navigator as any).serial;
    this.port = await serial.requestPort({ filters: [] });
    await this.port.open({ baudRate: baud });
    this.closing = false;
    this.onState("open");
    this.writer = this.port.writable.getWriter();
    void this.readLoop();
  }

  private async readLoop() {
    const decoder = new TextDecoderStream();
    const done = this.port.readable.pipeTo(decoder.writable).catch(() => {});
    this.reader = decoder.readable.getReader();
    let buf = "";
    try {
      for (;;) {
        const { value, done: end } = await this.reader.read();
        if (end) break;
        buf += value;
        let i: number;
        while ((i = buf.indexOf("\n")) >= 0) {
          this.onLine(buf.slice(0, i).replace(/\r$/, ""));
          buf = buf.slice(i + 1);
        }
      }
    } catch (e: any) {
      if (!this.closing) this.onState("error", String(e?.message || e));
    }
    await done;
  }

  async send(text: string) {
    if (!this.writer) throw new Error("Not connected");
    await this.writer.write(new TextEncoder().encode(text.endsWith("\n") ? text : `${text}\n`));
  }

  async close() {
    this.closing = true;
    try {
      await this.reader?.cancel();
    } catch {
      /* ignore */
    }
    try {
      this.writer?.releaseLock();
    } catch {
      /* ignore */
    }
    try {
      await this.port?.close();
    } catch {
      /* ignore */
    }
    this.port = null;
    this.onState("closed");
  }
}
