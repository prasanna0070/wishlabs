import { useMemo, useState } from "react";
import { AlertTriangle, Check, ChevronRight, Circle, CircleCheck, CircleDashed, CircleX, Hammer, RefreshCw, RotateCw, SlidersHorizontal } from "lucide-react";
import { useProject, type TurnState } from "../../store/project";
import { useNow, useVersionFile } from "../../lib/hooks";
import { checkTally, duration, endLists, inr, plainText, stageHasFailures } from "../../lib/format";
import { conceptEstimate, waitText } from "../../lib/estimate";
import { groupConcepts } from "../../lib/concepts";
import { rewriteShareUrl } from "../../lib/urls";
import type { Build, ChatMessage, Concept, Status, StatusStage } from "../../lib/types";
import { Markdown, ProgressBar, Spinner } from "../ui";
import { BriefView } from "../BriefView";

export function MessageView({ m, active }: { m: ChatMessage; active: string | null }) {
  if (m.role === "user") return <UserMessage m={m} />;
  switch (m.kind) {
    case "progress":
      return <BuildProgressCard m={m} />;
    case "result":
      return <ResultCard m={m} />;
    case "error":
      return <ErrorCard m={m} />;
    case "concepts":
      return <ConceptsMessage m={m} active={active === m.id} />;
    case "brief":
      return <BriefMessage m={m} active={active === m.id} />;
    case "choices":
      return <ChoicesMessage m={m} active={active === m.id} />;
    default:
      return (
        <div className="msg assistant">
          <Markdown text={m.text} />
          {m.draft ? <span className="caret" aria-hidden="true" /> : null}
        </div>
      );
  }
}

function UserMessage({ m }: { m: ChatMessage }) {
  const project = useProject((s) => s.project)!;
  const ctx = useProject((s) => s.ctx);
  const turns = useProject((s) => s.turns);
  const turn = useProject((s) => s.turn);
  let concept: Concept | undefined;
  const choice = /^My choices:\n(.+)$/s.exec(m.text || "");
  if (m.payload?.concept_id) concept = project.concepts.find((c) => c.id === m.payload.concept_id);
  else if (choice) concept = project.concepts.find((c) => c.title === choice[1].trim());
  const img = rewriteShareUrl(m.payload?.image_url || (concept ? concept.photo_url || concept.render_url : null), ctx?.share);
  const editPart = m.payload?.action === "edit_part";
  const t = turn?.userMsgId === m.id ? turn : turns[m.id];
  return (
    <div className="msg-user-wrap">
      {img ? <img className="choice-img" src={img} alt={concept ? `${concept.title}: ${concept.blurb}` : "Chosen concept"} /> : null}
      <div className={`msg user${m.kind === "tweak" ? " is-tweak" : ""}`}>
        {editPart ? (
          <div className="row small muted" style={{ marginBottom: 4 }}>
            <SlidersHorizontal size={13} /> Part edit{m.payload?.label ? `: ${m.payload.label}` : ""}
          </div>
        ) : null}
        {m.kind === "tweak" ? (
          <div className="row small muted" style={{ marginBottom: 4 }}>
            <SlidersHorizontal size={13} /> Direct edit{m.payload?.version ? ` → v${m.payload.version}` : ""}
          </div>
        ) : null}
        {choice ? (
          <>
            <span className="muted">My choices:</span>
            <br />
            {choice[1]}
          </>
        ) : (
          <Markdown text={m.text} className="compact" />
        )}
      </div>
      {t ? <TurnActivity t={t} /> : null}
    </div>
  );
}

export function TurnActivity({ t }: { t: TurnState }) {
  const [open, setOpen] = useState(false);
  const usage = useProject((s) => s.project?.usage?.turns?.find((u: any) => u.turn_id && u.turn_id === t.turnId));
  const now = useNow(1000, t.running);
  const secs = ((t.endedAt || now) - t.startedAt) / 1000;
  const cur = [...t.steps].reverse().find((s) => s.state === "running");
  const turns = useProject((s) => s.project?.usage?.turns);
  const pid = useProject((s) => s.project?.id);
  const conceptTurn = t.running && isConceptTurn(t);
  const eta = useMemo(() => (conceptTurn ? conceptEstimate(turns, pid) : null), [conceptTurn, turns, pid]);
  return (
    <div className="activity">
      <button className="activity-pill" onClick={() => setOpen(!open)} aria-expanded={open}>
        <ChevronRight size={14} className={open ? "rot" : ""} />
        {t.running ? `Working for ${duration(secs)}` : `Worked for ${duration(secs)}`}
      </button>
      {t.running && cur ? (
        <div className="activity-now">
          <Spinner /> {cur.label}…
        </div>
      ) : null}
      {conceptTurn ? (
        <div className="activity-eta muted small">
          Concepts with photos usually take {waitText(eta)}{eta ? ` here (last ${eta.n === 1 ? "concept turn" : `${eta.n} concept turns`})` : ""}. It keeps working if you leave this page.
        </div>
      ) : null}
      {open ? (
        <ul className="activity-steps">
          {t.steps.map((s) => (
            <li key={s.id} className={s.state}>
              {s.state === "running" ? <Spinner /> : s.state === "error" ? <CircleX size={14} /> : <Check size={14} />}
              <span>{s.label || "Done"}</span>
            </li>
          ))}
          {usage ? (
            <li className="usage">
              <span className="muted">
                {fmtTok((usage.input_tokens || 0) + (usage.output_tokens || 0) + (usage.thinking_tokens || 0))} tokens
                {usage.images ? ` · ${usage.images} images` : ""}
                {usage.cost_usd_est != null ? ` · about $${Number(usage.cost_usd_est).toFixed(2)} (estimate)` : ""}
              </span>
            </li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}

/** A turn that is making concepts (or will, after the answers): its spinner gets a measured ETA. */
export function isConceptTurn(t: Pick<TurnState, "action" | "steps">): boolean {
  if (["regenerate_concepts", "more_concepts"].includes(t.action || "")) return true;
  return t.steps.some((s) => s.id.startsWith("concept:") || /propose_concepts|concept/i.test(s.label || "")) || t.action === "answers";
}

function fmtTok(n: number) {
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n);
}

function ChoicesMessage({ m, active }: { m: ChatMessage; active: boolean }) {
  const qs = m.payload?.questions || [];
  return (
    <div className="msg assistant">
      {m.text ? <Markdown text={m.text} /> : null}
      {!active && qs.length ? (
        <div className="q-summary">
          {qs.map((q: any) => (
            <div key={q.id} className="small">
              <span className="muted">Asked:</span> {q.question}
            </div>
          ))}
        </div>
      ) : null}
      {active ? <div className="hint-below">Answer below ↓</div> : null}
    </div>
  );
}

function ConceptsMessage({ m, active }: { m: ChatMessage; active: boolean }) {
  const project = useProject((s) => s.project)!;
  const ctx = useProject((s) => s.ctx);
  const readOnly = useProject((s) => s.readOnly);
  const turn = useProject((s) => s.turn);
  const send = useProject((s) => s.send);
  const ids: string[] = m.payload?.concept_ids || [];
  const list = ids.map((id) => project.concepts.find((c) => c.id === id)).filter(Boolean) as Concept[];
  const sel = m.payload?.selected || project.selected_concept;
  const groups = groupConcepts(list, m.payload?.tiers);
  // after a build, the newest concept card offers a fresh set (for example a real robot instead of an animal)
  const lastConcepts = [...project.chat].reverse().find((x) => x.kind === "concepts");
  const offerMore = !readOnly && !active && lastConcepts?.id === m.id && project.versions.length > 0 && project.stage !== "building";
  return (
    <div className="msg assistant">
      {m.text ? <Markdown text={m.text} /> : null}
      {active ? (
        <div className="hint-below">Choose a concept below ↓</div>
      ) : list.length ? (
        <div className="concept-history">
          {groups.map((g) => (
            <div key={g.id} className="concept-tier">
              {g.label ? <div className="tier-label">{g.label}</div> : null}
              <div className="concept-mini">
                {g.items.map((c) => {
                  const src = rewriteShareUrl(c.photo_url || c.render_url, ctx?.share);
                  return (
                    <figure key={c.id} className={c.id === sel ? "on" : ""} title={c.blurb}>
                      {src ? <img src={src} alt={c.title} loading="lazy" /> : <div className="ph" />}
                      <figcaption>
                        {c.id === sel ? <Check size={12} /> : null}
                        {c.title}
                      </figcaption>
                    </figure>
                  );
                })}
              </div>
            </div>
          ))}
          {offerMore ? (
            <button className="btn sm" style={{ marginTop: 10 }} disabled={!!turn?.running} onClick={() => void send({ action: "more_concepts" })}>
              <RefreshCw size={14} /> Show new concepts
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function BriefMessage({ m, active }: { m: ChatMessage; active: boolean }) {
  const project = useProject((s) => s.project)!;
  const v = m.payload?.brief_version;
  const brief = (project.brief_history || []).find((b) => b.version === v) || (project.brief?.version === v ? project.brief : null) || project.brief;
  const [open, setOpen] = useState(false);
  return (
    <div className="msg assistant">
      {m.text ? <Markdown text={m.text} /> : null}
      {active ? (
        <div className="hint-below">Review the brief below ↓</div>
      ) : brief ? (
        <div className="brief-static">
          <button className="brief-static-head" onClick={() => setOpen(!open)} aria-expanded={open}>
            <ChevronRight size={14} className={open ? "rot" : ""} />
            <strong>Brief v{brief.version}</strong>
            <span className="muted">{brief.title}</span>
            <span className="grow" />
            {brief.approved ? <span className="badge success">Approved</span> : <span className="badge">Superseded</span>}
          </button>
          {open ? <BriefView brief={brief} compact /> : null}
        </div>
      ) : null}
    </div>
  );
}

function stageIcon(state: string, s?: StatusStage) {
  if (state === "running") return <Spinner />;
  if (state === "done" && stageHasFailures(s)) return <AlertTriangle size={16} className="warn warn-icon" aria-label="finished with failures" />;
  if (state === "done") return <CircleCheck size={16} className="ok" />;
  if (state === "error" || state === "failed") return <CircleX size={16} className="bad" />;
  if (state === "skipped") return <CircleDashed size={16} className="muted" />;
  return <Circle size={16} className="pending" />;
}

function stageSecs(s: { started?: string | null; ended?: string | null }, now: number) {
  if (!s.started) return null;
  const a = Date.parse(s.started);
  const b = s.ended ? Date.parse(s.ended) : now;
  return Number.isNaN(a) ? null : (b - a) / 1000;
}

export function BuildProgressCard({ m, status: given, compact = false }: { m?: ChatMessage; status?: Status | null; compact?: boolean }) {
  const project = useProject((s) => s.project)!;
  const statuses = useProject((s) => s.statuses);
  const n: number | undefined = m?.payload?.version;
  const version = project.versions.find((v) => v.n === n);
  const status: Status | null = given ?? (n != null ? statuses[n] : null) ?? m?.payload?.status ?? null;
  const state = version?.state === "done" || version?.state === "error" ? version.state : status?.state || version?.state || "queued";
  const running = state === "queued" || state === "running";
  const now = useNow(1000, running);
  const [open, setOpen] = useState(running && !compact);
  const pct = state === "done" ? 100 : status?.percent ?? version?.percent ?? 0;
  const startedAt = version?.started || status?.stages?.find((s) => s.started)?.started || version?.created;
  const total = startedAt ? ((version?.ended && !running ? Date.parse(version.ended) : now) - Date.parse(startedAt)) / 1000 : null;
  const stages = status?.stages || [];
  const lines = status?.messages || [];
  const said = lines.filter((l) => !l.derived);
  const derived = lines.filter((l) => l.derived);
  return (
    <>
    {!compact && said.length ? (
      <div className="msg assistant narration-lines">
        {said.map((l, i) => (
          <p key={i}>{plainText(l.text)}</p>
        ))}
      </div>
    ) : null}
    <div className={`build-card ${state}${compact ? " compact" : ""}`}>
      <button className="build-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <Hammer size={16} />
        <span className="grow">
          <strong>
            {state === "done" ? `Built v${n}` : state === "error" ? `Build v${n} stopped` : state === "queued" ? `v${n} queued` : `Building v${n}`}
          </strong>
          {version?.label ? <span className="muted"> · {version.label}</span> : null}
        </span>
        <span className="muted small nowrap">
          {Math.round(pct)}%{total != null ? ` · ${duration(total)}` : ""}
        </span>
        <ChevronRight size={15} className={open ? "rot" : ""} />
      </button>
      <ProgressBar value={pct} />
      {open ? (
        <>
          {stages.length ? (
            <StageList stages={stages} now={now} />
          ) : running ? (
            <div className="row muted small" style={{ padding: "10px 2px 2px" }}>
              <Spinner /> Waiting for the builder to start…
            </div>
          ) : null}
          {(compact ? lines : derived).length ? (
            <div className="narration">
              {(compact ? lines : derived).slice(-4).map((l, i) => (
                <p key={i}>{plainText(l.text)}</p>
              ))}
            </div>
          ) : null}
          {running ? <div className="muted small" style={{ marginTop: 8 }}>It keeps working if you leave this page.</div> : null}
          {state === "error" ? <div className="callout danger" style={{ marginTop: 10 }}>{String(status?.error?.message || status?.error || version?.error?.message || version?.error || "The build stopped with an error.")}</div> : null}
        </>
      ) : null}
    </div>
    </>
  );
}

/** The build card's stage rows. A finished stage whose own result has failures (the checks stage) gets a warning
 * icon, and raw status enums are shown as words. */
export function StageList({ stages, now }: { stages: StatusStage[]; now: number }) {
  return (
    <ul className="stages">
      {stages.map((s) => {
        const secs = stageSecs(s, now);
        return (
          <li key={s.id} className={`${s.state}${s.state === "done" && stageHasFailures(s) ? " has-failures" : ""}`}>
            {stageIcon(s.state, s)}
            <span className="grow">
              <span className="st-label">{s.label}</span>
              {s.message && s.state !== "pending" ? <span className="st-msg"> {plainText(s.message)}</span> : null}
            </span>
            {secs != null ? <span className="muted small">{duration(secs)}</span> : null}
          </li>
        );
      })}
    </ul>
  );
}

/** Result chips: the digital-checks chip shows a warning icon (never a tick) when any check failed. */
export function ResultChips({ summary }: { summary: any }) {
  const c = summary?.checks || {};
  const t = checkTally(c);
  return (
    <div className="result-chips">
      {t ? (
        t.failed > 0 ? (
          <span className="pill warning" title={t.failedTitles.length ? `Failed: ${t.failedTitles.join("; ")}` : undefined}>
            <AlertTriangle size={13} className="warn-icon" /> {t.passed}/{t.total} digital checks · {t.failed} failed
            {t.warned ? ` · ${t.warned} warning${t.warned === 1 ? "" : "s"}` : ""}
          </span>
        ) : (
          <span className={`pill ${t.passed === t.total && !t.warned ? "success" : "warning"}`}>
            {t.passed === t.total && !t.warned ? <Check size={13} /> : <AlertTriangle size={13} className="warn-icon" />} {t.passed}/{t.total} digital checks
            {t.warned ? ` · ${t.warned} warning${t.warned === 1 ? "" : "s"}` : ""}
          </span>
        )
      ) : null}
      {c.firmware_tests ? <span className="pill">Firmware tests {c.firmware_tests}</span> : null}
      {c.pcb_drc_errors != null ? <span className={`pill ${c.pcb_drc_errors === 0 ? "success" : "warning"}`}>PCB DRC {c.pcb_drc_errors} errors</span> : null}
      {summary?.price?.inr ? (
        <span className="pill primary">
          {plainText(summary.price.label && /₹/.test(summary.price.label) ? summary.price.label : `${inr(summary.price.inr)} delivered · ${summary.price.label || "estimate"}`)}
        </span>
      ) : null}
    </div>
  );
}

const TRACE_WORDS: Record<string, string> = { implemented: "Implemented", not_met: "Not met", unverified: "Not verified" };

/** REVIEW-1 MAJ-37: one row per brief item (summary.requirements from the server's trace), with its evidence and files. */
export function BriefTrace({ rows }: { rows?: { item?: string; status?: string; evidence?: string | string[]; paths?: string[] }[] }) {
  if (!rows?.length) return null;
  const done = rows.filter((r) => r.status === "implemented").length;
  return (
    <>
      <h4>
        Brief trace · {done} of {rows.length} implemented
      </h4>
      <ul className="brief-trace">
        {rows.map((r, i) => {
          const ev = Array.isArray(r.evidence) ? r.evidence.join("; ") : r.evidence;
          return (
            <li key={i} className={r.status === "implemented" ? "passed" : r.status === "not_met" ? "failed" : "not_run"}>
              {r.status === "implemented" ? <CircleCheck size={15} className="ok" /> : r.status === "not_met" ? <CircleX size={15} className="bad" /> : <CircleDashed size={15} className="muted" />}
              <span>
                <strong>{r.item}</strong>
                <span className="muted"> · {TRACE_WORDS[r.status || ""] || r.status}</span>
                {ev ? <div className="muted small">{plainText(ev)}</div> : null}
                {r.paths?.length ? <div className="muted small mono">{r.paths.join(", ")}</div> : null}
              </span>
            </li>
          );
        })}
      </ul>
    </>
  );
}

function ResultCard({ m }: { m: ChatMessage }) {
  const ctx = useProject((s) => s.ctx);
  const n: number | undefined = m.payload?.version;
  const summary = m.payload?.summary || {};
  const [open, setOpen] = useState(false);
  const build = useVersionFile<Build>(open ? ctx : null, n, "build.json");
  const tests = build.data?.tests || [];
  const digital = tests.filter((t) => t.kind === "digital");
  const physical = tests.filter((t) => t.kind !== "digital");
  return (
    <div className="msg assistant result">
      <Markdown text={plainText(endLists(m.text))} />
      <ResultChips summary={summary} />
      <details className="progress-disc" open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
        <summary>
          <ChevronRight size={14} className={open ? "rot" : ""} /> Design progress
        </summary>
        {open ? (
          build.loading ? (
            <div className="row muted small">
              <Spinner /> Loading checks
            </div>
          ) : (
            <div className="dp">
              <p className="muted small">Assessment of version {n}. Digital checks ran on the generated files; physical checks need the real parts.</p>
              <BriefTrace rows={summary?.requirements} />
              {digital.length ? <h4>Digital checks</h4> : null}
              <ul>
                {digital.map((t, i) => (
                  <li key={i} className={t.status}>
                    {t.status === "passed" ? <CircleCheck size={15} className="ok" /> : t.status === "failed" ? <CircleX size={15} className="bad" /> : t.status === "warning" ? <AlertTriangle size={15} className="warn" /> : <CircleDashed size={15} className="muted" />}
                    <span>
                      <strong>{t.title}</strong>
                      {t.detail ? <span className="muted"> · {t.detail}</span> : null}
                      {t.expected ? <div className="muted small">{t.expected}</div> : null}
                    </span>
                  </li>
                ))}
              </ul>
              {physical.length ? <h4>Physical checks still needed</h4> : null}
              <ul>
                {physical.map((t, i) => (
                  <li key={i}>
                    <Circle size={15} className="pending" />
                    <span>
                      <strong>{t.title}</strong>
                      {t.expected ? <div className="muted small">Expected: {t.expected}</div> : null}
                    </span>
                  </li>
                ))}
              </ul>
              {!tests.length ? <p className="muted small">This version lists no checks.</p> : null}
            </div>
          )
        ) : null}
      </details>
    </div>
  );
}

function ErrorCard({ m }: { m: ChatMessage }) {
  const send = useProject((s) => s.send);
  const readOnly = useProject((s) => s.readOnly);
  const turn = useProject((s) => s.turn);
  const retry = m.payload?.retryable;
  return (
    <div className="msg assistant">
      <div className="callout danger row" style={{ alignItems: "flex-start" }}>
        <AlertTriangle size={16} style={{ marginTop: 2, flex: "none" }} />
        <div className="grow">
          <Markdown text={m.text || "Something went wrong."} className="compact" />
          {retry && !readOnly ? (
            <button className="btn sm" style={{ marginTop: 8 }} disabled={!!turn?.running} onClick={() => void send(m.payload?.code === "builder" ? { action: "retry_build" } : { text: "Please try that again." })}>
              <RotateCw size={14} /> Try again
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
