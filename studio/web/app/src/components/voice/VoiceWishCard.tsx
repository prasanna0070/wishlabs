// "Spoken wish": the voice chat a project started from (Gemini Live), shown in the project view and on read-only
// share links. Renders nothing for a typed wish. The share page mounts it through import.meta.glob by file name.
import { useId } from "react";
import { Mic } from "lucide-react";
import type { Project, Version } from "../../lib/types";
import type { Ctx } from "../../lib/urls";
import { clock, hasVoiceWish, liveModelName, voiceOf, voiceUrl } from "../../lib/voice";
import "./voice.css";

export { hasVoiceWish };

export interface VoiceWishCardProps {
  project: Project;
  version?: Version | null;
  ctx?: Ctx | null;
  readOnly?: boolean;
  compact?: boolean;
}

export function VoiceWishCard({ project, ctx, readOnly = false, compact = false }: VoiceWishCardProps) {
  const headId = useId(); // unique per mount: the card can be on the page more than once (chat and share Overview)
  const v = voiceOf(project);
  if (!v) return null;
  const share = ctx?.share || (readOnly ? (project.share?.token ?? null) : null);
  const audio = voiceUrl(v.audio_url, share);
  const turns = v.turns || [];
  const details = (v.details || []).filter((d) => d && d.question && d.answer);
  const asked = v.questions ?? details.length;
  const facts = [
    `with ${liveModelName(v.model)}`,
    v.seconds ? clock(v.seconds) : null,
    `${asked} ${asked === 1 ? "question" : "questions"}`,
  ].filter(Boolean);

  return (
    <section className={`vw-card${compact ? " compact" : ""}`} aria-labelledby={headId}>
      <header className="vw-card-head">
        <span className="vw-card-icon" aria-hidden="true">
          <Mic size={16} />
        </span>
        <div className="grow">
          <h3 id={headId}>Spoken wish</h3>
          <div className="muted small">This design started as a voice chat {facts.join(" · ")}.</div>
        </div>
      </header>

      {v.wish ? (
        <blockquote className="vw-card-wish">
          <span className="sr-only">The wish, as handed to the design studio: </span>
          {v.wish}
        </blockquote>
      ) : null}
      {details.length ? (
        <dl className="vw-card-details">
          {details.map((d, i) => (
            <div key={i}>
              <dt>{d.question}</dt>
              <dd>{d.answer}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      {audio ? (
        <div className="vw-card-audio">
          <audio controls preload="none" src={audio} aria-label="Recording of the spoken wish" />
          <span className="muted small">
            The speaker's own voice{v.audio_seconds ? `, ${clock(v.audio_seconds)}` : ""}, long pauses trimmed.
          </span>
        </div>
      ) : null}

      {turns.length ? (
        <details className="vw-card-transcript" open={!compact && turns.length <= 8}>
          <summary>Transcript ({turns.length} turns)</summary>
          <ol>
            {turns.map((t, i) => (
              <li key={i} className={`vw-line ${t.role}`}>
                <span className="vw-who">{t.role === "user" ? "Speaker" : "Wish Labs"}</span>
                <span className="vw-text">{t.text}</span>
                <span className="vw-t muted" aria-label={`at ${clock(t.t)}`}>
                  {clock(t.t)}
                </span>
              </li>
            ))}
          </ol>
          <p className="muted small vw-card-note">Captions written live by Gemini Live's speech transcription; they can contain small mistakes.</p>
        </details>
      ) : null}
    </section>
  );
}

export default VoiceWishCard;
