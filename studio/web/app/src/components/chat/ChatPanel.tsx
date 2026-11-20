import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, PanelLeftClose, PanelRightClose, PanelRightOpen } from "lucide-react";
import { useProject } from "../../store/project";
import { useUI } from "../../store/ui";
import type { ChatMessage, Project } from "../../lib/types";
import { MessageView, TurnActivity } from "./Messages";
import { BriefDock, ChoicesDock, ConceptsDock } from "./Docks";
import { Composer, type ComposerHandle } from "./Composer";
import { VoiceWishCard } from "../voice/VoiceWishCard";
import type { TabId } from "../panel/RightPanel";

/** The pending interactive message (questions, concepts or brief) that should be docked above the composer. */
export function activeInteraction(p: Project, running: boolean): { kind: "choices" | "concepts" | "brief"; msg: ChatMessage } | null {
  if (running || p.read_only) return null;
  const chat = p.chat;
  let lastUser = -1;
  chat.forEach((m, i) => {
    if (m.role === "user") lastUser = i;
  });
  for (let i = chat.length - 1; i > lastUser; i--) {
    const m = chat[i];
    if (m.kind === "choices" && !m.payload?.answered) return { kind: "choices", msg: m };
    if (m.kind === "concepts" && !m.payload?.selected && p.stage === "concepts") return { kind: "concepts", msg: m };
    if (m.kind === "brief" && p.stage === "brief" && p.brief && !p.brief.approved && m.payload?.brief_version === p.brief.version) return { kind: "brief", msg: m };
  }
  return null;
}

export function ChatPanel({ full, split, onTab: _onTab }: { full: boolean; split: boolean; onTab: (t: TabId) => void }) {
  const project = useProject((s) => s.project)!;
  const turn = useProject((s) => s.turn);
  const sendError = useProject((s) => s.sendError);
  const readOnly = useProject((s) => s.readOnly);
  const ctx = useProject((s) => s.ctx);
  const { setChatHidden, panelHidden, setPanelHidden } = useUI();
  const list = useRef<HTMLDivElement>(null);
  const col = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const [away, setAway] = useState(false);
  const composer = useRef<ComposerHandle>(null);
  const active = useMemo(() => activeInteraction(project, !!turn?.running), [project, turn?.running]);

  // stick to the bottom while new content streams in, unless the reader scrolled up
  useLayoutEffect(() => {
    const el = list.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  });
  useEffect(() => {
    stick.current = true;
    const el = list.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [project.id, active?.msg.id]);
  // content also grows after render (build cards expand, images load): keep following it while stuck to the bottom
  useEffect(() => {
    const el = list.current;
    const c = col.current;
    if (!el || !c || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      if (stick.current) el.scrollTop = el.scrollHeight;
    });
    ro.observe(c);
    return () => ro.disconnect();
  }, []);
  // a message the user just sent always brings the view down
  const lastUser = [...project.chat].reverse().find((m) => m.role === "user")?.id;
  useEffect(() => {
    stick.current = true;
    const el = list.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lastUser]);

  const empty = project.chat.length === 0;
  const pendingTurn = turn?.running && !turn.userMsgId;

  return (
    <section className={`chat${full ? " full" : ""}`} aria-label="Project conversation">
      {split ? <div className="sub-head">
        <div className="grow" />
        {split ? (
          full ? (
            <button className="icon-btn sm" aria-label="Show project" title="Show project" onClick={() => setPanelHidden(false)}>
              <PanelRightOpen size={18} />
            </button>
          ) : (
            <>
              <button className="icon-btn sm" aria-label="Hide project" title="Hide project" onClick={() => setPanelHidden(!panelHidden)}>
                <PanelRightClose size={18} />
              </button>
              <button className="icon-btn sm" aria-label="Hide chat" title="Hide chat" onClick={() => setChatHidden(true)}>
                <PanelLeftClose size={18} />
              </button>
            </>
          )
        ) : null}
      </div> : null}
      <div
        className="chat-list"
        ref={list}
        onScroll={(e) => {
          const el = e.currentTarget;
          const gap = el.scrollHeight - el.scrollTop - el.clientHeight;
          stick.current = gap < 80;
          setAway(gap > 400);
        }}
      >
        <div className="chat-col" ref={col}>
          <VoiceWishCard project={project} ctx={ctx} readOnly={readOnly} compact />
          {empty && !turn ? (
            <div className="chat-empty">
              <h2>What should we make?</h2>
              <p className="muted">Describe the gadget: who it is for, what it does, where it lives. I will ask at most three questions, show concepts, then write a brief for you to approve.</p>
            </div>
          ) : null}
          {project.chat.map((m) => (
            <MessageView key={m.id} m={m} active={active?.msg.id || null} />
          ))}
          {pendingTurn ? (
            <div className="msg-user-wrap">
              <TurnActivity t={turn!} />
            </div>
          ) : null}
          {sendError ? <div className="callout danger">{sendError}</div> : null}
        </div>
      </div>
      {away ? (
        <button
          className="jump-latest"
          onClick={() => {
            const el = list.current;
            stick.current = true;
            if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
          }}
        >
          <ArrowDown size={14} /> Jump to latest
        </button>
      ) : null}
      {!readOnly ? (
        <div className="chat-bottom">
          <div className="chat-col">
            {active?.kind === "choices" ? <ChoicesDock m={active.msg} /> : null}
            {active?.kind === "concepts" ? <ConceptsDock m={active.msg} onOther={() => composer.current?.focus("")} /> : null}
            {active?.kind === "brief" && project.brief ? (
              <BriefDock brief={project.brief} onChange={() => composer.current?.focus("Change something in the brief: ")} />
            ) : null}
            <Composer ref={composer} placeholder={empty ? "Describe what you want to make..." : "Add a detail, or ask anything..."} />
          </div>
        </div>
      ) : (
        <div className="chat-bottom ro muted small">This is a read-only shared view.</div>
      )}
    </section>
  );
}
