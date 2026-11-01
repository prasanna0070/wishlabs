import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, Check, ChevronDown, Copy, Download, FileText, History, Link2, Redo2, RotateCcw, Undo2, Eye, AlertCircle, TriangleAlert } from "lucide-react";
import { useProject, currentVersion, isBuilding } from "../store/project";
import { api } from "../lib/api";
import { exportUrl, shareUrl } from "../lib/urls";
import { dateTime, timeAgo } from "../lib/format";
import { briefFor, designChangedSinceBrief, originText } from "../lib/versions";
import type { Brief, Version } from "../lib/types";
import { Dialog, Dropdown, Logo, Spinner, Toast } from "./ui";
import { BriefView } from "./BriefView";
import { VersionDialog } from "./VersionDialog";
import type { TabId } from "./panel/RightPanel";

export function Header({ onTab: _onTab }: { onTab: (t: TabId) => void }) {
  const project = useProject((s) => s.project)!;
  const ctx = useProject((s) => s.ctx)!;
  const readOnly = useProject((s) => s.readOnly);
  const saving = useProject((s) => s.saving);
  const saveError = useProject((s) => s.saveError);
  const turn = useProject((s) => s.turn);
  const statuses = useProject((s) => s.statuses);
  const { rename, undo, redo } = useProject.getState();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(project.name);
  const [briefOpen, setBriefOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [viewN, setViewN] = useState<number | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => setName(project.name), [project.name]);
  useEffect(() => {
    if (editing) input.current?.select();
  }, [editing]);

  const cur = currentVersion(project);
  const ns = project.versions.map((v) => v.n);
  const canUndo = !readOnly && cur != null && ns.length > 1 && cur.n > Math.min(...ns);
  const canRedo = !readOnly && cur != null && ns.length > 1 && cur.n < Math.max(...ns);
  const building = isBuilding(project);
  const runningV = project.versions.find((v) => v.state === "queued" || v.state === "running");
  const pct = runningV ? statuses[runningV.n]?.percent ?? runningV.percent ?? 0 : 0;
  const hasVersions = project.versions.length > 0;

  const commit = () => {
    setEditing(false);
    const v = name.trim();
    if (v && v !== project.name) void rename(v);
    else setName(project.name);
  };

  return (
    <header className="ws-header">
      {readOnly ? (
        // A signed-out viewer of a share link: the mark and name lead to the Wish Labs landing page ("/" on
        // wish.plugbook.si), not into the sign-in screen.
        <a href="/" className="ws-brand" aria-label="Wish Labs home" title="Wish Labs">
          <Logo size={24} />
        </a>
      ) : (
        <>
          <Link to="/" className="icon-btn" aria-label="Back to home" title="Back to home">
            <ArrowLeft size={18} />
          </Link>
          <span className="ws-mark" title="Wish Labs">
            <Logo size={22} withText={false} />
          </span>
        </>
      )}
      {editing ? (
        <input
          ref={input}
          className="title-input"
          value={name}
          maxLength={80}
          onChange={(e) => setName(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
            if (e.key === "Escape") {
              setName(project.name);
              setEditing(false);
            }
          }}
          aria-label="Project name"
        />
      ) : (
        <h1 className="ws-title">
          <button className="title-btn" onClick={() => !readOnly && setEditing(true)} aria-label={`Rename project: ${project.name}`} disabled={readOnly}>
            {project.name || "Untitled gadget"}
          </button>
        </h1>
      )}
      {readOnly ? (
        <span className="pill ro-pill" title="Read-only share">
          <Eye size={13} /> <span className="lbl">Read-only share</span>
        </span>
      ) : null}
      <div className="grow" />

      <button className="btn" onClick={() => setBriefOpen(true)}>
        <FileText size={16} /> <span className="lbl">Project brief</span>
      </button>

      {!readOnly ? (
        <span className={`saved${saveError ? " err" : ""}`} aria-live="polite">
          {saveError ? (
            <>
              <AlertCircle size={15} /> {saveError}
            </>
          ) : saving > 0 ? (
            <>
              <Spinner /> Saving
            </>
          ) : (
            <>
              <Check size={15} /> Saved
            </>
          )}
        </span>
      ) : null}

      {hasVersions ? (
        <div className="row" style={{ gap: 2 }}>
          <button className="icon-btn" aria-label="Undo design change" title="Previous version" disabled={!canUndo || building} onClick={() => void undo()}>
            <Undo2 size={18} />
          </button>
          <button className="icon-btn" aria-label="Redo design change" title="Next version" disabled={!canRedo || building} onClick={() => void redo()}>
            <Redo2 size={18} />
          </button>
        </div>
      ) : null}

      <VersionMenu versions={project.versions} current={project.current_version ?? null} readOnly={readOnly} onToast={setToast} onView={setViewN} />

      {building || turn?.running ? (
        <span className="taking-shape">
          <Spinner /> {building ? `Building v${runningV?.n}${pct ? ` · ${Math.round(pct)}%` : ""}` : "Taking shape"}
        </span>
      ) : null}

      {!readOnly && hasVersions ? (
        <button className="btn" onClick={() => setShareOpen(true)} disabled={building}>
          <Link2 size={16} /> <span className="lbl">Share</span>
        </button>
      ) : null}
      {hasVersions ? (
        <a
          className="btn primary"
          href={cur?.state === "done" ? exportUrl(ctx, cur.n) : undefined}
          aria-disabled={cur?.state !== "done"}
          onClick={(e) => {
            if (cur?.state !== "done") e.preventDefault();
            else setToast("Preparing download");
          }}
          download
        >
          <Download size={16} /> <span className="lbl">Export project</span>
        </a>
      ) : null}

      <BriefSheet open={briefOpen} onClose={() => setBriefOpen(false)} />
      {shareOpen ? <ShareDialog onClose={() => setShareOpen(false)} /> : null}
      {viewN != null ? <VersionDialog key={viewN} n={viewN} onClose={() => setViewN(null)} onToast={setToast} /> : null}
      <Toast text={toast} onDone={() => setToast(null)} />
    </header>
  );
}

function stateChip(v: Version) {
  if (v.state === "done") return null;
  if (v.state === "error") return <span className="badge danger">Error</span>;
  return <span className="badge primary">{v.state === "queued" ? "Queued" : "Building"}</span>;
}

function VersionMenu({ versions, current, readOnly, onToast, onView }: { versions: Version[]; current: number | null; readOnly: boolean; onToast: (s: string) => void; onView: (n: number) => void }) {
  const { restore } = useProject.getState();
  const [busy, setBusy] = useState<number | null>(null);
  const list = [...versions].reverse();
  return (
    <Dropdown
      align="right"
      width={420}
      trigger={({ toggle, open }) => (
        <button className="btn ghost" onClick={toggle} aria-expanded={open} aria-label={`Version history${current ? ` · v${current}` : ""}`}>
          <History size={16} /> <span className="lbl">Version history</span>
          {current ? <span className="muted">v{current}</span> : null}
          <ChevronDown size={15} />
        </button>
      )}
    >
      {(close) => (
        <div className="ver-menu">
          <div className="ver-head">
            <strong>Version history</strong>
            <span className="muted small">Every build, edit, tweak and restore is a new version. Open one to look at it; your project stays on v{current ?? "?"}.</span>
          </div>
          {list.length === 0 ? (
            <div className="muted small" style={{ padding: "12px 10px" }}>
              No saved versions yet. A version appears when the first build starts.
            </div>
          ) : (
            <div className="ver-list">
              {list.map((v) => (
                <div key={v.n} className={`ver-row${v.n === current ? " on" : ""}`}>
                  <button
                    className="ver-main"
                    title={`Open v${v.n}: model, files and download (read-only)`}
                    onClick={() => {
                      close();
                      onView(v.n);
                    }}
                  >
                    <span className="ver-n">v{v.n}</span>
                    <span className="grow">
                      <span className="ver-label">{v.label || `Version ${v.n}`}</span>
                      <span className="ver-sub">
                        {dateTime(v.created)} · {timeAgo(v.created)}
                        {originText(v) ? ` · ${originText(v)}` : ""}
                        {v.summary ? ` · ${v.summary}` : ""}
                      </span>
                    </span>
                    {stateChip(v)}
                    {v.n === current ? <span className="badge success">Current</span> : null}
                    {busy === v.n ? <Spinner /> : null}
                  </button>
                  {!readOnly && v.state === "done" && v.n !== current ? (
                    <button
                      className="btn sm"
                      title="Make a new version from this one"
                      onClick={async () => {
                        setBusy(v.n);
                        try {
                          await restore(v.n);
                          onToast(`Restored v${v.n} as a new version`);
                          close();
                        } catch (e: any) {
                          onToast(e?.message || "Restore failed");
                        } finally {
                          setBusy(null);
                        }
                      }}
                    >
                      <RotateCcw size={14} /> Restore
                    </button>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </Dropdown>
  );
}

function BriefSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const project = useProject((s) => s.project)!;
  const cur = currentVersion(project);
  const history = briefHistory(project.brief_history, project.brief);
  const [pick, setPick] = useState<number | null>(null);
  const defaultBrief = briefFor(project, cur);
  const brief = (pick != null ? history.find((b) => b.version === pick) : null) || defaultBrief;
  const changed = !!brief && !!cur && cur.state !== "error" && designChangedSinceBrief(brief, cur, project.versions);
  return (
    <Dialog open={open} onClose={onClose} title="Project brief" sub={project.name} variant="sheet">
      {brief ? (
        <>
          <div className="row" style={{ marginBottom: 12, flexWrap: "wrap" }}>
            {history.length > 1 ? (
              <select className="select sm" value={brief.version} onChange={(e) => setPick(Number(e.target.value))} aria-label="Brief version">
                {history.map((b) => (
                  <option key={b.version} value={b.version}>
                    Brief v{b.version}
                    {b.created ? ` · ${dateTime(b.created)}` : ""}
                  </option>
                ))}
              </select>
            ) : (
              <span className="pill">Brief v{brief.version}</span>
            )}
            {brief.approved ? (
              <span className="pill success">
                <Check size={13} /> Approved {brief.approved_at ? timeAgo(brief.approved_at) : ""}
              </span>
            ) : project.brief?.version === brief.version ? (
              <span className="pill warning">Waiting for your approval</span>
            ) : null}
          </div>
          {changed && cur ? (
            <div className="callout warning row brief-changed" role="status" style={{ marginBottom: 12, alignItems: "flex-start" }}>
              <TriangleAlert size={15} style={{ flex: "none", marginTop: 2 }} />
              <span>
                <strong>The design changed since this brief.</strong> v{cur.n} ({cur.label || `Version ${cur.n}`}) is not the design brief v{brief.version} describes, so sizes and parts below may be out of date.
              </span>
            </div>
          ) : null}
          <BriefView brief={brief} />
        </>
      ) : (
        <div className="empty" style={{ height: "auto", paddingTop: 80 }}>
          <div className="icon-wrap">
            <FileText size={22} />
          </div>
          <h3>No project brief yet</h3>
          <p style={{ margin: 0 }}>When you review a brief in the chat, you will find it here.</p>
          <button className="btn" onClick={onClose}>
            Go to chat
          </button>
        </div>
      )}
    </Dialog>
  );
}

function briefHistory(history: Brief[] | undefined, latest: Brief | null | undefined): Brief[] {
  const all = [...(history || [])];
  if (latest && !all.some((b) => b.version === latest.version)) all.push(latest);
  return all.sort((a, b) => b.version - a.version);
}

function ShareDialog({ onClose }: { onClose: () => void }) {
  const project = useProject((s) => s.project)!;
  const [token, setToken] = useState<string | null>(project.share?.token || null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const url = token ? shareUrl(token) : "";

  const create = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await api.share(project.id);
      setToken(r.token);
    } catch (e: any) {
      setErr(e?.message || "Could not create a link");
    } finally {
      setBusy(false);
    }
  };
  const revoke = async () => {
    setBusy(true);
    try {
      await api.unshare(project.id);
      setToken(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onClose={onClose} title="Share a read-only link" sub="Anyone with the link can view the design, chat, parts, manual and files, and download the export. They cannot edit or order.">
      {token ? (
        <div className="stack">
          <div className="row">
            <input className="input mono" readOnly value={url} onFocus={(e) => e.target.select()} aria-label="Share link" />
            <button
              className="btn"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(url);
                  setCopied(true);
                } catch {
                  setErr("Copy failed: select the link and copy it.");
                }
              }}
            >
              {copied ? <Check size={15} /> : <Copy size={15} />} {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <div className="row">
            <a className="btn sm" href={url} target="_blank" rel="noreferrer">
              <Eye size={14} /> Open link
            </a>
            <div className="grow" />
            <button className="btn sm danger" onClick={revoke} disabled={busy}>
              Stop sharing
            </button>
          </div>
        </div>
      ) : (
        <button className="btn primary" onClick={create} disabled={busy}>
          {busy ? <Spinner /> : <Link2 size={15} />} Create link
        </button>
      )}
      {err ? <div className="callout danger" style={{ marginTop: 12 }}>{err}</div> : null}
    </Dialog>
  );
}
