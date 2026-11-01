import { useRef, useState } from "react";
import { CircleCheck, CircleX, Download, FileArchive, FileSpreadsheet, FileCode2 } from "lucide-react";
import { useProject } from "../../store/project";
import { useFileIndex, useVersionFile } from "../../lib/hooks";
import { fileUrl } from "../../lib/urls";
import type { Build } from "../../lib/types";
import { Viewer, ViewerControls, type ViewerHandle } from "../../three/Viewer";
import { Spinner, ZoomPan } from "../ui";
import type { TabProps } from "./RightPanel";

type View = "top" | "bottom" | "3d" | "schematic";

export default function PcbTab({ version }: TabProps) {
  const ctx = useProject((s) => s.ctx)!;
  const build = useVersionFile<Build>(ctx, version.n, "build.json");
  const elec = useVersionFile<any>(ctx, version.n, "electrical.json");
  const drc = useVersionFile<any>(ctx, version.n, "pcb/drc.json");
  const erc = useVersionFile<any>(ctx, version.n, "pcb/erc.json");
  const idx = useFileIndex(ctx, version.n);
  const [view, setView] = useState<View>("top");
  const handle = useRef<ViewerHandle | null>(null);

  if (build.loading)
    return (
      <div className="empty">
        <Spinner lg />
      </div>
    );
  const pcb = build.data?.pcb;
  if (!pcb) return <div className="empty">This version has no backplane PCB.</div>;
  const instances: any[] = elec.data?.instances || [];
  const ok = (v?: number | null) => v === 0;
  const f = (p: string) => fileUrl(ctx, version.n, p);
  const dl = (p: string) => fileUrl(ctx, version.n, p, true);
  const has = (p: string) => (idx instanceof Set ? idx.has(p) : true);
  const missing = (what: string) => (
    <div className="empty" style={{ height: 300 }}>
      <span>No {what} in this version.</span>
    </div>
  );

  return (
    <div className="tab-scroll">
      <div className="tab-inner">
        <div className="sec-head">
          <h2>Backplane PCB</h2>
          <div className="grow" />
          <a className="btn primary" href={dl("pcb/backplane-gerbers.zip")} aria-disabled={!has("pcb/backplane-gerbers.zip")}>
            <FileArchive size={15} /> Gerbers for fabrication
          </a>
        </div>
        <p className="muted" style={{ margin: "0 0 14px" }}>
          The same standard core board in every gadget: the ESP32-S3 sits in sockets and each module plugs into a keyed port. This design populates only the ports it uses.
        </p>
        <div className="pcb-chips">
          <StatusChip ok={ok(pcb.drc_errors)} label={`DRC ${pcb.drc_errors ?? "?"} errors`} />
          <StatusChip ok={ok(pcb.erc_errors)} label={`ERC ${pcb.erc_errors ?? "?"} errors`} />
          <StatusChip ok={!!pcb.routed} label={pcb.routed ? "All nets routed" : `${pcb.unrouted ?? "?"} unrouted`} />
          {pcb.layers ? <span className="pill">{pcb.layers} layers</span> : null}
          {pcb.size_mm ? (
            <span className="pill">
              {pcb.size_mm[0]} × {pcb.size_mm[1]} mm
            </span>
          ) : null}
        </div>

        <div className="card pcb-view">
          <div className="row" style={{ padding: "10px 12px", borderBottom: "1px solid var(--line)" }}>
            <div className="seg" role="tablist" aria-label="Board view">
              {(
                [
                  ["top", "Top"],
                  ["bottom", "Bottom"],
                  ["3d", "3D board"],
                  ["schematic", "Schematic"],
                ] as [View, string][]
              ).map(([id, lab]) => (
                <button key={id} role="tab" aria-selected={view === id} className={view === id ? "on" : ""} onClick={() => setView(id)}>
                  {lab}
                </button>
              ))}
            </div>
            <div className="grow" />
            <span className="muted small">{view === "3d" ? "Drag to orbit" : "Scroll to zoom, drag to pan, double-click to reset"}</span>
          </div>
          {view === "top" ? has("pcb/board-top.png") ? <ZoomPan src={f("pcb/board-top.png")} alt="Board top render" height={460} background="#F3F2EE" /> : missing("top render") : null}
          {view === "bottom" ? has("pcb/board-bottom.png") ? <ZoomPan src={f("pcb/board-bottom.png")} alt="Board bottom render" height={460} background="#F3F2EE" /> : missing("bottom render") : null}
          {view === "schematic" ? has("pcb/schematic.svg") ? <ZoomPan src={f("pcb/schematic.svg")} alt="Backplane schematic" height={520} background="#FFFFFF" /> : missing("schematic") : null}
          {view === "3d" && !has("pcb/board.glb") ? missing("3D board") : null}
          {view === "3d" && has("pcb/board.glb") ? (
            <div style={{ height: 460, position: "relative" }}>
              <Viewer url={f("pcb/board.glb")} autoFlat handle={handle} ariaLabel="3D board">
                <ViewerControls handle={handle} />
              </Viewer>
            </div>
          ) : null}
        </div>

        <div className="grid2" style={{ marginTop: 16 }}>
          <div className="card pad">
            <h3 style={{ fontSize: 15, marginBottom: 8 }}>Port map</h3>
            {elec.loading ? (
              <Spinner />
            ) : instances.length ? (
              <table className="table">
                <thead>
                  <tr>
                    <th>Port</th>
                    <th>Module</th>
                    <th>Cable</th>
                  </tr>
                </thead>
                <tbody>
                  {instances
                    .filter((i) => i.slot)
                    .map((i) => (
                      <tr key={i.ref}>
                        <td className="mono small">{i.slot}</td>
                        <td>
                          <strong>{i.ref}</strong> <span className="muted">{i.feature ? `· ${String(i.feature).replace(/_/g, " ")}` : ""}</span>
                          <div className="muted small mono">{i.module}</div>
                        </td>
                        <td className="small">{i.harness ? `${i.harness.cable || ""}${i.harness.length_mm ? `, ${i.harness.length_mm} mm` : ""}` : <span className="muted">socket</span>}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            ) : (
              <div className="muted small">No electrical.json in this version.</div>
            )}
          </div>
          <div className="card pad">
            <h3 style={{ fontSize: 15, marginBottom: 8 }}>Design checks</h3>
            <RuleSummary title="Design-rule check (DRC)" doc={drc.data} count={pcb.drc_errors} />
            <RuleSummary title="Electrical-rule check (ERC)" doc={erc.data} count={pcb.erc_errors} />
            <h3 style={{ fontSize: 15, margin: "16px 0 8px" }}>Downloads</h3>
            <div className="dl-list">
              {(
                [
                  ["pcb/backplane-gerbers.zip", "Gerbers and drill (zip)", FileArchive],
                  ["pcb/backplane.kicad_pro", "KiCad project", FileCode2],
                  ["pcb/backplane.kicad_sch", "KiCad schematic", FileCode2],
                  ["pcb/backplane.kicad_pcb", "KiCad board", FileCode2],
                  ["pcb/bom.csv", "BOM (csv)", FileSpreadsheet],
                  ["pcb/cpl.csv", "Pick and place (csv)", FileSpreadsheet],
                  ["pcb/schematic.pdf", "Schematic (pdf)", Download],
                  ["pcb/schematic.svg", "Schematic (svg)", Download],
                ] as [string, string, typeof Download][]
              )
                .filter(([p]) => has(p))
                .map(([p, lab, Icon]) => (
                  <a key={p} href={dl(p)}>
                    <Icon size={15} /> {lab}
                  </a>
                ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function StatusChip({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className={`pill ${ok ? "success" : "warning"}`}>
      {ok ? <CircleCheck size={13} /> : <CircleX size={13} />} {label}
    </span>
  );
}

function RuleSummary({ title, doc, count }: { title: string; doc: any; count?: number | null }) {
  const items: any[] = doc?.violations || doc?.errors || doc?.items || doc?.sheets?.flatMap((s: any) => s.violations || []) || [];
  const n = count ?? items.filter((v) => (v.severity || "error") === "error").length;
  return (
    <div className="rule">
      <div className="row">
        {n === 0 ? <CircleCheck size={15} className="ok" /> : <CircleX size={15} className="bad" />}
        <span className="grow">{title}</span>
        <span className="small muted">
          {n} errors{items.length && items.length !== n ? `, ${items.length - n} warnings` : ""}
        </span>
      </div>
      {items.length ? (
        <ul className="small muted" style={{ margin: "6px 0 0 22px", padding: 0 }}>
          {items.slice(0, 5).map((v, i) => (
            <li key={i}>{v.description || v.message || v.type || JSON.stringify(v).slice(0, 100)}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
