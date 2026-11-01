import { useEffect, useMemo, useRef, useState } from "react";
import { Box, Eye, EyeOff, SlidersHorizontal, X } from "lucide-react";
import { useProject, lastDoneVersion } from "../../store/project";
import { useFileIndex, useVersionFile } from "../../lib/hooks";
import { fileUrl, rewriteShareUrl } from "../../lib/urls";
import { humanPart, kindLine, partKind } from "../../lib/format";
import type { Components } from "../../lib/types";
import { Viewer, ViewerControls, type ModelInfo, type SizeRef, type ViewerHandle } from "../../three/Viewer";
import { BuildProgressCard } from "../chat/Messages";
import { TweakDrawer } from "./TweakDrawer";
import type { TabProps } from "./RightPanel";

/** The decor entry (components.json PRINT_Shell.decor[]) a decor selection such as "PRINT_Shell#decor[1]" names. */
export function decorInfo(components: Components | null, name: string) {
  if (!name.includes("#decor")) return null;
  const base = name.split("#decor")[0];
  return components?.[base]?.decor?.find((d) => d.name === name) || null;
}

export function partLabeler(components: Components | null) {
  return (name: string) => {
    const d = decorInfo(components, name);
    if (d) {
      return { title: (d.label || "decor").toUpperCase(), sub: `Part of the main shell${d.spec_path ? ` (${d.spec_path})` : ""}.` };
    }
    const c = components?.[name];
    const kind = partKind(name, c?.kind);
    return { title: (c?.label || humanPart(name)).toUpperCase(), sub: kindLine(kind) };
  };
}

export function PreviewTab({ version }: TabProps) {
  const project = useProject((s) => s.project)!;
  const ctx = useProject((s) => s.ctx)!;
  const statuses = useProject((s) => s.statuses);
  const readOnly = useProject((s) => s.readOnly);
  const send = useProject((s) => s.send);
  const turn = useProject((s) => s.turn);
  const handle = useRef<ViewerHandle | null>(null);

  const [explodeOn, setExplodeOn] = useState(false);
  const [explode, setExplode] = useState(0.6);
  const [separate, setSeparate] = useState(false);
  const [xray, setXray] = useState(false);
  const [dimsOn, setDimsOn] = useState(false);
  const [sizeRefOn, setSizeRefOn] = useState(false);
  const [sizeRef, setSizeRef] = useState<SizeRef>("auto");
  const [partsOn, setPartsOn] = useState(false);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const [info, setInfo] = useState<ModelInfo | null>(null);
  const [tweakOpen, setTweakOpen] = useState(false);
  const [liveColor, setLiveColor] = useState<string | null>(null);
  const [tweakPreview, setTweakPreview] = useState<string | null>(null);

  // which model to show
  const progressMsg = project.chat.find((m) => m.kind === "progress" && m.payload?.version === version.n);
  const status = statuses[version.n] || progressMsg?.payload?.status;
  const building = version.state === "queued" || version.state === "running";
  const last = lastDoneVersion(project);
  const concept = project.concepts.find((c) => c.id === project.selected_concept);
  let url: string | null = null;
  let mode: "final" | "draft" | "previous" | "concept" | "none" = "none";
  if (version.state === "done") {
    url = fileUrl(ctx, version.n, "cad/assembly.glb");
    mode = "final";
  } else if (building) {
    const cadDone = status?.stages?.find((s) => s.id === "cad" && s.state === "done");
    if (status?.draft_url) {
      url = rewriteShareUrl(status.draft_url, ctx.share);
      mode = "draft";
    } else if (cadDone) {
      url = `${fileUrl(ctx, version.n, "cad/assembly.glb")}?draft=${encodeURIComponent(cadDone.ended || "1")}`;
      mode = "draft";
    } else if (last) {
      url = fileUrl(ctx, last.n, "cad/assembly.glb");
      mode = "previous";
    } else if (concept?.glb_url) {
      url = rewriteShareUrl(concept.glb_url, ctx.share);
      mode = "concept";
    }
  } else if (last) {
    url = fileUrl(ctx, last.n, "cad/assembly.glb");
    mode = "previous";
  }
  const compsN = mode === "final" ? version.n : mode === "previous" && last ? last.n : null;
  const idx = useFileIndex(ctx, compsN);
  const noModel = compsN != null && idx instanceof Set && !idx.has("cad/assembly.glb");
  if (noModel || (compsN != null && idx === undefined)) url = null;
  if (tweakPreview) url = tweakPreview;

  const comps = useVersionFile<Components>(ctx, compsN, "cad/components.json");
  const label = useMemo(() => partLabeler(comps.data), [comps.data]);
  useEffect(() => {
    setSelected(null);
    setHidden(new Set());
  }, [url]);

  const sizeText = info?.size ? `${info.size[0]} mm × ${info.size[1]} mm × ${info.size[2]} mm` : null;
  const toggles: [string, boolean, (v: boolean) => void][] = [
    ["Exploded view", explodeOn, (v) => {
      setExplodeOn(v);
      if (v) setSeparate(false);
    }],
    ["Separate parts", separate, (v) => {
      setSeparate(v);
      if (v) setExplodeOn(false);
    }],
    ["Transparent", xray, setXray],
    ["Dimensions", dimsOn, setDimsOn],
    ["Size reference", sizeRefOn, setSizeRefOn],
    ["Model parts", partsOn, setPartsOn],
  ];
  return (
    <div className="preview">
      <div className="view-toggles" role="group" aria-label="View options">
        {toggles.map(([lab, on, set]) => (
          <label key={lab} className={`check${on ? " on" : ""}`}>
            <input type="checkbox" checked={on} onChange={(e) => set(e.target.checked)} disabled={!url} />
            {lab}
          </label>
        ))}
        {explodeOn ? (
          <input type="range" min={0.1} max={1.4} step={0.01} value={explode} onChange={(e) => setExplode(+e.target.value)} aria-label="Explode distance" style={{ width: 110 }} />
        ) : null}
        {sizeRefOn ? (
          <select className="select sm" value={sizeRef} onChange={(e) => setSizeRef(e.target.value as SizeRef)} aria-label="Reference object">
            <option value="auto">Automatic</option>
            <option value="apple">Apple</option>
            <option value="toy">Toy figure</option>
            <option value="person">Person</option>
          </select>
        ) : null}
        <div className="grow" />
        {!readOnly && version.state === "done" ? (
          <button className={`btn sm${tweakOpen ? " active" : ""}`} onClick={() => setTweakOpen(!tweakOpen)} aria-expanded={tweakOpen}>
            <SlidersHorizontal size={15} /> Tweak
          </button>
        ) : null}
      </div>
      {dimsOn && sizeText ? (
        <div className="dims-line">
          Assembly: {sizeText} · {mode === "concept" ? "concept shell" : `version ${mode === "previous" && last ? last.n : version.n}`}
        </div>
      ) : null}
      {mode === "draft" || (building && mode !== "final") ? (
        <div className="draft-banner">
          <strong>{mode === "draft" ? "3D draft · Work in progress" : `Building v${version.n}`}</strong>
          <span className="muted">
            {mode === "draft"
              ? "Wish Labs is working on this draft. It may still change; your saved version is unchanged."
              : mode === "previous"
                ? `Showing v${last?.n} until the new shell is ready.`
                : mode === "concept"
                  ? "Showing the chosen concept's shell until the build's CAD is ready."
                  : "The 3D draft appears here as soon as the shell is modelled."}
          </span>
        </div>
      ) : null}
      {tweakPreview ? (
        <div className="draft-banner">
          <strong>Tweak preview · shell only</strong>
          <span className="muted">Not saved. Apply the tweak to make a new version.</span>
          <button className="btn sm" onClick={() => setTweakPreview(null)}>
            Back to v{version.n}
          </button>
        </div>
      ) : null}
      <div className={`stage${mode === "draft" || mode === "concept" ? " draft" : ""}`}>
        {url ? (
          <Viewer
            url={url}
            bodyColor={liveColor}
            xray={xray}
            explode={explodeOn ? explode : 0}
            separate={separate}
            showBox={dimsOn}
            sizeRef={sizeRefOn ? sizeRef : "none"}
            hidden={hidden}
            selectable={!building || mode === "draft"}
            selected={selected}
            onSelect={setSelected}
            onInfo={setInfo}
            handle={handle}
            className={mode === "previous" && building ? "faded" : ""}
          >
            <ViewerControls handle={handle} />
          </Viewer>
        ) : (
          <div className="empty">
            {building && !url && !noModel ? (
              <div style={{ width: "min(420px, 90%)" }}>
                <BuildProgressCard m={progressMsg} />
              </div>
            ) : idx === undefined && compsN != null ? (
              <span className="spinner lg" />
            ) : (
              <>
                <div className="icon-wrap">
                  <Box size={22} />
                </div>
                <h3>No 3D model in this version yet</h3>
                <p style={{ margin: 0, maxWidth: 420 }}>The CAD stage did not deliver cad/assembly.glb for v{compsN ?? version.n}. The other tabs show what was made.</p>
              </>
            )}
          </div>
        )}
        {building && url && progressMsg ? (
          <div className="stage-progress">
            <BuildProgressCard m={progressMsg} compact />
          </div>
        ) : null}
        {selected ? (
          <EditPartPopover
            name={selected}
            title={label(selected).title}
            sub={label(selected).sub}
            size={info?.parts.find((p) => p.name === selected)?.size}
            readOnly={readOnly || !!turn?.running}
            onClose={() => setSelected(null)}
            onApply={(text) => {
              void send({ action: "edit_part", component: selected, instruction: text });
              setSelected(null);
            }}
          />
        ) : null}
        {partsOn && info ? (
          <aside className="parts-side" aria-label="Model parts">
            <div className="row" style={{ padding: "12px 12px 6px" }}>
              <strong className="grow">Model parts</strong>
              <button className="linkish" onClick={() => setHidden(new Set())}>
                Show all
              </button>
            </div>
            <div className="parts-side-list">
              {info.parts.map((p) => {
                const off = hidden.has(p.name);
                return (
                  <button
                    key={p.name}
                    className={`part-row${off ? " off" : ""}${selected === p.name ? " sel" : ""}`}
                    onClick={() => {
                      const n = new Set(hidden);
                      if (off) n.delete(p.name);
                      else n.add(p.name);
                      setHidden(n);
                    }}
                    title={p.name}
                  >
                    {off ? <EyeOff size={15} /> : <Eye size={15} />}
                    <span className="grow">{comps.data?.[p.name]?.label || humanPart(p.name)}</span>
                    <span className="muted small">{partKind(p.name, comps.data?.[p.name]?.kind) === "PRINT" ? "print" : partKind(p.name) === "REFERENCE" ? "ref" : ""}</span>
                  </button>
                );
              })}
            </div>
          </aside>
        ) : null}
        {tweakOpen && version.state === "done" ? (
          <TweakDrawer
            version={version}
            size={info?.size || null}
            onClose={() => {
              setTweakOpen(false);
              setLiveColor(null);
            }}
            onLiveColor={setLiveColor}
            onPreview={setTweakPreview}
          />
        ) : null}
      </div>
    </div>
  );
}

function EditPartPopover({
  name,
  title,
  sub,
  size,
  readOnly,
  onClose,
  onApply,
}: {
  name: string;
  title: string;
  sub?: string;
  size?: [number, number, number];
  readOnly: boolean;
  onClose: () => void;
  onApply: (text: string) => void;
}) {
  const [text, setText] = useState("");
  const ta = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    setText("");
    ta.current?.focus();
  }, [name]);
  const human = title.charAt(0) + title.slice(1).toLowerCase();
  return (
    <div className="edit-pop" role="dialog" aria-label={`Edit ${human}`}>
      <div className="row">
        <strong className="grow">Edit {human}</strong>
        <button className="icon-btn sm" aria-label="Close" onClick={onClose}>
          <X size={16} />
        </button>
      </div>
      <div className="muted small" style={{ margin: "2px 0 10px" }}>
        {sub} {size ? `${size[0]} × ${size[1]} × ${size[2]} mm.` : ""} <span className="mono">{name}</span>
      </div>
      {!readOnly ? (
        <>
          <textarea
            ref={ta}
            className="textarea"
            rows={3}
            placeholder="e.g. Make this part orange"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && text.trim()) onApply(text.trim());
            }}
          />
          <button className="btn primary block" style={{ marginTop: 10 }} disabled={!text.trim()} onClick={() => onApply(text.trim())}>
            Apply to this part
          </button>
          <div className="muted small" style={{ marginTop: 8 }}>
            Makes a new version. Your current version stays in Version history.
          </div>
        </>
      ) : null}
    </div>
  );
}
