// Direct editability: colour, scale, roundness, wall, feature nudges, decor toggles and raw spec JSON.
// Apply = POST /tweak (API.md §5.1), which makes a new version and builds it.
import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Check, Eye, RotateCcw, TriangleAlert, X } from "lucide-react";
import { useProject } from "../../store/project";
import { api, ApiError } from "../../lib/api";
import { useVersionFile } from "../../lib/hooks";
import { diffMergePatch } from "../../lib/format";
import type { DigitalCheck, Version } from "../../lib/types";
import { Spinner } from "../ui";

/** A layout conflict from the validator (validate_only), with an optional suggested move. */
export interface Nudge {
  text: string;
  pair?: string[];
  feature?: string;
  dx?: number; // mm, front view: + right, + up (the same axes as the nudge arrows)
  dz?: number;
  resolved?: boolean; // the pending change already moves the feature far enough
}

/** Read nudges/conflicts from a validate_only answer (or a 422's error details): {nudges: [...]}, {conflicts: [...]},
 * {layout: {nudges}}. Items are strings; web-backend rows {parts, labels, feature, move_mm, direction: up|down|left|
 * right, resolved, message}; cad's `footprint.layout_conflicts()` rows {a, b, a_part, a_what, b_part, b_what, gap_mm,
 * nudge: "<text>"}; or {message|text, pair, nudge: {feature, dx_mm, dz_mm, text}}. */
export function readNudges(r: any): Nudge[] {
  const raw = r?.nudges ?? r?.conflicts ?? r?.layout?.nudges ?? r?.layout?.conflicts ?? [];
  if (!Array.isArray(raw)) return [];
  const num = (x: any) => (typeof x === "number" && Number.isFinite(x) ? x : undefined);
  return raw
    .map((x: any): Nudge | null => {
      if (typeof x === "string") return { text: x };
      if (!x || typeof x !== "object") return null;
      const n = x.nudge && typeof x.nudge === "object" ? x.nudge : {};
      const gap = num(x.gap_mm);
      const k = num(x.move_mm);
      const dir = typeof x.direction === "string" ? x.direction : null;
      const byDir = k != null && dir ? ({ up: [0, k], down: [0, -k], right: [k, 0], left: [-k, 0] } as Record<string, number[]>)[dir] : undefined;
      const what = x.a != null && x.b != null ? `${x.a_what || x.a} and ${x.b_what || x.b}${gap != null ? (gap < 0 ? ` overlap by ${Math.abs(gap)} mm` : ` are ${gap} mm apart`) : ""}` : null;
      const text = [x.message || x.text || x.detail || what, typeof x.nudge === "string" ? x.nudge : n.text].filter(Boolean).join(": ");
      return {
        text: text || (Array.isArray(x.pair) ? x.pair.join(" and ") : "Layout conflict"),
        pair: Array.isArray(x.pair) ? x.pair.map(String) : Array.isArray(x.parts) ? x.parts.map(String) : x.a != null && x.b != null ? [String(x.a_part || x.a), String(x.b_part || x.b)] : undefined,
        feature: n.feature ?? x.feature ?? undefined,
        dx: byDir ? byDir[0] || undefined : num(n.dx_mm ?? n.dx ?? x.dx_mm),
        dz: byDir ? byDir[1] || undefined : num(n.dz_mm ?? n.dz ?? n.dy_mm ?? x.dz_mm),
        resolved: x.resolved === true,
      };
    })
    .filter(Boolean) as Nudge[];
}

const LAYOUT_CHECK = /collision|clearance|intersect|overlap/i;

const SWATCHES: [string, string][] = [
  ["#8CC9C0", "pale teal"],
  ["#F2B5C4", "blush pink"],
  ["#9CC3E6", "sky blue"],
  ["#F4D58D", "butter yellow"],
  ["#C3B1E1", "lavender"],
  ["#A8D5A2", "mint"],
  ["#F4F1EA", "cream white"],
  ["#F6B98B", "apricot"],
  ["#3A3F45", "charcoal"],
];

type Spec = Record<string, any>;

export function TweakDrawer({
  version,
  size,
  onClose,
  onLiveColor,
  onPreview,
}: {
  version: Version;
  size: [number, number, number] | null;
  onClose: () => void;
  onLiveColor: (c: string | null) => void;
  onPreview: (glbUrl: string | null) => void;
}) {
  const ctx = useProject((s) => s.ctx)!;
  const afterTweak = useProject((s) => s.afterTweak);
  const { data: spec, loading } = useVersionFile<Spec>(ctx, version.n, "spec.json");
  const base = useMemo(() => (spec ? stripDerived(spec) : null), [spec]);

  const [color, setColor] = useState<string | null>(null);
  const [colorName, setColorName] = useState<string | null>(null);
  const [scale, setScale] = useState(1);
  const [edge, setEdge] = useState<number | null>(null);
  const [wall, setWall] = useState<number | null>(null);
  const [moves, setMoves] = useState<Record<string, [number, number]>>({});
  const [decorOff, setDecorOff] = useState<Set<number>>(new Set());
  const [adv, setAdv] = useState(false);
  const [json, setJson] = useState("");
  const [jsonErr, setJsonErr] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState<null | "check" | "preview" | "apply">(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [okMsg, setOkMsg] = useState<string | null>(null);
  const [nudges, setNudges] = useState<Nudge[]>([]);
  const [baseErrors, setBaseErrors] = useState<string[]>([]); // the current rules reject the base version as it is
  const [checkingBase, setCheckingBase] = useState(true);
  const checksDoc = useVersionFile<{ checks?: DigitalCheck[] }>(ctx, version.n, "checks/digital-checks.json");
  const layoutFails = (checksDoc.data?.checks || []).filter((c) => c.status === "failed" && (c.stage === "cad" || LAYOUT_CHECK.test(`${c.id} ${c.title}`)) && LAYOUT_CHECK.test(`${c.id} ${c.title} ${c.detail || ""}`));

  useEffect(() => {
    if (base) setJson(JSON.stringify(base, null, 2));
  }, [base]);
  // MAJ-8: ask the validator about the base version once, so its layout conflicts (and the suggested moves) show
  // before any change. Servers without nudges answer with warnings only; nothing is shown then.
  useEffect(() => {
    let alive = true;
    setCheckingBase(true);
    api
      .tweak(ctx.pid, { base_version: version.n, patch: {}, validate_only: true })
      .finally(() => alive && setCheckingBase(false))
      .then((r) => {
        if (!alive) return;
        setNudges(readNudges(r));
        setBaseErrors([]);
      })
      .catch((e) => {
        if (!alive) return;
        setNudges(readNudges(e?.details));
        setBaseErrors(e instanceof ApiError && e.status === 422 ? (e.errors.length ? e.errors : [e.message]) : []);
      });
    return () => {
      alive = false;
    };
  }, [ctx.pid, version.n]);
  useEffect(() => onLiveColor(color), [color, onLiveColor]);

  if (loading || !base)
    return (
      <aside className="tweak">
        <div className="row muted" style={{ padding: 20 }}>
          <Spinner /> Loading the design spec
        </div>
      </aside>
    );

  const body = base.body || {};
  const feats: any[] = base.features || [];
  const decor: any[] = base.decor || [];
  const curEdge = edge ?? body.edge_round ?? 8;
  const curWall = wall ?? body.wall ?? 2.4;

  const buildPatch = (): { patch: any; scale?: number; summary: string[] } | { error: string } => {
    if (adv) {
      try {
        const edited = JSON.parse(json);
        return { patch: diffMergePatch(base, edited), summary: ["spec JSON edited"] };
      } catch (e: any) {
        return { error: `Spec JSON: ${e.message}` };
      }
    }
    const patch: any = {};
    const summary: string[] = [];
    if (color && color.toLowerCase() !== String(base.colors?.body || "").toLowerCase()) {
      patch.colors = { body: color, ...(colorName ? { body_name: colorName } : {}) };
      summary.push(colorName || color);
    }
    const bodyP: any = {};
    if (edge != null && edge !== body.edge_round) {
      bodyP.edge_round = edge;
      summary.push(`roundness ${edge} mm`);
    }
    if (wall != null && wall !== body.wall) {
      bodyP.wall = wall;
      summary.push(`wall ${wall} mm`);
    }
    if (Object.keys(bodyP).length) patch.body = bodyP;
    const fm: any = {};
    for (const [id, d] of Object.entries(moves)) {
      if (!d[0] && !d[1]) continue;
      const f = feats.find((x) => x.id === id);
      if (!f?.at) continue;
      fm[id] = { at: [round(f.at[0] + d[0]), round(f.at[1] + d[1])] };
      summary.push(`moved ${f.label || id}`);
    }
    if (Object.keys(fm).length) patch.features = fm;
    if (decorOff.size) {
      patch.decor = Object.fromEntries([...decorOff].map((i) => [String(i), null]));
      summary.push(`removed ${decorOff.size} decor item${decorOff.size > 1 ? "s" : ""}`);
    }
    if (scale !== 1) summary.push(`scale ${Math.round(scale * 100)}%`);
    return { patch, ...(scale !== 1 ? { scale } : {}), summary };
  };

  const built = buildPatch();
  const nChanges = "error" in built ? 0 : built.summary.length;

  const run = async (kind: "check" | "preview" | "apply") => {
    const b = buildPatch();
    if ("error" in b) {
      setErrors([b.error]);
      return;
    }
    setBusy(kind);
    setErrors([]);
    setOkMsg(null);
    const body = { base_version: version.n, patch: b.patch, ...(b.scale ? { scale: b.scale } : {}), label: label.trim() || `Tweak: ${b.summary.join(", ")}`.slice(0, 80) };
    try {
      if (kind === "check") {
        const r = await api.tweak(ctx.pid, { ...body, validate_only: true });
        const ns = readNudges(r);
        setNudges(ns);
        setOkMsg(
          ns.length
            ? "The layout rules pass with notes below. The full collision check runs when you apply."
            : r?.warnings?.length
              ? `The layout rules pass, with notes: ${r.warnings.join("; ")}. The full collision check runs when you apply.`
              : "The layout rules pass. The full collision check runs when you apply.",
        );
      } else if (kind === "preview") {
        const r = await api.preview(ctx.pid, body);
        if (r?.glb_url) onPreview(r.glb_url);
        setOkMsg(r?.warnings?.length ? `Preview ready. ${r.warnings.join("; ")}` : "Preview ready (shell only).");
      } else {
        const r = await api.tweak(ctx.pid, body);
        if (r?.version) {
          onPreview(null);
          afterTweak(r.version);
          onClose();
        }
      }
    } catch (e: any) {
      setErrors(e instanceof ApiError && e.errors.length ? e.errors : [e?.message || "The tweak failed."]);
      if (e instanceof ApiError) {
        const ns = readNudges(e.details);
        if (ns.length) setNudges(ns);
      }
    } finally {
      setBusy(null);
    }
  };

  const nudge = (id: string, dx: number, dz: number) => setMoves((m) => ({ ...m, [id]: [round((m[id]?.[0] || 0) + dx), round((m[id]?.[1] || 0) + dz)] }));
  // a suggested move is measured from the base version: set it, never add it twice
  const applyNudge = (id: string, n: Nudge) =>
    setMoves((m) => {
      const cur = m[id] || [0, 0];
      const far = (c: number, want?: number) => (want == null ? c : Math.sign(c) === Math.sign(want) && Math.abs(c) >= Math.abs(want) ? c : want);
      return { ...m, [id]: [round(far(cur[0], n.dx)), round(far(cur[1], n.dz))] };
    });
  const reset = () => {
    setColor(null);
    setColorName(null);
    setScale(1);
    setEdge(null);
    setWall(null);
    setMoves({});
    setDecorOff(new Set());
    setJson(JSON.stringify(base, null, 2));
    setErrors([]);
    setOkMsg(null);
    onPreview(null);
  };

  return (
    <aside className="tweak" aria-label="Tweak the design">
      <div className="tweak-head">
        <strong className="grow">Tweak v{version.n}</strong>
        <button className="btn ghost sm" onClick={reset} title="Reset all changes">
          <RotateCcw size={14} /> Reset
        </button>
        <button className="icon-btn sm" aria-label="Close tweak panel" onClick={onClose}>
          <X size={16} />
        </button>
      </div>
      <div className="tweak-body">
        {checkingBase ? (
          <div className="row muted small" style={{ marginBottom: 10 }} data-testid="layout-checking">
            <Spinner /> Checking v{version.n} against the layout rules…
          </div>
        ) : null}
        {layoutFails.length || nudges.length || baseErrors.length ? (
          <div className="callout warning small layout-issues" style={{ marginBottom: 10 }} data-testid="layout-issues">
            <div className="row" style={{ gap: 6, marginBottom: 4 }}>
              <TriangleAlert size={14} /> <strong>v{version.n} has layout problems</strong>
            </div>
            {baseErrors.length ? (
              <div style={{ marginBottom: 4 }}>
                The current layout rules reject v{version.n} as it is: {baseErrors.slice(0, 3).join("; ")}
              </div>
            ) : null}
            {layoutFails.map((c) => (
              <div key={c.id || c.title}>
                {c.title}
                {c.detail ? <span className="muted">: {c.detail}</span> : null}
              </div>
            ))}
            {!nudges.length && !checkingBase ? <div className="muted" style={{ marginTop: 4 }}>Nudge the features below to move them apart; Apply runs the full collision check again.</div> : null}
            {nudges.map((n, i) => {
              const f = n.feature ? feats.find((x) => x.id === n.feature) : null;
              const movable = !!f && !n.resolved && (n.dx != null || n.dz != null);
              return (
                <div key={i} className="row" style={{ gap: 6, marginTop: 4, alignItems: "flex-start" }}>
                  <span className="grow">{n.text}</span>
                  {movable ? (
                    <button className="btn sm" onClick={() => applyNudge(f!.id, n)} title="Set this move in the tweak">
                      Apply move
                    </button>
                  ) : null}
                </div>
              );
            })}
          </div>
        ) : null}
        <fieldset disabled={adv}>
          <section>
            <h4>Body colour</h4>
            <div className="swatches">
              {SWATCHES.map(([hex, name]) => {
                const on = (color || base.colors?.body || "").toLowerCase() === hex.toLowerCase();
                return (
                  <button
                    key={hex}
                    className={`sw${on ? " on" : ""}`}
                    style={{ background: hex }}
                    title={name}
                    aria-label={name}
                    onClick={() => {
                      setColor(hex);
                      setColorName(name);
                    }}
                  >
                    {on ? <Check size={14} color={hex === "#3A3F45" ? "#fff" : "#1B1F24"} /> : null}
                  </button>
                );
              })}
              <label className="sw custom" title="Custom colour">
                <input
                  type="color"
                  value={color || base.colors?.body || "#8CC9C0"}
                  onChange={(e) => {
                    setColor(e.target.value);
                    setColorName(null);
                  }}
                  aria-label="Custom colour"
                />
              </label>
            </div>
            <div className="muted small">{colorName || base.colors?.body_name || ""} {color || base.colors?.body}</div>
          </section>

          <section>
            <h4>
              Overall size <span className="val">{Math.round(scale * 100)}%</span>
            </h4>
            <input type="range" min={0.7} max={1.4} step={0.01} value={scale} onChange={(e) => setScale(+e.target.value)} aria-label="Overall scale" />
            {size ? (
              <div className="muted small">
                About {Math.round(size[0] * scale)} × {Math.round(size[1] * scale)} × {Math.round(size[2] * scale)} mm. Modules, holes and walls keep their size.
              </div>
            ) : null}
          </section>

          <section>
            <h4>
              Edge roundness <span className="val">{curEdge} mm</span>
            </h4>
            <input type="range" min={0} max={30} step={0.5} value={curEdge} onChange={(e) => setEdge(+e.target.value)} aria-label="Edge roundness" />
            <h4 style={{ marginTop: 14 }}>
              Wall thickness <span className="val">{curWall.toFixed(1)} mm</span>
            </h4>
            <input type="range" min={2} max={3.5} step={0.1} value={curWall} onChange={(e) => setWall(+e.target.value)} aria-label="Wall thickness" />
          </section>

          {feats.length ? (
            <section>
              <h4>Feature positions</h4>
              <div className="muted small" style={{ marginBottom: 8 }}>
                Nudge in 2 mm steps (front view). Check runs the layout rules; the full collision check runs when you apply.
              </div>
              {feats.map((f) => {
                const d = moves[f.id] || [0, 0];
                return (
                  <div key={f.id} className="nudge">
                    <span className="grow">
                      {f.label || f.id}
                      <span className="muted small mono">
                        {" "}
                        {f.at ? `${round(f.at[0] + d[0])}, ${round(f.at[1] + d[1])}` : ""}
                      </span>
                    </span>
                    <button className="icon-btn sm" aria-label={`Move ${f.id} left`} onClick={() => nudge(f.id, -2, 0)}>
                      <ArrowLeft size={14} />
                    </button>
                    <button className="icon-btn sm" aria-label={`Move ${f.id} right`} onClick={() => nudge(f.id, 2, 0)}>
                      <ArrowRight size={14} />
                    </button>
                    <button className="icon-btn sm" aria-label={`Move ${f.id} up`} onClick={() => nudge(f.id, 0, 2)}>
                      <ArrowUp size={14} />
                    </button>
                    <button className="icon-btn sm" aria-label={`Move ${f.id} down`} onClick={() => nudge(f.id, 0, -2)}>
                      <ArrowDown size={14} />
                    </button>
                  </div>
                );
              })}
            </section>
          ) : null}

          {decor.length ? (
            <section>
              <h4>Decoration</h4>
              {decor.map((d, i) => (
                <label key={i} className="check" style={{ display: "flex", margin: "6px 0" }}>
                  <input
                    type="checkbox"
                    checked={!decorOff.has(i)}
                    onChange={(e) => {
                      const n = new Set(decorOff);
                      if (e.target.checked) n.delete(i);
                      else n.add(i);
                      setDecorOff(n);
                    }}
                  />
                  {decorName(d)}
                </label>
              ))}
            </section>
          ) : null}
        </fieldset>

        <section>
          <label className="check">
            <input type="checkbox" checked={adv} onChange={(e) => setAdv(e.target.checked)} />
            Advanced: edit spec JSON
          </label>
          {adv ? (
            <>
              <textarea
                className={`textarea mono json-edit${jsonErr ? " invalid" : ""}`}
                spellCheck={false}
                value={json}
                onChange={(e) => {
                  setJson(e.target.value);
                  try {
                    JSON.parse(e.target.value);
                    setJsonErr(null);
                  } catch (er: any) {
                    setJsonErr(er.message);
                  }
                }}
                rows={16}
                aria-label="Spec JSON"
              />
              {jsonErr ? <div className="small" style={{ color: "var(--danger)" }}>{jsonErr}</div> : <div className="muted small">The engine validates every field and rejects unknown keys.</div>}
            </>
          ) : null}
        </section>
      </div>
      <div className="tweak-foot">
        {errors.length ? (
          <div className="callout danger small">
            {errors.map((e, i) => (
              <div key={i}>{e}</div>
            ))}
          </div>
        ) : null}
        {okMsg ? <div className="callout success small">{okMsg}</div> : null}
        <input className="input" placeholder={`Version label (optional)`} value={label} onChange={(e) => setLabel(e.target.value)} aria-label="Version label" />
        <div className="row">
          <button className="btn sm" disabled={!!busy || !nChanges} onClick={() => void run("check")}>
            {busy === "check" ? <Spinner /> : <Check size={14} />} Check
          </button>
          <button className="btn sm" disabled={!!busy || !nChanges} onClick={() => void run("preview")} title="Render the shell only (about 20 s)">
            {busy === "preview" ? <Spinner /> : <Eye size={14} />} Preview shell
          </button>
          <button className="btn primary" disabled={!!busy || !nChanges} onClick={() => void run("apply")}>
            {busy === "apply" ? <Spinner /> : null} Apply as new version
          </button>
        </div>
        <div className="muted small">{nChanges ? `${nChanges} change${nChanges > 1 ? "s" : ""}. Applying builds a new version; v${version.n} stays in history.` : "No changes yet."}</div>
      </div>
    </aside>
  );
}

function round(x: number) {
  return Math.round(x * 10) / 10;
}

function stripDerived(spec: Spec): Spec {
  const { derived: _d, ...rest } = spec;
  return rest;
}

function decorName(d: any): string {
  const t = String(d?.type || "decor").replace(/_/g, " ");
  const bits = [t];
  if (d?.style) bits.push(String(d.style).replace(/_/g, " "));
  if (d?.text) bits.push(`“${d.text}”`);
  if (d?.side) bits.push(d.side);
  if (Array.isArray(d?.at)) bits.push(`at ${d.at.join(", ")}`);
  return bits.join(" · ");
}
