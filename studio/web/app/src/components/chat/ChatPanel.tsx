import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowDown, PanelLeftClose, PanelRightClose, PanelRightOpen } from "lucide-react";
import { useProject } from "../../store/project";
import { useUI } from "../../store/ui";
import type { ChatMessage, Project } from "../../lib/types";
import { MessageView, TurnActivity } from "./Messages";
import { BriefDock, ChoicesDock, ConceptsDock } from "./Docks";
import { Composer, type ComposerHandle } from "./Composer";
import { VoiceWishCard } from "../voice/VoiceWishCard";
import { ImageBriefCard } from "../cards/ImageBriefCard";
import { hasImageBrief, imageBriefAnchor } from "../../lib/imageBrief";
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

/** voiceCard=false while a share link's Overview tab is beside the chat: the Overview already shows the Spoken wish
 * (and briefCard=false: it shows "What I read from your image" too). */
export function ChatPanel({
  full,
  split,
  onTab: _onTab,
  voiceCard = true,
  briefCard = true,
}: {
  full: boolean;
  split: boolean;
  onTab: (t: TabId) => void;
  voiceCard?: boolean;
  briefCard?: boolean;
}) {
  const project = useProject((s) => s.project)!;
  const turn = useProject((s) => s.turn);
  const sendError = useProject((s) => s.sendError);
  const sendErrorCode = useProject((s) => s.sendErrorCode);
  const watchTurn = useProject((s) => s.watchTurn);
  const readOnly = useProject((s) => s.readOnly);
  const ctx = useProject((s) => s.ctx);
  const { setChatHidden, panelHidden, setPanelHidden } = useUI();
  const list = useRef<HTMLDivElement>(null);
  const col = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const [away, setAway] = useState(false);
  const composer = useRef<ComposerHandle>(null);
  const active = useMemo(() => activeInteraction(project, !!turn?.running), [project, turn?.running]);
  // "What I read from your image" sits under the message with that image (or at the top when none is in the chat)
  const brief = briefCard && hasImageBrief(project);
  const briefAt = useMemo(() => (brief ? imageBriefAnchor(project) : null), [brief, project.chat, project.image_brief]);

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
  // a turn with no message of its own to sit under (just sent, followed from the server, or started in another tab
  // whose message is not loaded yet): show it at the end
  const pendingTurn = (turn?.running || turn?.stale) && !(turn.userMsgId && project.chat.some((m) => m.id === turn.userMsgId));

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
          {voiceCard ? <VoiceWishCard project={project} ctx={ctx} readOnly={readOnly} compact /> : null}
          {brief && !briefAt ? <ImageBriefCard project={project} ctx={ctx} readOnly={readOnly} compact /> : null}
          {empty && !turn ? (
            <div className="chat-empty">
              <h2>What should we make?</h2>
              <p className="muted">Describe the gadget: who it is for, what it does, where it lives. I will ask at most three questions, show concepts, then write a brief for you to approve.</p>
            </div>
          ) : null}
          {project.chat.map((m) => (
            <Fragment key={m.id}>
              <MessageView m={m} active={active?.msg.id || null} />
              {brief && m.id === briefAt ? <ImageBriefCard project={project} ctx={ctx} readOnly={readOnly} compact /> : null}
            </Fragment>
          ))}
          {pendingTurn ? (
            <div className="msg-user-wrap">
              <TurnActivity t={turn!} />
            </div>
          ) : null}
          {sendError ? (
            sendErrorCode === "conflict" ? (
              <div className="callout warning send-error-row" role="status">
                <span className="grow">{sendError}</span>
                <button
                  type="button"
                  className="btn sm"
                  onClick={() => {
                    watchTurn();
                    window.setTimeout(() => list.current?.querySelector(".working-card")?.scrollIntoView({ block: "center", behavior: "smooth" }), 250);
                  }}
                >
                  Show progress
                </button>
              </div>
            ) : sendErrorCode === "refused" ? (
              // a 409 that is not "still answering": the server's reason, as a note (nothing broke)
              <div className="callout warning" role="status">
                {sendError}
              </div>
            ) : (
              <div className="callout danger">{sendError}</div>
            )
          ) : null}
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
      ) : project.showcase ? (
        <ShowcaseBanner />
      ) : (
        <div className="chat-bottom ro muted small">This is a read-only shared view.</div>
      )}
    </section>
  );
}

/** A magic-link guest inside a showcase: no composer, a way to make its own wish instead (server app/guest.py). */
export function ShowcaseBanner() {
  return (
    <div className="chat-bottom ro showcase-banner small" role="status">
      <span>Showcase: view only.</span>{" "}
      <Link to="/" className="showcase-cta">
        Make your own wish
      </Link>
    </div>
  );
}
