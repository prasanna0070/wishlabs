import { useState } from "react";
import { ChevronDown, CircleCheck, CircleDashed, CircleX, ShieldCheck, TriangleAlert } from "lucide-react";
import { useProject } from "../../store/project";
import { useVersionFile } from "../../lib/hooks";
import type { DigitalCheck, Version } from "../../lib/types";
import { Spinner } from "../ui";

interface ChecksDoc {
  summary?: { passed?: number; failed?: number; warning?: number; not_run?: number };
  all_passed?: boolean;
  checks?: DigitalCheck[];
  limits?: string[];
}

const STAGE_NAMES: Record<string, string> = {
  normalize: "Design spec",
  electrical: "Wiring and pins",
  cad: "Shell and CAD",
  pcb: "Backplane PCB",
  firmware: "Firmware",
  order: "Print slicing and parts",
  manual: "Build manual",
  pipeline: "Cross-stage consistency",
  files: "Files",
};

export function checkIcon(s: string, size = 15) {
  if (s === "passed") return <CircleCheck size={size} className="ok" />;
  if (s === "failed") return <CircleX size={size} className="bad" />;
  if (s === "warning") return <TriangleAlert size={size} className="warn" />;
  return <CircleDashed size={size} className="muted" />;
}

export function ChecksCard({ version, expanded = false }: { version: Version; expanded?: boolean }) {
  const ctx = useProject((s) => s.ctx);
  const { data, loading, error } = useVersionFile<ChecksDoc>(ctx, version.n, "checks/digital-checks.json");
  const [open, setOpen] = useState(expanded);
  if (loading)
    return (
      <div className="card pad row muted">
        <Spinner /> Loading checks
      </div>
    );
  if (error || !data) return <div className="card pad muted small">No digital check report in this version.</div>;
  const s = data.summary || {};
  const checks = data.checks || [];
  const bad = (s.failed || 0) + (s.warning || 0);
  const groups = new Map<string, DigitalCheck[]>();
  for (const c of checks) {
    const k = c.stage || (c.id || "").split(":")[0] || "other";
    groups.set(k, [...(groups.get(k) || []), c]);
  }
  return (
    <div className="card checks-card">
      <div className="checks-top">
        <ShieldCheck size={20} className={s.failed ? "bad" : "ok"} />
        <div className="grow">
          <strong>Gadget Studio checks</strong>
          <div className="muted small">
            {s.failed ? `${s.failed} failed` : bad ? `${s.warning} warning${s.warning === 1 ? "" : "s"}` : "No issues found in completed checks"} · {s.passed || 0} passed
            {s.not_run ? ` · ${s.not_run} not run` : ""}
          </div>
        </div>
        <button className="btn sm" onClick={() => setOpen(!open)} aria-expanded={open}>
          Review results <ChevronDown size={14} className={open ? "rot180" : ""} />
        </button>
      </div>
      {open ? (
        <div className="checks-body">
          {[...groups.entries()].map(([stage, cs]) => (
            <details key={stage} open={cs.some((c) => c.status === "failed" || c.status === "warning")}>
              <summary className="check-group">
                {checkIcon(cs.some((c) => c.status === "failed") ? "failed" : cs.some((c) => c.status === "warning") ? "warning" : cs.every((c) => c.status === "passed") ? "passed" : "not_run")}
                <span className="grow">{STAGE_NAMES[stage] || stage}</span>
                <span className="muted small">
                  {cs.filter((c) => c.status === "passed").length}/{cs.length}
                </span>
              </summary>
              <ul className="check-list">
                {cs.map((c, i) => (
                  <li key={c.id || i}>
                    {checkIcon(c.status, 14)}
                    <span>
                      {c.title}
                      {c.detail ? <div className="muted small">{c.detail}</div> : null}
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          ))}
          {data.limits?.length ? (
            <details>
              <summary className="check-group">
                <CircleDashed size={15} className="muted" />
                <span className="grow">What these checks don't establish</span>
              </summary>
              <ul className="check-list plain">
                {data.limits.map((l, i) => (
                  <li key={i}>{l}</li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      ) : null}
      <div className="muted small checks-foot">
        Automated checks can miss problems or flag things incorrectly. Passing checks do not guarantee fit, function or safety; the physical checks in the build manual still need doing.
      </div>
    </div>
  );
}
