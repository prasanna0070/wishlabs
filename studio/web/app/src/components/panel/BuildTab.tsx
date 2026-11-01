import { useEffect, useMemo, useRef, useState } from "react";
import { BookOpen, Cable, ChevronDown, ChevronLeft, ChevronRight, Download, Tags, Wrench } from "lucide-react";
import { useProject } from "../../store/project";
import { useFileIndex, useVersionFile } from "../../lib/hooks";
import { fileUrl, resolveDocUrl } from "../../lib/urls";
import { humanPart, softBreaks } from "../../lib/format";
import { groupUse, splitUse, type UseItem } from "../../lib/steps";
import type { Build, BuildStep, CableRoute, Components } from "../../lib/types";
import { Viewer, ViewerControls, type Label, type ViewerHandle } from "../../three/Viewer";
import { Dialog, Lightbox, Markdown, Ring, Spinner, ZoomPan } from "../ui";
import { partLabeler } from "./PreviewTab";
import { DeviceTools } from "./DeviceTools";
import type { TabProps } from "./RightPanel";

type Track = "assembly" | "wiring";

export default function BuildTab({ version }: TabProps) {
  const ctx = useProject((s) => s.ctx)!;
  const project = useProject((s) => s.project)!;
  const build = useVersionFile<Build>(ctx, version.n, "build.json");
  const comps = useVersionFile<Components>(ctx, version.n, "cad/components.json");
  const [track, setTrack] = useState<Track>("assembly");
  const [manual, setManual] = useState(false);

  if (build.loading)
    return (
      <div className="empty">
        <Spinner lg />
      </div>
    );
  if (!build.data) return <div className="empty">This version has no build manual (build.json is missing).</div>;
  const steps = build.data.steps || [];
  const wsteps = build.data.wiring?.steps || [];
  const list = track === "assembly" ? steps : wsteps;
  const checks = project.checklist?.[track] || {};
  const done = list.filter((s) => checks[s.id]).length;

  return (
    <div className="tab-scroll">
      <div className="tab-inner build">
        <div className="sec-head">
          <h2>Build manual</h2>
          <div className="grow" />
          <button className="btn primary" onClick={() => setManual(true)}>
            <BookOpen size={15} /> Complete manual · included
          </button>
        </div>
        <div className="subtabs">
          <div className="subtab-group" role="tablist">
            <button role="tab" aria-selected={track === "assembly"} className={track === "assembly" ? "on" : ""} onClick={() => setTrack("assembly")}>
              <Wrench size={15} /> Assembly
            </button>
            <button role="tab" aria-selected={track === "wiring"} className={track === "wiring" ? "on" : ""} onClick={() => setTrack("wiring")}>
              <Cable size={15} /> Wiring
            </button>
          </div>
          <div className="grow" />
          <span className="muted small">
            {done} of {list.length} done
          </span>
        </div>
        {track === "wiring" ? (
          <p className="muted" style={{ margin: "0 0 12px" }}>
            Wiring from this version's electrical plan and backplane. Check each connection against the manual and the parts before you power the board.
          </p>
        ) : null}
        <StepStage key={track} track={track} steps={list} build={build.data} comps={comps.data} versionN={version.n} />
        <Checklist track={track} steps={list} build={build.data} versionN={version.n} />
        {track === "wiring" ? <WiringDetails build={build.data} versionN={version.n} /> : null}
        <DeviceTools version={version} build={build.data} />
      </div>
      <ManualDialog open={manual} onClose={() => setManual(false)} versionN={version.n} />
    </div>
  );
}

function StepStage({ track, steps, build, comps, versionN }: { track: Track; steps: BuildStep[]; build: Build; comps: Components | null; versionN: number }) {
  const ctx = useProject((s) => s.ctx)!;
  const project = useProject((s) => s.project)!;
  const setChecked = useProject((s) => s.setChecked);
  const readOnly = useProject((s) => s.readOnly);
  const checks = project.checklist?.[track] || {};
  const firstOpen = Math.max(0, steps.findIndex((s) => !checks[s.id]));
  const [i, setI] = useState(firstOpen);
  const [labelsOn, setLabelsOn] = useState(true);
  const handle = useRef<ViewerHandle | null>(null);
  const anchors = useVersionFile<Record<string, number[]>>(track === "wiring" ? ctx : null, versionN, "cad/anchors.json");
  const routes = useVersionFile<CableRoute[] | { routes: CableRoute[] }>(track === "wiring" ? ctx : null, versionN, "cad/cable-routes.json");
  const elec = useVersionFile<any>(track === "wiring" ? ctx : null, versionN, "electrical.json");
  const step = steps[Math.min(i, steps.length - 1)];
  const label = useMemo(() => partLabeler(comps), [comps]);
  const idx = useFileIndex(ctx, versionN);

  const hl = step ? (step.highlight?.length ? step.highlight : step.parts?.length ? step.parts : null) : null;
  // callouts only for the parts this step uses (highlight can include context parts that are only shown)
  const labelParts = step?.highlight?.length && step?.parts?.length ? step.highlight.filter((h) => step.parts!.includes(h)) : hl;
  const card = useRef<HTMLDivElement>(null);
  const [shift, setShift] = useState(0);
  useEffect(() => {
    const el = card.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const upd = () => {
      const host = el.parentElement;
      const right = el.offsetLeft + el.offsetWidth;
      // centre the model in the space right of the card, unless the card fills the view (narrow screens)
      setShift(host && right < host.clientWidth * 0.6 ? Math.round(right / 2) : 0);
    };
    upd();
    const ro = new ResizeObserver(upd);
    ro.observe(el);
    if (el.parentElement) ro.observe(el.parentElement);
    return () => ro.disconnect();
  }, [!!step]);
  const wiringParts = build.wiring?.parts || [];
  const connsAll = build.wiring?.connections || [];
  const slotRef = new Map<string, string>(((elec.data?.instances as any[]) || []).filter((x) => x.slot).map((x) => [String(x.slot), String(x.ref)]));
  const shortName = (id: string) => {
    const n = wiringParts.find((p) => p.id === id || p.id?.toUpperCase() === id)?.name;
    if (!n) return null;
    const t = n.replace(/\s*\(.*?\)\s*/g, " ").trim();
    return t.length > 34 ? `${t.slice(0, 32).trimEnd()}…` : t;
  };
  const labelFor = (a: string): Label => {
    const [ref, ...rest] = a.split(".");
    const conn = rest.join(".");
    const part = wiringParts.find((p) => p.id === ref);
    const isBackplane = ref === "BP1" || /BACKPLANE/i.test(part?.module_id || "");
    if (conn && isBackplane) {
      const plugged = slotRef.get(conn) || connsAll.find((c) => c.to === `${ref}.${conn}` || c.to?.startsWith(`${ref}.${conn}.`))?.from?.split(".")[0];
      const what = plugged ? shortName(plugged) || plugged : null;
      return { id: a, at: anchors.data![a], title: `${conn.replace(/_/g, " ")} ${/^J_/.test(conn) ? "port" : "socket"}`.toUpperCase(), sub: what ? `${what} plugs in here.` : "On the backplane." };
    }
    return { id: a, at: anchors.data![a], title: (shortName(ref) || ref).toUpperCase(), sub: conn ? conn.replace(/_/g, " ").toLowerCase() : part?.kind || "" };
  };
  const labels: Label[] | null =
    track === "wiring" && labelsOn && step?.labels?.length && anchors.data ? step.labels.filter((a) => anchors.data![a]).map(labelFor) : null;
  const routeList: CableRoute[] = Array.isArray(routes.data) ? routes.data : (routes.data as any)?.routes || [];
  const conns = connsAll;
  const cables =
    track === "wiring" && routeList.length
      ? routeList.map((r) => {
          const c = conns.find((x) => (r.from && x.from?.startsWith(r.from.split(".")[0])) || (r.to && x.from?.startsWith(r.to.split(".")[0])));
          const highlight = !step?.highlight?.length || step.highlight.some((h) => (r.id && h.includes(r.id)) || (r.to && h.includes(r.to.split(".")[0])) || /REFERENCE_Cable/i.test(h));
          return { ...r, color: r.color || colorHex(c?.color) || "#3046D9", highlight };
        })
      : null;

  if (!steps.length) return <div className="card pad muted">No {track} steps in this version.</div>;
  const url = idx instanceof Set && !idx.has("cad/assembly.glb") ? null : idx === undefined ? null : fileUrl(ctx, versionN, "cad/assembly.glb");
  const advance = () => {
    if (!readOnly && step && !checks[step.id]) void setChecked(track, step.id, true);
    setI((v) => Math.min(steps.length - 1, v + 1));
  };
  return (
    <div className="step-stage">
      <Viewer
        url={url}
        highlight={hl}
        isolate={track === "assembly"}
        partLabels={labelsOn && track === "assembly"}
        partLabel={label}
        labelParts={labelParts}
        maxLabels={10}
        avoid=".step-card, .viewer-ctl, .stage-pill, .step-parts"
        viewShift={shift}
        labels={labels}
        cables={cables}
        hideKinds={track === "wiring" && cables?.length ? ["REFERENCE"] : undefined}
        camera={step?.camera || null}
        handle={handle}
        xray={track === "wiring"}
      >
        {track === "wiring" && url ? <span className="stage-pill">Simplified parts at about real size, not manufacturer CAD.</span> : null}
        {!url && idx instanceof Set ? <div className="viewer-status">No 3D model in this version; follow the steps and pictures below.</div> : null}
        <ViewerControls handle={handle}>
          <button className={`icon-btn sm${labelsOn ? " active" : ""}`} aria-pressed={labelsOn} aria-label="Show labels" title="Labels" onClick={() => setLabelsOn(!labelsOn)}>
            <Tags size={16} />
          </button>
        </ViewerControls>
      </Viewer>
      {step ? (
        <div className="step-card" aria-live="polite" ref={card}>
          <div className="muted small">
            Step {i + 1} of {steps.length}
            {checks[step.id] ? <span className="badge success" style={{ marginLeft: 8 }}>done</span> : null}
          </div>
          <h3>{step.title}</h3>
          <div className="step-card-body">
            <StepText key={step.id} md={step.instruction || step.text || ""} build={build} />
            {step.expected ? (
              <p className="done-when">
                <strong>Done when:</strong> {step.expected}
              </p>
            ) : null}
          </div>
          <div className="step-card-foot">
            <button className="icon-btn sm" aria-label="Previous step" disabled={i === 0} onClick={() => setI(i - 1)}>
              <ChevronLeft size={18} />
            </button>
            <div className="step-dots" aria-hidden="true">
              {steps.map((s, k) => (
                <span key={s.id} className={`${k === i ? "cur" : ""}${checks[s.id] ? " ok" : ""}`} onClick={() => setI(k)} />
              ))}
            </div>
            <div className="grow" />
            {i < steps.length - 1 ? (
              <button className="btn primary sm" onClick={advance}>
                Next step <ChevronRight size={15} />
              </button>
            ) : (
              <button className="btn primary sm" disabled={readOnly || !!checks[step.id]} onClick={advance}>
                {checks[step.id] ? "All done" : "Mark done"}
              </button>
            )}
          </div>
        </div>
      ) : null}
      {hl && comps && track === "assembly" ? (
        <div className="step-parts small muted" style={shift ? { left: shift * 2 + 12, maxWidth: `calc(100% - ${shift * 2 + 28}px)` } : undefined}>
          Shown: {hl.slice(0, 6).map((n) => comps[n]?.label || humanPart(n)).join(", ")}
          {hl.length > 6 ? ` +${hl.length - 6}` : ""}
        </div>
      ) : null}
    </div>
  );
}

/** Step text with a long "Use:" list shown grouped by role with counts; the full list is behind "Show all parts". */
export function StepText({ md, build }: { md: string; build: Build | null }) {
  const [all, setAll] = useState(false);
  const split = useMemo(() => splitUse(md, build), [md, build]);
  if (!split || split.items.length <= 4) return <Markdown text={softBreaks(md)} className="compact" />;
  const groups = groupUse(split.items);
  const pieces = split.items.reduce((a, i) => a + i.qty, 0);
  return (
    <>
      <div className="use-list" data-testid="use-list">
        <div className="use-head">
          <strong>Use:</strong>
          {groups.map((g) => (
            <span key={g.role} className="use-chip" title={g.items.map((i) => `${i.qty} × ${i.name}`).join("\n")}>
              {g.label} <b>{g.pieces}</b>
            </span>
          ))}
        </div>
        <button className="linkish small use-toggle" onClick={() => setAll(!all)} aria-expanded={all}>
          <ChevronDown size={13} className={all ? "rot180" : ""} /> {all ? "Hide the parts" : `Show all parts (${split.items.length} lines, ${pieces} pieces)`}
        </button>
        {all ? (
          <div className="use-all">
            {groups.map((g) => (
              <div key={g.role}>
                <div className="muted small">{g.label}</div>
                <ul>
                  {g.items.map((i: UseItem, k: number) => (
                    <li key={k}>
                      {i.qty} × {i.name}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        ) : null}
      </div>
      <Markdown text={softBreaks(split.rest)} className="compact" />
    </>
  );
}

function colorHex(c?: string): string | null {
  if (!c) return null;
  if (c.startsWith("#")) return c;
  const map: Record<string, string> = {
    black: "#2B2B2B",
    red: "#D64541",
    blue: "#2F6FD6",
    yellow: "#E8B92F",
    green: "#2E9E5B",
    white: "#F2F2F2",
    orange: "#EE8A2F",
    purple: "#8A5CD6",
    brown: "#8B5A2B",
    grey: "#8D9096",
    gray: "#8D9096",
  };
  return map[c.toLowerCase()] || null;
}

function Checklist({ track, steps, build: _b, versionN }: { track: Track; steps: BuildStep[]; build: Build; versionN: number }) {
  const ctx = useProject((s) => s.ctx)!;
  const project = useProject((s) => s.project)!;
  const setChecked = useProject((s) => s.setChecked);
  const readOnly = useProject((s) => s.readOnly);
  const checks = project.checklist?.[track] || {};
  const done = steps.filter((s) => checks[s.id]).length;
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [lb, setLb] = useState<{ src: string; alt?: string; caption?: string } | null>(null);
  if (!steps.length) return null;
  return (
    <details className="checklist card" open>
      <summary className="checklist-head">
        <Ring value={done} total={steps.length} />
        <div className="grow">
          <strong>Checklist, step pictures and final checks</strong>
          <div className="muted small">
            {done} of {steps.length} steps done · saved to this project
          </div>
        </div>
        <ChevronRight size={16} className="chev" />
      </summary>
      <ol className="check-steps">
        {steps.map((s, k) => {
          const on = !!checks[s.id];
          const exp = !!open[s.id];
          return (
            <li key={s.id} className={on ? "done" : ""}>
              <div className="cs-row">
                <input type="checkbox" checked={on} disabled={readOnly} onChange={(e) => void setChecked(track, s.id, e.target.checked)} aria-label={`Mark step ${k + 1} done`} />
                <button className="cs-title" onClick={() => setOpen({ ...open, [s.id]: !exp })} aria-expanded={exp}>
                  <span className="muted">{k + 1}.</span> {s.title}
                  {s.visuals?.length ? <span className="badge" style={{ marginLeft: 8 }}>{s.visuals.length} image{s.visuals.length > 1 ? "s" : ""}</span> : null}
                </button>
                <ChevronRight size={15} className={exp ? "rot" : ""} />
              </div>
              {exp ? (
                <div className="cs-body">
                  {(s.visuals || []).map((v, j) => {
                    const src = fileUrl(ctx, versionN, v.path);
                    return (
                      <figure key={j} className="cs-fig">
                        <img src={src} alt={v.alt || s.title} loading="lazy" onClick={() => setLb({ src, alt: v.alt, caption: v.caption })} />
                        {v.caption ? <figcaption>{v.caption}</figcaption> : null}
                      </figure>
                    );
                  })}
                  <Markdown text={softBreaks(s.instruction || s.text || "")} className="compact" />
                  {s.expected ? (
                    <p className="done-when">
                      <strong>Done when:</strong> {s.expected}
                    </p>
                  ) : null}
                </div>
              ) : null}
            </li>
          );
        })}
      </ol>
      <Lightbox src={lb?.src || null} alt={lb?.alt} caption={lb?.caption} onClose={() => setLb(null)} />
    </details>
  );
}

function WiringDetails({ build, versionN }: { build: Build; versionN: number }) {
  const ctx = useProject((s) => s.ctx)!;
  const conns = build.wiring?.connections || [];
  const idx = useFileIndex(ctx, versionN);
  const svgOk = idx instanceof Set ? idx.has("manual/wiring.svg") : idx === null;
  return (
    <>
      {conns.length ? (
        <div className="card pad" style={{ marginTop: 16 }}>
          <h3 style={{ fontSize: 15, marginBottom: 8 }}>Connections</h3>
          <table className="table">
            <thead>
              <tr>
                <th>From</th>
                <th />
                <th>To</th>
                <th>Cable</th>
                <th>Wire</th>
              </tr>
            </thead>
            <tbody>
              {conns.map((c, i) => (
                <tr key={i}>
                  <td className="mono small">{c.from}</td>
                  <td className="muted">→</td>
                  <td className="mono small">{c.to}</td>
                  <td>
                    {c.via || c.kind || ""}
                    {c.note ? <div className="muted small">{c.note}</div> : null}
                  </td>
                  <td className="nowrap">
                    {c.color ? (
                      <>
                        <span className="swatch" style={{ background: colorHex(c.color) || c.color }} /> {c.color}
                      </>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {svgOk ? (
        <div className="card" style={{ marginTop: 16, overflow: "hidden" }}>
          <div className="row" style={{ padding: "12px 14px" }}>
            <h3 className="grow" style={{ fontSize: 15 }}>
              Wiring diagram
            </h3>
            <a className="btn sm" href={fileUrl(ctx, versionN, "manual/wiring.svg", true)}>
              <Download size={14} /> SVG
            </a>
          </div>
          <ZoomPan src={fileUrl(ctx, versionN, "manual/wiring.svg")} alt="Wiring diagram: backplane ports to modules" height={420} />
        </div>
      ) : null}
    </>
  );
}

function ManualDialog({ open, onClose, versionN }: { open: boolean; onClose: () => void; versionN: number }) {
  const ctx = useProject((s) => s.ctx)!;
  const md = useVersionFile<string>(open ? ctx : null, versionN, "BUILD_GUIDE.md", "text");
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Complete build manual"
      sub={`BUILD_GUIDE.md · version ${versionN} · included with every build`}
      wide
      footer={
        <a className="btn" href={fileUrl(ctx, versionN, "BUILD_GUIDE.md", true)}>
          <Download size={15} /> Download BUILD_GUIDE.md
        </a>
      }
    >
      {md.loading ? (
        <div className="row muted">
          <Spinner /> Loading the manual
        </div>
      ) : md.data ? (
        <Markdown text={md.data} className="manual-md" resolveImg={(src) => resolveDocUrl(ctx, versionN, src)} />
      ) : (
        <div className="muted">This version has no BUILD_GUIDE.md.</div>
      )}
    </Dialog>
  );
}
