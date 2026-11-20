import { useEffect, useRef } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useProject } from "../store/project";
import { useUI } from "../store/ui";
import { Header } from "../components/Header";
import { ChatPanel } from "../components/chat/ChatPanel";
import { homeTab, RightPanel, tabsFor, type TabId } from "../components/panel/RightPanel";
import { Spinner } from "../components/ui";
import { clamp } from "../lib/format";
import { behindLatest } from "../lib/versions";
import { History } from "lucide-react";

export function ProjectPage({ shared = false }: { shared?: boolean }) {
  const { pid, token, tab } = useParams();
  const nav = useNavigate();
  const project = useProject((s) => s.project);
  const loading = useProject((s) => s.loading);
  const error = useProject((s) => s.error);
  const load = useProject((s) => s.load);
  const loadShare = useProject((s) => s.loadShare);
  const send = useProject((s) => s.send);
  const stopFollow = useProject((s) => s.stopFollow);
  const { chatWidth, chatHidden, panelHidden, setChatWidth } = useUI();
  const sentPending = useRef<string | null>(null);

  useEffect(() => {
    if (shared && token) void loadShare(token);
    else if (pid) void load(pid);
  }, [shared, pid, token, load, loadShare]);
  useEffect(() => () => stopFollow(), [stopFollow]);

  // first prompt typed on the Home page
  useEffect(() => {
    if (!project || shared || sentPending.current === project.id) return;
    let pending: string | null = null;
    try {
      pending = sessionStorage.getItem(`gs.pending.${project.id}`);
      if (pending) sessionStorage.removeItem(`gs.pending.${project.id}`);
    } catch {
      /* ignore */
    }
    sentPending.current = project.id;
    if (pending && project.chat.length === 0) void send({ text: pending });
  }, [project, shared, send]);

  useEffect(() => {
    if (project) document.title = `${project.name || "Untitled gadget"} · Wish Labs`;
    return () => {
      document.title = "Wish Labs";
    };
  }, [project?.name]);

  const home = homeTab(shared);
  const active: TabId = (tabsFor(shared).some((t) => t.id === tab) ? tab : home) as TabId;
  const base = shared ? `/s/${token}` : `/p/${pid}`;
  const setTab = (t: TabId) => nav(t === home ? base : `${base}/${t}`, { replace: false });

  if (loading && !project)
    return (
      <div className="empty">
        <Spinner lg />
      </div>
    );
  if (error && !project)
    return (
      <div className="empty">
        <h3>{error}</h3>
        <Link className="btn" to="/">
          Back to projects
        </Link>
      </div>
    );
  if (!project) return null;

  const split = project.versions.length > 0;
  const showChat = !chatHidden || !split;
  const showPanel = split && !panelHidden;
  const overview = shared && showPanel && active === "overview"; // the share Overview shows the Spoken wish itself

  const startDrag = (e: React.PointerEvent) => {
    e.preventDefault();
    const x0 = e.clientX;
    const w0 = chatWidth;
    const move = (ev: PointerEvent) => setChatWidth(clamp(w0 + ev.clientX - x0, 360, Math.max(400, window.innerWidth - 560)));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      document.body.classList.remove("resizing");
    };
    document.body.classList.add("resizing");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  return (
    <div className="workspace">
      <Header onTab={setTab} />
      <BehindBanner />
      <div
        className={`ws-body${split ? " split" : ""}${!showChat ? " no-chat" : ""}${!showPanel ? " no-panel" : ""}${overview ? " ov-mode" : ""}`}
        style={{ ["--chat-w" as any]: `${clamp(chatWidth, 360, 900)}px` }}
      >
        {showChat ? <ChatPanel full={!showPanel} split={split} onTab={setTab} voiceCard={!overview} /> : null}
        {showChat && showPanel ? (
          <div
            className="resize-handle"
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize chat"
            tabIndex={0}
            onPointerDown={startDrag}
            onKeyDown={(e) => {
              if (e.key === "ArrowLeft") setChatWidth(clamp(chatWidth - 24, 360, 900));
              if (e.key === "ArrowRight") setChatWidth(clamp(chatWidth + 24, 360, 900));
            }}
          />
        ) : null}
        {showPanel ? <RightPanel tab={active} onTab={setTab} /> : null}
      </div>
    </div>
  );
}

/** MAJ-39: an edit starts from the current version, so say so when that is not the newest one. */
function BehindBanner() {
  const project = useProject((s) => s.project);
  const readOnly = useProject((s) => s.readOnly);
  const setCurrent = useProject((s) => s.setCurrent);
  const b = behindLatest(project);
  if (!b || readOnly) return null;
  const latest = project!.versions.find((v) => v.n === b.latest);
  return (
    <div className="behind-banner" role="status">
      <History size={15} />
      <span className="grow">
        You are on v{b.cur}. Edits start from v{b.cur}; the latest is v{b.latest}.
      </span>
      {latest && latest.state !== "error" ? (
        <button className="btn sm" onClick={() => void setCurrent(b.latest)}>
          Go to v{b.latest}
        </button>
      ) : null}
    </div>
  );
}
