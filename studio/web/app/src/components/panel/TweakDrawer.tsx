// Direct editability: colour, scale, roundness, wall, feature nudges, decor toggles and raw spec JSON.
// Apply = POST /tweak (API.md §5.1), which makes a new version and builds it.
import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Check, Eye, RotateCcw, X } from "lucide-react";
import { useProject } from "../../store/project";
import { api, ApiError } from "../../lib/api";
import { useVersionFile } from "../../lib/hooks";
import { diffMergePatch } from "../../lib/format";
import type { Version } from "../../lib/types";
import { Spinner } from "../ui";

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

  useEffect(() => {
    if (base) setJson(JSON.stringify(base, null, 2));
  }, [base]);
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
        setOkMsg(r?.warnings?.length ? `Valid, with notes: ${r.warnings.join("; ")}` : "The tweak is valid.");
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
    } finally {
      setBusy(null);
    }
  };

  const nudge = (id: string, dx: number, dz: number) => setMoves((m) => ({ ...m, [id]: [round((m[id]?.[0] || 0) + dx), round((m[id]?.[1] || 0) + dz)] }));
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
                Nudge in 2 mm steps (front view). The checker stops overlaps.
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
