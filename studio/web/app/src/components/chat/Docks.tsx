// Interactive cards docked above the composer: clarifying questions, concept tiers, brief review.
import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Check, Hammer, RefreshCw } from "lucide-react";
import { useProject } from "../../store/project";
import { rewriteShareUrl } from "../../lib/urls";
import { groupConcepts } from "../../lib/concepts";
import { buildEstimate, waitText } from "../../lib/estimate";
import type { Brief, ChatMessage, Concept, Question } from "../../lib/types";
import { BriefView } from "../BriefView";
import { Spinner } from "../ui";

export function ChoicesDock({ m, onOther: _onOther }: { m: ChatMessage; onOther?: () => void }) {
  const send = useProject((s) => s.send);
  const qs: Question[] = m.payload?.questions || [];
  const defaults: string[] = m.payload?.defaults || [];
  const [ans, setAns] = useState<Record<string, string>>({});
  const [free, setFree] = useState<Record<string, string>>({});
  const single = qs.length === 1;
  const value = (q: Question) => (free[q.id]?.trim() ? free[q.id].trim() : ans[q.id]);
  const answered = qs.filter((q) => !!value(q)).length;
  const submit = (override?: Record<string, string>) => {
    const answers = qs.map((q) => ({ question_id: q.id, value: override?.[q.id] ?? value(q) })).filter((a) => !!a.value);
    if (!answers.length) return;
    void send(answers.length < qs.length ? { answers, skipped: true } : { answers });
  };
  return (
    <div className="dock card" role="group" aria-label="Questions">
      <div className="dock-scroll">
        {qs.map((q, qi) => (
          <div key={q.id} className="q">
            <div className="q-text">{q.question}</div>
            {q.consequence ? <div className="q-cons">{q.consequence}</div> : null}
            {q.options?.length ? (
              <div className="q-opts">
                {q.options.map((o, i) => (
                  <button
                    key={o}
                    className={`q-opt${ans[q.id] === o && !free[q.id]?.trim() ? " on" : ""}`}
                    onClick={() => {
                      setAns((a) => ({ ...a, [q.id]: o }));
                      setFree((f) => ({ ...f, [q.id]: "" }));
                      if (single) submit({ [q.id]: o });
                    }}
                  >
                    <span className="q-num">{i + 1}</span>
                    <span className="grow">{o}</span>
                    {ans[q.id] === o && !free[q.id]?.trim() ? <Check size={15} /> : null}
                  </button>
                ))}
              </div>
            ) : null}
            <input
              className="input q-free"
              placeholder="Type your answer..."
              value={free[q.id] || ""}
              onChange={(e) => setFree((f) => ({ ...f, [q.id]: e.target.value }))}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (single ? !!free[q.id]?.trim() : answered === qs.length)) {
                  e.preventDefault();
                  submit();
                }
              }}
              aria-label={`Your answer to question ${qi + 1}`}
            />
          </div>
        ))}
        {defaults.length ? (
          <div className="q-defaults">
            <span className="muted small">Unless you say otherwise:</span>
            {defaults.map((d) => (
              <span key={d} className="pill">
                {d}
              </span>
            ))}
          </div>
        ) : null}
      </div>
      <div className="dock-foot">
        <button className="btn sm" onClick={() => void send({ answers: [], skipped: true })} title="Skip the questions and use the suggested defaults">
          Use your suggestions
        </button>
        <div className="grow" />
        {!single || !qs[0]?.options?.length ? (
          <>
            <span className="muted small">
              {answered} of {qs.length} answered
            </span>
            <button className="btn primary" disabled={!answered} onClick={() => submit()}>
              Send <ArrowRight size={15} />
            </button>
          </>
        ) : (
          <span className="muted small">Pick one, or type your own answer</span>
        )}
      </div>
    </div>
  );
}

export function ConceptsDock({ m, onOther }: { m: ChatMessage; onOther?: () => void }) {
  const project = useProject((s) => s.project)!;
  const ctx = useProject((s) => s.ctx);
  const send = useProject((s) => s.send);
  const turn = useProject((s) => s.turn);
  const ids: string[] = m.payload?.concept_ids || project.concepts.map((c) => c.id);
  const list = ids.map((id) => project.concepts.find((c) => c.id === id)).filter(Boolean) as Concept[];
  const groups = groupConcepts(list, m.payload?.tiers);
  const tiered = groups.length > 1 || !!groups[0]?.label;
  let k = 0;
  const [picked, setPicked] = useState<string | null>(null);
  const notes = [...new Set(list.map((c) => c.photo_note).filter(Boolean))];
  const sharedNote = list.length > 1 && notes.length === 1 && list.every((c) => c.photo_note === notes[0]) ? notes[0] : null;
  return (
    <div className="dock card concepts-dock" role="group" aria-label="Concepts">
      <div className="dock-head">
        <strong>Which concept should guide the design?</strong>
        <span className="muted small">
          Each photo is made from the buildable CAD render shown in its corner. Pick one, or describe changes below.
          {sharedNote ? ` ${sharedNote}` : ""}
        </span>
      </div>
      <div className="dock-scroll">
        <div className={tiered ? "tier-cols" : ""} style={tiered ? ({ ["--n" as any]: groups.length } as any) : undefined}>
        {groups.map((g) => (
          <div key={g.id} className="tier">
            {g.label ? <div className="tier-label">{g.label}</div> : null}
            <div className="tiles">
              {g.items.map((c) => {
                const num = ++k;
                const photo = rewriteShareUrl(c.photo_url, ctx?.share);
                const render = rewriteShareUrl(c.render_url, ctx?.share);
                const main = photo || render;
                const failed = c.state === "failed";
                return (
                  <button
                    key={c.id}
                    className={`tile${picked === c.id ? " on" : ""}`}
                    disabled={failed || !!turn?.running}
                    title={`${c.title}: ${c.blurb}`}
                    onClick={() => {
                      setPicked(c.id);
                      void send({ action: "select_concept", concept_id: c.id });
                    }}
                  >
                    <div className="tile-img">
                      {main ? <img src={main} alt={`${c.title}: ${c.blurb}`} loading="lazy" /> : <div className="ph">{failed ? "Render failed" : <Spinner />}</div>}
                      {photo && render ? (
                        <span className="tile-cad" title="CAD render of the same design">
                          <img src={render} alt="" />
                          <span>CAD</span>
                        </span>
                      ) : null}
                      <span className="tile-num">{num}</span>
                      {picked === c.id ? (
                        <span className="tile-busy">
                          <Spinner />
                        </span>
                      ) : null}
                    </div>
                    <div className="tile-title">{c.title}</div>
                    <div className="tile-blurb">{c.blurb}</div>
                    {c.photo_note && !sharedNote ? <div className="tile-note" title={c.photo_note}>{c.photo_note}</div> : null}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
        </div>
      </div>
      <div className="dock-foot">
        <button className="btn sm" disabled={!!turn?.running} onClick={() => void send({ action: "skip_concepts" })} title="Start from the first Expected concept">
          Continue without an image
        </button>
        <button className="btn sm" disabled={!!turn?.running} onClick={() => onOther?.()}>
          Other...
        </button>
        <button className="btn sm ghost" disabled={!!turn?.running} onClick={() => void send({ action: "regenerate_concepts" })}>
          <RefreshCw size={14} /> New concepts
        </button>
        <div className="grow" />
        <span className="muted small">One click chooses.</span>
      </div>
    </div>
  );
}

export function BriefDock({ brief, onChange }: { brief: Brief; onChange: () => void }) {
  const send = useProject((s) => s.send);
  const turn = useProject((s) => s.turn);
  const versions = useProject((s) => s.project?.versions);
  const pid = useProject((s) => s.project?.id);
  const wait = useMemo(() => buildEstimate(versions, pid), [versions, pid]);
  const body = useRef<HTMLDivElement>(null);
  const [read, setRead] = useState(false);
  const check = () => {
    const el = body.current;
    if (el && el.scrollTop + el.clientHeight >= el.scrollHeight - 6) setRead(true);
  };
  useEffect(() => {
    setRead(false);
    window.setTimeout(check, 50);
  }, [brief.version]);
  return (
    <div className="dock card brief-dock" role="group" aria-label="Review your project brief">
      <div className="brief-dock-head">Review your project brief</div>
      <div className="dock-scroll brief-scroll" ref={body} onScroll={check} tabIndex={0}>
        <BriefView brief={brief} compact />
      </div>
      <div className="brief-dock-note">
        A build makes the printed shell and print files, a routed backplane PCB, compiled firmware, the parts list, an illustrated build manual and an India delivered price. It usually takes {waitText(wait)}
        {wait ? ` (measured on ${wait.n === 1 ? "the last build" : `the last ${wait.n} builds`})` : ""} and keeps working if you leave.
      </div>
      <div className="dock-foot">
        <button className="btn" onClick={onChange}>
          Change something
        </button>
        <div className="grow" />
        {!read ? (
          <span className="muted small" id="read-hint">
            Read the entire brief before approving
          </span>
        ) : null}
        <button
          className="btn primary lg"
          aria-disabled={!read || !!turn?.running}
          aria-describedby={!read ? "read-hint" : undefined}
          onClick={() => {
            if (!read) {
              body.current?.scrollBy({ top: 200, behavior: "smooth" });
              return;
            }
            if (!turn?.running) void send({ action: "approve_brief" });
          }}
        >
          <Hammer size={16} /> Build this
        </button>
      </div>
    </div>
  );
}
