// In-browser device tools: flash firmware (esp-web-tools over Web Serial), a serial console (115200) and the
// firmware's factory self-test ("selftest" -> GS-TEST / GS-PROMPT / GS-SELFTEST lines), plus reports and files.
import { useEffect, useRef, useState } from "react";
import { ClipboardCheck, Cpu, Download, FileText, FolderOpen, Plug, PlugZap, Send, ShieldCheck, TerminalSquare, TriangleAlert } from "lucide-react";
import { useProject } from "../../store/project";
import { useFileIndex, useVersionFile } from "../../lib/hooks";
import { fileUrl } from "../../lib/urls";
import { bytes } from "../../lib/format";
import { SerialConsole, parseSerialLine, serialSupported } from "../../lib/serial";
import type { Build, FileEntry, Version } from "../../lib/types";
import { Spinner } from "../ui";
import { StoryAudio } from "./StoryAudio";

type ToolTab = "flash" | "console" | "selftest" | "reports" | "files";

interface TestRow {
  id: string;
  status: string;
  detail?: any;
}

export function DeviceTools({ version, build }: { version: Version; build: Build }) {
  const [tab, setTab] = useState<ToolTab>("flash");
  const [lines, setLines] = useState<string[]>([]);
  const [state, setState] = useState<"idle" | "open" | "closed" | "error">("idle");
  const [stateMsg, setStateMsg] = useState<string | null>(null);
  const [tests, setTests] = useState<TestRow[]>([]);
  const [prompt, setPrompt] = useState<string | null>(null);
  const [summary, setSummary] = useState<any>(null);
  const [testRunning, setTestRunning] = useState(false);
  const port = useRef<SerialConsole | null>(null);

  useEffect(() => () => void port.current?.close(), []);

  const connect = async () => {
    if (!serialSupported()) return;
    const c = new SerialConsole();
    c.onLine = (l) => {
      setLines((ls) => [...ls.slice(-499), l]);
      const p = parseSerialLine(l);
      if (!p) return;
      if (p.tag === "GS-SELFTEST-BEGIN") {
        setTests([]);
        setSummary(null);
        setTestRunning(true);
      } else if (p.tag === "GS-TEST" && p.data?.id) {
        setTests((t) => [...t.filter((x) => x.id !== p.data.id), p.data]);
        setPrompt(null);
      } else if (p.tag === "GS-PROMPT") setPrompt(p.data?.text || String(p.data));
      else if (p.tag === "GS-SELFTEST") {
        setSummary(p.data);
        setPrompt(null);
        setTestRunning(false);
      }
    };
    c.onState = (s, msg) => {
      setState(s);
      setStateMsg(msg || null);
    };
    try {
      await c.open(115200);
      port.current = c;
    } catch (e: any) {
      setState("error");
      setStateMsg(e?.name === "NotFoundError" ? "No port chosen." : e?.message || String(e));
    }
  };
  const disconnect = async () => {
    await port.current?.close();
    port.current = null;
  };
  const sendLine = async (t: string) => {
    if (!port.current) return;
    setLines((ls) => [...ls, `> ${t}`]);
    await port.current.send(t);
  };
  const runSelfTest = async () => {
    if (!port.current) await connect();
    if (!port.current) return;
    setTests([]);
    setSummary(null);
    setTestRunning(true);
    await sendLine("selftest");
  };

  const TABS: { id: ToolTab; label: string; icon: typeof Cpu }[] = [
    { id: "flash", label: "Set up the board", icon: Cpu },
    { id: "console", label: "Device console", icon: TerminalSquare },
    { id: "selftest", label: "Self-test", icon: ClipboardCheck },
    { id: "reports", label: "Check reports", icon: ShieldCheck },
    { id: "files", label: "Files", icon: FolderOpen },
  ];
  const connected = state === "open";

  return (
    <div className="card tools">
      <div className="tools-head">
        <strong>Build tools</strong>
        <div className="tools-tabs" role="tablist">
          {TABS.map((t) => (
            <button key={t.id} role="tab" aria-selected={tab === t.id} className={tab === t.id ? "on" : ""} onClick={() => setTab(t.id)} title={t.label}>
              <t.icon size={15} /> <span>{t.label}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="tools-body">
        {tab === "flash" ? <FlashPanel version={version} build={build} /> : null}
        {tab === "console" || tab === "selftest" ? (
          !serialSupported() ? (
            <div className="callout">
              <TriangleAlert size={14} /> The device console needs Web Serial: use Chrome or Edge on a computer, with the board plugged in by a USB data cable.
            </div>
          ) : (
            <div className="row" style={{ marginBottom: 10 }}>
              {connected ? (
                <button className="btn sm" onClick={() => void disconnect()}>
                  <Plug size={14} /> Disconnect
                </button>
              ) : (
                <button className="btn sm primary" onClick={() => void connect()}>
                  <PlugZap size={14} /> Connect board
                </button>
              )}
              <span className={`pill ${connected ? "success" : ""}`}>{connected ? "Connected · 115200 baud" : state === "error" ? "Not connected" : "Not connected"}</span>
              {stateMsg ? <span className="muted small">{stateMsg}</span> : null}
            </div>
          )
        ) : null}
        {tab === "console" && serialSupported() ? <ConsolePanel lines={lines} connected={connected} onSend={sendLine} onClear={() => setLines([])} /> : null}
        {tab === "selftest" ? (
          <div>
            <p className="muted small" style={{ marginTop: 0 }}>
              Runs the firmware's factory self-test over USB: LED, button, speaker tone, microphone level, microSD, display, sensors, memory and firmware identity. Some checks ask you to press a button or listen.
            </p>
            <button className="btn primary" disabled={!serialSupported() || testRunning} onClick={() => void runSelfTest()}>
              {testRunning ? <Spinner /> : <ClipboardCheck size={15} />} Run factory self-test
            </button>
            {prompt ? (
              <div className="callout warning" style={{ marginTop: 12, fontSize: 15 }}>
                <strong>Your turn:</strong> {prompt}
              </div>
            ) : null}
            {tests.length ? (
              <table className="table" style={{ marginTop: 12 }}>
                <thead>
                  <tr>
                    <th>Check</th>
                    <th>Result</th>
                    <th>Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {tests.map((t) => (
                    <tr key={t.id}>
                      <td className="mono small">{t.id}</td>
                      <td>
                        <span className={`badge ${t.status === "pass" ? "success" : t.status === "fail" ? "danger" : t.status === "operator_check" ? "warning" : ""}`}>
                          {t.status === "operator_check" ? "confirm by eye/ear" : t.status}
                        </span>
                      </td>
                      <td className="muted small mono">{t.detail ? JSON.stringify(t.detail).slice(0, 160) : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
            {summary ? (
              <div className={`callout ${summary.ok ? "success" : "danger"}`} style={{ marginTop: 12 }}>
                <strong>{summary.ok ? "Automatic checks passed" : "Some automatic checks failed"}</strong>: {summary.passed ?? 0} passed, {summary.failed ?? 0} failed
                {summary.operator_checks?.length ? `; confirm by hand: ${summary.operator_checks.join(", ")}` : ""}. This covers the automatic checks only; it is not a physical, acoustic or safety certification.
              </div>
            ) : null}
          </div>
        ) : null}
        {tab === "reports" ? <Reports version={version} /> : null}
        {tab === "files" ? <FilesPanel version={version} /> : null}
      </div>
    </div>
  );
}

function FlashPanel({ version, build }: { version: Version; build: Build }) {
  const ctx = useProject((s) => s.ctx)!;
  const manifestPath = "firmware/web-flasher-manifest.json";
  const manifest = useVersionFile<any>(ctx, version.n, manifestPath);
  const idx = useFileIndex(ctx, version.n);
  const has = (p: string) => (idx instanceof Set ? idx.has(p) : true);
  const [ready, setReady] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!manifest.data || !serialSupported()) return;
    import("esp-web-tools/dist/web/install-button.js").then(
      () => setReady(true),
      (e) => setErr(String(e?.message || e)),
    );
  }, [manifest.data]);
  const fw = build.firmware;
  return (
    <div>
      <p style={{ marginTop: 0 }}>
        <strong>Install the firmware from this page.</strong> Plug the ESP32-S3 board into this computer with a USB data cable, then press the button. It takes about a minute.
      </p>
      <div className="row" style={{ flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
        {fw?.compiled ? <span className="pill success">Compiled for {fw.board || "ESP32-S3"}</span> : <span className="pill warning">Not compiled</span>}
        {fw?.tests_total ? <span className="pill">Unit tests {fw.tests_passed}/{fw.tests_total}</span> : null}
      </div>
      {manifest.loading ? (
        <div className="row muted small">
          <Spinner /> Checking for the web flasher
        </div>
      ) : manifest.data ? (
        !serialSupported() ? (
          <div className="callout">Flashing from the browser needs Web Serial: use Chrome or Edge on a computer.</div>
        ) : ready ? (
          <esp-web-install-button manifest={fileUrl(ctx, version.n, manifestPath)}>
            <button slot="activate" className="btn primary lg">
              <Cpu size={16} /> Flash firmware
            </button>
            <span slot="unsupported" className="muted small">
              This browser cannot flash. Use Chrome or Edge.
            </span>
          </esp-web-install-button>
        ) : err ? (
          <div className="callout danger small">The flasher could not load: {err}</div>
        ) : (
          <div className="row muted small">
            <Spinner /> Loading the flasher
          </div>
        )
      ) : (
        <div className="callout small">This version has no web-flasher manifest. Download the firmware and flash it with esptool, as SOFTWARE_SETUP.md describes.</div>
      )}
      <div className="row" style={{ marginTop: 14, flexWrap: "wrap" }}>
        {has("firmware/firmware.bin") ? (
          <a className="btn sm" href={fileUrl(ctx, version.n, "firmware/firmware.bin", true)}>
            <Download size={14} /> firmware.bin
          </a>
        ) : (
          <span className="pill warning">No firmware.bin in this version</span>
        )}
        {has("SOFTWARE_SETUP.md") ? (
          <a className="btn sm" href={fileUrl(ctx, version.n, "SOFTWARE_SETUP.md", true)}>
            <FileText size={14} /> SOFTWARE_SETUP.md
          </a>
        ) : null}
      </div>
      <p className="muted small" style={{ marginBottom: 0 }}>
        Pins and ports come from this version's electrical plan. Check the wiring against the manual before powering the board.
      </p>
      <div style={{ marginTop: 14 }}>
        <StoryAudio version={version} />
      </div>
    </div>
  );
}

function ConsolePanel({ lines, connected, onSend, onClear }: { lines: string[]; connected: boolean; onSend: (t: string) => void; onClear: () => void }) {
  const [t, setT] = useState("");
  const box = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [lines]);
  return (
    <div>
      <pre className="serial" ref={box} aria-live="polite">
        {lines.length ? lines.join("\n") : "Your device's serial output will appear here."}
      </pre>
      <form
        className="row"
        style={{ marginTop: 8 }}
        onSubmit={(e) => {
          e.preventDefault();
          if (t.trim()) {
            void onSend(t.trim());
            setT("");
          }
        }}
      >
        <input className="input mono" placeholder="Send a command (info, selftest)" value={t} onChange={(e) => setT(e.target.value)} disabled={!connected} />
        <button className="btn sm" disabled={!connected || !t.trim()}>
          <Send size={14} /> Send
        </button>
        <button type="button" className="btn sm ghost" onClick={onClear}>
          Clear
        </button>
      </form>
    </div>
  );
}

const REPORTS = ["checks/digital-checks.json", "checks/delivery-checks.json", "pcb/drc.json", "pcb/erc.json", "firmware/test-results.txt", "firmware/size.json", "print/slice-stats.json"];

function Reports({ version }: { version: Version }) {
  const ctx = useProject((s) => s.ctx)!;
  const idx = useFileIndex(ctx, version.n);
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div>
      <p className="muted small" style={{ marginTop: 0 }}>
        The original check reports delivered with this version. They are digital checks on the generated files, not physical validation.
      </p>
      {REPORTS.filter((r) => (idx instanceof Set ? idx.has(r) : true)).map((r) => (
        <details key={r} className="report" open={open === r} onToggle={(e) => (e.target as HTMLDetailsElement).open && setOpen(r)}>
          <summary className="mono small">{r}</summary>
          {open === r ? <ReportBody path={r} n={version.n} /> : null}
        </details>
      ))}
    </div>
  );
}

function ReportBody({ path, n }: { path: string; n: number }) {
  const ctx = useProject((s) => s.ctx)!;
  const f = useVersionFile<string>(ctx, n, path, "text");
  if (f.loading) return <Spinner />;
  if (f.error) return <div className="muted small">Not in this version.</div>;
  let txt = f.data || "";
  if (path.endsWith(".json")) {
    try {
      txt = JSON.stringify(JSON.parse(txt), null, 2);
    } catch {
      /* raw */
    }
  }
  return <pre className="serial report-pre">{txt.length > 40000 ? `${txt.slice(0, 40000)}\n…` : txt}</pre>;
}

function FilesPanel({ version }: { version: Version }) {
  const ctx = useProject((s) => s.ctx)!;
  const man = useVersionFile<{ files: FileEntry[] }>(ctx, version.n, "manifest.json");
  if (man.loading) return <Spinner />;
  const files = man.data?.files || [];
  if (!files.length) return <div className="muted small">No manifest in this version.</div>;
  const groups: [string, (p: string) => boolean][] = [
    ["Instructions", (p) => /\.md$/i.test(p)],
    ["Manual images", (p) => p.startsWith("manual/")],
    ["CAD and print files", (p) => p.startsWith("cad/") || p.startsWith("print/")],
    ["PCB", (p) => p.startsWith("pcb/")],
    ["Firmware", (p) => p.startsWith("firmware/")],
  ];
  const used = new Set<string>();
  const out = groups.map(([g, f]) => {
    const list = files.filter((x) => !used.has(x.path) && f(x.path));
    list.forEach((x) => used.add(x.path));
    return [g, list] as const;
  });
  out.push(["Other files", files.filter((x) => !used.has(x.path))]);
  return (
    <div className="files-groups">
      {out
        .filter(([, l]) => l.length)
        .map(([g, l]) => (
          <div key={g}>
            <div className="muted small" style={{ margin: "10px 0 4px", fontWeight: 600 }}>
              {g} · {l.length}
            </div>
            {l.slice(0, 60).map((x) => (
              <a key={x.path} className="file-row" href={fileUrl(ctx, version.n, x.path, true)}>
                <span className="mono small grow">{x.path}</span>
                <span className="muted small">{bytes(x.bytes)}</span>
              </a>
            ))}
            {l.length > 60 ? <div className="muted small">+{l.length - 60} more in the export</div> : null}
          </div>
        ))}
    </div>
  );
}
