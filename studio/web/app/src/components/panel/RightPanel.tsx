import { lazy, Suspense, useState } from "react";
import { AlertTriangle, CircuitBoard, CodeXml, Hammer, LayoutDashboard, Package, PanelLeftOpen, RotateCw, Shapes, ShieldCheck, ShoppingCart, Wrench, type LucideIcon } from "lucide-react";
import { useProject, currentVersion, lastDoneVersion } from "../../store/project";
import { useUI } from "../../store/ui";
import type { Version } from "../../lib/types";
import { Dialog, Spinner } from "../ui";
import { BuildProgressCard } from "../chat/Messages";
import { ChecksCard } from "./Checks";
import { PreviewTab } from "./PreviewTab";
import { ErrorText } from "../ErrorText";
import { ChunkBoundary } from "../ChunkBoundary";
import { bestStageErrorText } from "../../lib/errors";

const PartsTab = lazy(() => import("./PartsTab"));
const OrderTab = lazy(() => import("./OrderTab"));
const BuildTab = lazy(() => import("./BuildTab"));
const PcbTab = lazy(() => import("./PcbTab"));
const CodeTab = lazy(() => import("./CodeTab"));
const OverviewTab = lazy(() => import("./OverviewTab"));

export const TABS = [
  { id: "preview", label: "Preview", icon: Shapes },
  { id: "parts", label: "Parts", icon: Package },
  { id: "order", label: "Order", icon: ShoppingCart },
  { id: "build", label: "Build", icon: Wrench },
  { id: "pcb", label: "PCB", icon: CircuitBoard },
  { id: "code", label: "Code", icon: CodeXml },
] as const;
/** The first tab of a read-only share link: the whole story on one page (OverviewTab.tsx). */
export const OVERVIEW_TAB = { id: "overview", label: "Overview", icon: LayoutDashboard } as const;
export type TabId = (typeof TABS)[number]["id"] | (typeof OVERVIEW_TAB)["id"];
export type TabDef = { id: TabId; label: string; icon: LucideIcon };

/** The tabs a viewer gets: share links open on Overview; the signed-in studio keeps its six tabs. */
export function tabsFor(shared: boolean): readonly TabDef[] {
  return shared ? [OVERVIEW_TAB, ...TABS] : TABS;
}
/** The tab a bare /p/<pid> or /s/<token> URL opens. */
export function homeTab(shared: boolean): TabId {
  return shared ? "overview" : "preview";
}

export interface TabProps {
  version: Version;
  onTab: (t: TabId) => void;
}

export function RightPanel({ tab, onTab }: { tab: TabId; onTab: (t: TabId) => void }) {
  const project = useProject((s) => s.project)!;
  const ctx = useProject((s) => s.ctx)!;
  const readOnly = useProject((s) => s.readOnly);
  const { chatHidden, setChatHidden } = useUI();
  const [checks, setChecks] = useState(false);
  const cur = currentVersion(project);
  const tabs = tabsFor(readOnly);

  let body: React.ReactNode;
  if (tab === "overview" && readOnly)
    body = (
      <ChunkBoundary key={tab}>
        <Suspense
          fallback={
            <div className="empty">
              <Spinner lg />
            </div>
          }
        >
          <OverviewTab onTab={onTab} />
        </Suspense>
      </ChunkBoundary>
    );
  else if (!cur) body = <div className="empty">No version selected.</div>;
  else if (tab === "preview") body = <PreviewTab version={cur} onTab={onTab} />;
  else if (cur.state !== "done") body = <NotReady version={cur} />;
  else {
    const props = { version: cur, onTab };
    body = (
      <ChunkBoundary key={tab}>
        <Suspense
          fallback={
            <div className="empty">
              <Spinner lg />
            </div>
          }
        >
          {tab === "parts" ? <PartsTab {...props} /> : tab === "order" ? <OrderTab {...props} /> : tab === "build" ? <BuildTab {...props} /> : tab === "pcb" ? <PcbTab {...props} /> : <CodeTab {...props} />}
        </Suspense>
      </ChunkBoundary>
    );
  }

  return (
    <section className="panel" aria-label="Project preview">
      <div className="sub-head tabs-bar">
        {chatHidden ? (
          <button className="icon-btn sm" aria-label="Show chat" title="Show chat" onClick={() => setChatHidden(false)}>
            <PanelLeftOpen size={18} />
          </button>
        ) : null}
        <nav className="tabs" role="tablist" aria-label="Project views">
          {tabs.map((t) => (
            <button key={t.id} role="tab" aria-selected={tab === t.id} className={`tab${tab === t.id ? " on" : ""}`} onClick={() => onTab(t.id)}>
              <t.icon size={16} strokeWidth={1.8} />
              {t.label}
            </button>
          ))}
        </nav>
        <div className="grow" />
        <button className="icon-btn sm" aria-label="See checks" title="See checks" onClick={() => setChecks(true)} disabled={cur?.state !== "done"}>
          <ShieldCheck size={18} />
        </button>
      </div>
      <div className="panel-body" key={`${ctx.pid}-${cur?.n}-${tab}`}>
        {body}
      </div>
      <Dialog open={checks} onClose={() => setChecks(false)} title="Checks" sub={cur ? `Version ${cur.n}` : ""} wide>
        {cur?.state === "done" ? <ChecksCard version={cur} expanded /> : null}
      </Dialog>
    </section>
  );
}

function NotReady({ version }: { version: Version }) {
  const project = useProject((s) => s.project)!;
  const setCurrent = useProject((s) => s.setCurrent);
  const send = useProject((s) => s.send);
  const readOnly = useProject((s) => s.readOnly);
  const last = lastDoneVersion(project);
  const progressMsg = project.chat.find((m) => m.kind === "progress" && m.payload?.version === version.n);
  const statuses = useProject((s) => s.statuses);
  if (version.state === "error")
    return (
      <div className="empty">
        <div className="icon-wrap">
          <AlertTriangle size={22} />
        </div>
        <h3>Version {version.n} stopped with an error</h3>
        <div style={{ maxWidth: 480, textAlign: "left" }}>
          <ErrorText text={String(version.error?.message || version.error || "The builder reported an error.")} full={bestStageErrorText(statuses[version.n], progressMsg?.payload?.status)} />
        </div>
        <div className="row">
          {!readOnly ? (
            <button className="btn primary" onClick={() => void send({ action: "retry_build" })}>
              <RotateCw size={15} /> Try the build again
            </button>
          ) : null}
          {last ? (
            <button className="btn" onClick={() => void setCurrent(last.n)}>
              View v{last.n}
            </button>
          ) : null}
        </div>
      </div>
    );
  return (
    <div className="not-ready">
      <div className="row" style={{ gap: 10 }}>
        <Hammer size={18} />
        <h3 style={{ fontSize: 16 }}>Version {version.n} is building</h3>
      </div>
      <p className="muted" style={{ margin: "6px 0 16px" }}>
        This tab fills in when the build finishes. The Preview tab shows a 3D draft as soon as the shell exists.
      </p>
      <BuildProgressCard m={progressMsg || { id: `progress-${version.n}`, role: "assistant", kind: "progress", text: "", ts: "", payload: { version: version.n } }} />

      {last ? (
        <button className="btn" style={{ marginTop: 16 }} onClick={() => void setCurrent(last.n)}>
          View the last finished version (v{last.n})
        </button>
      ) : null}
    </div>
  );
}
