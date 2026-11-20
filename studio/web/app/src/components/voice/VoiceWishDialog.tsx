// "Talk to make a wish": a live voice call with Gemini Live. The person says the wish, Live asks at most three short
// questions, confirms, and hands the wish to the design studio (submit_wish on the server makes the project). Live
// captions, a level meter, Done and Cancel, and a fallback to the text box when the microphone or the call fails.
import { useCallback, useEffect, useRef, useState } from "react";
import { Keyboard, Mic, PhoneOff, Send } from "lucide-react";
import { Dialog, Spinner } from "../ui";
import { VoiceCall, type FailKind, type VoiceEvent } from "../../lib/voiceCall";
import { clock, type VoiceTurn } from "../../lib/voice";
import "./voice.css";

export interface VoiceSubmitted {
  projectId: string;
  text: string; // the first chat message (the wish plus the voice answers), sent through the normal chat path
}

type Phase = "connecting" | "listening" | "speaking" | "ending" | "submitting" | "submitted" | "failed" | "ended";

const PHASE_TEXT: Record<Phase, string> = {
  connecting: "Connecting to Gemini Live…",
  listening: "Listening. Say what you would like to make.",
  speaking: "Wish Labs is talking. You can interrupt.",
  ending: "Wrapping up your wish…",
  submitting: "Handing your wish to the design studio…",
  submitted: "Your wish is with the design studio. Opening it…",
  failed: "The voice call stopped.",
  ended: "The call ended.",
};

export function VoiceWishDialog({
  open,
  mode,
  text,
  projectId,
  onSubmitted,
  onClose,
}: {
  open: boolean;
  mode: "pro" | "fast";
  text?: string;
  projectId?: string;
  /** The project exists and its first turn is ours to send; called once, after Live's closing line has played. */
  onSubmitted: (s: VoiceSubmitted) => void;
  /** Closed without a project. userText: what the person said, to drop into the text box. */
  onClose: (userText?: string) => void;
}) {
  const call = useRef<VoiceCall | null>(null);
  const [phase, setPhase] = useState<Phase>("connecting");
  const [turns, setTurns] = useState<VoiceTurn[]>([]);
  const [mic, setMic] = useState(0);
  const [speaker, setSpeaker] = useState(0);
  const [error, setError] = useState<{ kind: FailKind | string; message: string } | null>(null);
  const [left, setLeft] = useState<number | null>(null);
  const [keepAudio, setKeepAudio] = useState(false);
  const [recordingAllowed, setRecordingAllowed] = useState(true);
  const deadline = useRef<number | null>(null);
  const submitted = useRef<VoiceSubmitted | null>(null);
  const handedOver = useRef(false);
  const doneBtn = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLOListElement>(null);
  const turnsRef = useRef<VoiceTurn[]>([]);
  turnsRef.current = turns;

  const userText = () =>
    turnsRef.current
      .filter((t) => t && t.role === "user")
      .map((t) => t.text.trim())
      .join(" ")
      .trim();

  const handOver = useCallback(() => {
    const s = submitted.current;
    if (!s || handedOver.current) return;
    handedOver.current = true;
    const c = call.current;
    // let Live's closing line finish (at most 4 s), then open the project
    const wait = Math.min(4000, Math.round((c?.speechLeft() || 0) * 1000));
    window.setTimeout(() => {
      c?.close();
      onSubmitted(s);
    }, wait);
  }, [onSubmitted]);

  // one call per opening
  useEffect(() => {
    if (!open) return;
    setPhase("connecting");
    setTurns([]);
    setError(null);
    setLeft(null);
    submitted.current = null;
    handedOver.current = false;
    deadline.current = null;
    const c = new VoiceCall({
      onLevel: (m, s) => {
        setMic(m);
        setSpeaker(s);
      },
      onFail: (kind, message) => {
        if (submitted.current) return handOver();
        setError({ kind, message });
        setPhase("failed");
      },
      onEvent: (ev: VoiceEvent) => {
        switch (ev.type) {
          case "ready":
            setRecordingAllowed(ev.recording_allowed);
            deadline.current = Date.now() + ev.max_seconds * 1000;
            setPhase("listening");
            break;
          case "caption":
            setTurns((ts) => {
              const out = ts.slice();
              out[ev.i] = { role: ev.role, text: ev.text, t: ev.t }; // the server numbers turns densely from 0
              return out;
            });
            if (ev.role === "assistant") setPhase((p) => (p === "listening" ? "speaking" : p));
            break;
          case "turn_complete":
          case "interrupted":
            setPhase((p) => (p === "speaking" ? "listening" : p));
            break;
          case "ending":
            setPhase("ending");
            break;
          case "time_left":
            setLeft(ev.seconds);
            break;
          case "submitting":
            setPhase("submitting");
            break;
          case "submitted":
            submitted.current = { projectId: ev.project_id, text: ev.text };
            setPhase("submitted");
            break;
          case "error":
            if (ev.code === "create_failed" || !submitted.current) {
              setError({ kind: ev.code, message: ev.message });
              setPhase("failed");
            }
            break;
          case "ended":
            if (submitted.current) handOver();
            else if (ev.reason !== "cancelled") {
              setPhase((p) => (p === "failed" ? p : "ended"));
              setError((e) =>
                e || {
                  kind: ev.reason,
                  message:
                    ev.reason === "time_limit"
                      ? "That is the end of the three minutes for one voice wish. Type the rest of your wish, or start again."
                      : ev.reason === "no_wish"
                        ? "The call ended before the wish was clear. Type it instead, or start again."
                        : ev.reason === "replaced"
                          ? "This voice wish continued in another window. Type your wish here instead, or start again."
                          : "The call ended. Type your wish instead, or start again.",
                },
              );
            }
            break;
        }
      },
    });
    call.current = c;
    void c.start({ mode, keepAudio, text: text?.trim() || undefined, projectId });
    return () => {
      c.close();
      call.current = null;
    };
    // keepAudio is sent live through setKeepAudio; restarting the call for it would be wrong
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // remaining time, from the server's cap
  useEffect(() => {
    if (!open) return;
    const id = window.setInterval(() => {
      if (deadline.current) setLeft(Math.max(0, Math.round((deadline.current - Date.now()) / 1000)));
    }, 1000);
    return () => window.clearInterval(id);
  }, [open]);

  useEffect(() => {
    if (open && phase === "listening") doneBtn.current?.focus({ preventScroll: true });
  }, [open, phase === "listening"]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const el = list.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns]);

  const cancel = () => {
    if (submitted.current) return; // the project exists; let the hand-over finish
    call.current?.cancel();
    onClose(userText() || undefined);
  };
  const typeInstead = () => {
    call.current?.close();
    onClose(userText() || undefined);
  };
  const live = phase === "listening" || phase === "speaking";
  const shown = turns.filter(Boolean);
  const busy = phase === "connecting" || phase === "ending" || phase === "submitting" || phase === "submitted";

  return (
    <Dialog
      open={open}
      onClose={phase === "failed" || phase === "ended" ? typeInstead : cancel}
      title="Talk to make a wish"
      sub="A short voice chat with Gemini Live. It asks up to three questions, then hands your wish to the design studio."
      label="Talk to make a wish"
      footer={
        phase === "failed" || phase === "ended" ? (
          <>
            <div className="grow" />
            <button className="btn primary" onClick={typeInstead} autoFocus>
              <Keyboard size={16} /> Type instead
            </button>
          </>
        ) : (
          <>
            <button className="btn ghost" onClick={cancel} disabled={phase === "submitted" || phase === "submitting"}>
              <PhoneOff size={16} /> Cancel
            </button>
            <div className="grow" />
            <button
              ref={doneBtn}
              className="btn primary"
              onClick={() => call.current?.end()}
              disabled={!live}
              title="Gemini Live wraps up and sends your wish to the studio"
            >
              <Send size={16} /> Done, make it
            </button>
          </>
        )
      }
    >
      <div className="vw">
        <div className={`vw-orb ${phase}`} aria-hidden="true">
          <Mic size={26} />
        </div>
        <div className="vw-status" role="status">
          {busy ? <Spinner /> : null}
          <span>{error && (phase === "failed" || phase === "ended") ? error.message : PHASE_TEXT[phase]}</span>
          {left != null && live ? <span className="vw-left muted small">{clock(left)} left</span> : null}
        </div>
        <div
          className="vw-meter"
          role="meter"
          aria-label={phase === "speaking" ? "Wish Labs voice level" : "Microphone level"}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round((phase === "speaking" ? speaker : mic) * 100)}
        >
          {Array.from({ length: 16 }, (_, i) => {
            const v = phase === "speaking" ? speaker : mic;
            const on = live && v * 16 > i;
            return <span key={i} className={on ? (phase === "speaking" ? "on them" : "on") : ""} />;
          })}
        </div>
        <ol className="vw-captions" ref={list} role="log" aria-live="polite" aria-label="Live captions">
          {shown.length === 0 ? <li className="vw-hint muted small">Captions appear here as you talk.</li> : null}
          {shown.map((t, i) => (
            <li key={i} className={`vw-line ${t.role}`}>
              <span className="vw-who">{t.role === "user" ? "You" : "Wish Labs"}</span>
              <span className="vw-text">{t.text}</span>
            </li>
          ))}
        </ol>
        {recordingAllowed ? (
          <label className="vw-keep small">
            <input
              type="checkbox"
              checked={keepAudio}
              disabled={phase === "submitted" || phase === "failed" || phase === "ended"}
              onChange={(e) => {
                setKeepAudio(e.target.checked);
                call.current?.setKeepAudio(e.target.checked);
              }}
            />
            <span>
              Save my voice recording with the project. Anyone with its share link can play it. The written transcript is
              saved either way and shows on the share link.
            </span>
          </label>
        ) : null}
        {error && phase === "failed" && error.kind === "mic_denied" ? (
          <p className="muted small" style={{ margin: 0 }}>
            Nothing was recorded. You can still type the same wish.
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}
