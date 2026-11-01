// Read-only view of one saved version (MAJ-39): its 3D model, its files with sizes, a file comparison with another
// version, a download, and "Restore as new version". Opening it never moves the project's current version.
import { useMemo, useRef, useState } from "react";
import { ArrowRight, Download, File, GitCompare, RotateCcw } from "lucide-react";
import { useProject } from "../store/project";
import { useVersionFiles } from "../lib/hooks";
import { exportUrl, fileUrl } from "../lib/urls";
import { bytes, dateTime, timeAgo } from "../lib/format";
import { compareFiles, originText, sourceText, totalBytes, triggerMessage } from "../lib/versions";
import type { FileEntry } from "../lib/types";
import { Viewer, ViewerControls, type ViewerHandle } from "../three/Viewer";
import { Dialog, Spinner } from "./ui";

const GROUPS: [string, (p: string) => boolean][] = [
  ["Instructions and records", (p) => !p.includes("/")],
  ["CAD and print files", (p) => p.startsWith("cad/") || p.startsWith("print/")],
  ["Manual images", (p) => p.startsWith("manual/")],
  ["PCB", (p) => p.startsWith("pcb/")],
  ["Firmware", (p) => p.startsWith("firmware/")],
];

export function VersionDialog({ n, onClose, onToast }: { n: number | null; onClose: () => void; onToast: (s: string) => void }) {
  const project = useProject((s) => s.project)!;
  const ctx = useProject((s) => s.ctx)!;
  const readOnly = useProject((s) => s.readOnly);
  const restore = useProject((s) => s.restore);
  const setCurrent = useProject((s) => s.setCurrent);
  const [sel, setSel] = useState<number | null>(n);
  const [other, setOther] = useState<number | null>(null);
  const [onlyDiff, setOnlyDiff] = useState(false);
  const [busy, setBusy] = useState<null | "restore" | "switch">(null);
  const handle = useRef<ViewerHandle | null>(null);
  const shown = sel ?? n;
  const v = project.versions.find((x) => x.n === shown) || null;
  const files = useVersionFiles(v ? ctx : null, v?.n);
  const otherFiles = useVersionFiles(other != null ? ctx : null, other);
  const list = useMemo(() => [...project.versions].sort((a, b) => b.n - a.n), [project.versions]);
  const current = project.current_version ?? null;
  const done = v?.state === "done";
  const hasGlb = files ? files.some((f) => f.path === "cad/assembly.glb") : null;
  const diff = files && otherFiles ? compareFiles(files, otherFiles) : null;
  const trig = v ? triggerMessage(project.chat, v.n) : null;

  if (n == null) return null;
  return (
    <Dialog open onClose={onClose} title="Version history" sub={`${project.name} · viewing a version does not change your project`} wide label="Version history">
      <div className="vd">
        <nav className="vd-list" aria-label="Saved versions">
          {list.map((x) => (
            <button key={x.n} className={`vd-row${x.n === shown ? " on" : ""}`} onClick={() => setSel(x.n)} aria-current={x.n === shown ? "true" : undefined}>
              <span className="ver-n">v{x.n}</span>
              <span className="grow">
                <span className="ver-label">{x.label || `Version ${x.n}`}</span>
                <span className="ver-sub">
                  {dateTime(x.created)}
                  {originText(x) ? ` · ${originText(x)}` : ""}
                </span>
              </span>
              {x.n === current ? <span className="badge success">Current</span> : x.state !== "done" ? <span className="badge">{x.state}</span> : null}
            </button>
          ))}
        </nav>
        {v ? (
          <div className="vd-main">
            <div className="vd-head">
              <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                <h3 className="grow" style={{ fontSize: 16 }}>
                  v{v.n} · {v.label || `Version ${v.n}`}
                </h3>
                {v.n === current ? <span className="pill success">You are working on this version</span> : null}
              </div>
              <div className="muted small vd-meta">
                {dateTime(v.created)} ({timeAgo(v.created)}) · {sourceText(v)}
                {originText(v) ? ` ${originText(v)}` : ""}
                {v.brief_version != null ? ` · brief v${v.brief_version}` : ""}
                {files ? ` · ${files.length} files, ${bytes(totalBytes(files))}` : ""}
                {v.spec_hash ? ` · design ${v.spec_hash.replace(/^sha256:/, "").slice(0, 8)}` : ""}
              </div>
              {trig ? <div className="vd-trigger small">“{(trig.text || "").replace(/^My choices:\n/, "").slice(0, 200)}”</div> : null}
            </div>
            <div className="vd-viewer">
              {!done ? (
                <div className="empty small">{v.state === "error" ? "This version stopped with an error; it has no model." : "This version is still building."}</div>
              ) : hasGlb === false ? (
                <div className="empty small">No 3D model in this version.</div>
              ) : hasGlb === null ? (
                <div className="empty">
                  <Spinner />
                </div>
              ) : (
                <Viewer key={v.n} url={fileUrl(ctx, v.n, "cad/assembly.glb")} handle={handle} ariaLabel={`3D model of version ${v.n}`}>
                  <ViewerControls handle={handle} />
                </Viewer>
              )}
            </div>
            <div className="vd-actions">
              <a className={`btn${done ? "" : " disabled"}`} href={done ? exportUrl(ctx, v.n) : undefined} aria-disabled={!done} download onClick={(e) => (!done ? e.preventDefault() : onToast(`Preparing v${v.n} download`))}>
                <Download size={15} /> Download v{v.n}
              </a>
              {!readOnly && done ? (
                <button
                  className="btn primary"
                  disabled={!!busy}
                  title="Makes a new version with this design; nothing is deleted"
                  onClick={async () => {
                    setBusy("restore");
                    try {
                      await restore(v.n);
                      onToast(`Restored v${v.n} as a new version`);
                      onClose();
                    } catch (e: any) {
                      onToast(e?.message || "Restore failed");
                    } finally {
                      setBusy(null);
                    }
                  }}
                >
                  {busy === "restore" ? <Spinner /> : <RotateCcw size={15} />} Restore as new version
                </button>
              ) : null}
              <div className="grow" />
              {!readOnly && v.n !== current && v.state === "done" ? (
                <button
                  className="btn ghost"
                  disabled={!!busy}
                  title={`Make v${v.n} the version your next edit starts from`}
                  onClick={async () => {
                    setBusy("switch");
                    try {
                      await setCurrent(v.n);
                      onToast(`Now working on v${v.n}`);
                      onClose();
                    } finally {
                      setBusy(null);
                    }
                  }}
                >
                  Work on v{v.n} <ArrowRight size={15} />
                </button>
              ) : null}
            </div>
            <div className="vd-files">
              <div className="row" style={{ gap: 8, flexWrap: "wrap", marginBottom: 6 }}>
                <strong className="grow" style={{ fontSize: 14 }}>
                  Project folder
                </strong>
                <label className="row small" style={{ gap: 6 }}>
                  <GitCompare size={14} /> Compare files with
                  <select className="select sm" value={other ?? ""} onChange={(e) => setOther(e.target.value ? Number(e.target.value) : null)} aria-label="Compare with version">
                    <option value="">Choose a version</option>
                    {list
                      .filter((x) => x.n !== v.n && x.state === "done")
                      .map((x) => (
                        <option key={x.n} value={x.n}>
                          v{x.n} · {x.label || `Version ${x.n}`}
                        </option>
                      ))}
                  </select>
                </label>
                {diff ? (
                  <label className="check small">
                    <input type="checkbox" checked={onlyDiff} onChange={(e) => setOnlyDiff(e.target.checked)} /> Only differences
                  </label>
                ) : null}
              </div>
              {diff ? (
                <div className="muted small" style={{ marginBottom: 8 }} data-testid="vd-diff">
                  Against v{other}: {diff.changed.length} changed, {diff.added.length} only in v{v.n}, {diff.removed.length} only in v{other}, {diff.same} the same.
                </div>
              ) : null}
              {files === undefined ? (
                <div className="row muted small">
                  <Spinner /> Loading files
                </div>
              ) : !files ? (
                <div className="muted small">No saved files in this version yet.</div>
              ) : (
                <FileGroups files={files} n={v.n} diff={diff} onlyDiff={onlyDiff} otherN={other} />
              )}
            </div>
          </div>
        ) : (
          <div className="vd-main empty">That version does not exist.</div>
        )}
      </div>
    </Dialog>
  );
}

function FileGroups({ files, n, diff, onlyDiff, otherN }: { files: FileEntry[]; n: number; diff: ReturnType<typeof compareFiles> | null; onlyDiff: boolean; otherN: number | null }) {
  const ctx = useProject((s) => s.ctx)!;
  const tag = (p: string) => (!diff ? null : diff.added.includes(p) ? "new" : diff.changed.includes(p) ? "changed" : null);
  const shown = onlyDiff && diff ? files.filter((f) => tag(f.path)) : files;
  const used = new Set<string>();
  const groups = GROUPS.map(([g, f]) => {
    const l = shown.filter((x) => !used.has(x.path) && f(x.path));
    l.forEach((x) => used.add(x.path));
    return [g, l] as const;
  });
  groups.push(["Other files", shown.filter((x) => !used.has(x.path))]);
  return (
    <div className="vd-groups" data-testid="vd-files">
      {groups
        .filter(([, l]) => l.length)
        .map(([g, l]) => (
          <details key={g} open={g === "Instructions and records" || (onlyDiff && !!diff)}>
            <summary className="small">
              <strong>{g}</strong> <span className="muted">· {l.length} files · {bytes(totalBytes(l))}</span>
            </summary>
            {l.map((x) => {
              const t = tag(x.path);
              return (
                <a key={x.path} className="file-row" href={fileUrl(ctx, n, x.path, true)}>
                  <File size={13} className="muted" />
                  <span className="mono small grow">{x.path}</span>
                  {t ? <span className={`badge ${t === "new" ? "primary" : "warning"}`}>{t === "new" ? `not in v${otherN}` : "changed"}</span> : null}
                  <span className="muted small">{bytes(x.bytes)}</span>
                </a>
              );
            })}
          </details>
        ))}
      {onlyDiff && diff?.removed.length ? (
        <details open>
          <summary className="small">
            <strong>Only in v{otherN}</strong> <span className="muted">· {diff.removed.length} files</span>
          </summary>
          {diff.removed.map((p) => (
            <div key={p} className="file-row muted">
              <File size={13} />
              <span className="mono small grow">{p}</span>
            </div>
          ))}
        </details>
      ) : null}
    </div>
  );
}
