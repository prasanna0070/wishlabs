// "Every gadget gets its own sound": the theme Lyria (on Vertex AI) composed from this design and the signature chime
// cut from it. Owner view: compose / compose again, with the server's limits. Share links (readOnly, ctx.share):
// play only, and nothing at all until a composition exists. The share page mounts this file by name (SoundCard.tsx).
import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import { BellRing, Download, Music2, Pause, Play, RefreshCw } from "lucide-react";
import { audioUrl, clock, hasSound, kb, playable, soundApi, soundOf, soundStatus, type SoundApiState, type SoundRecord, type SoundState } from "../../lib/sound";
import type { Project, Version } from "../../lib/types";
import type { Ctx } from "../../lib/urls";
import { useProject } from "../../store/project";
import { Spinner } from "../ui";
import "./sound.css";

export { hasSound };

export interface SoundCardProps {
  project: Project;
  version: Version | null;
  ctx: Ctx;
  readOnly: boolean;
  /** One-line strip (the Preview tab, under the 3D view); the full card otherwise. */
  compact?: boolean;
}

type Track = "chime" | "theme";

function usePlayer(cur: SoundRecord | null, ctx: Ctx) {
  const el = useRef<HTMLAudioElement | null>(null);
  const loaded = useRef<string | null>(null);
  const [playing, setPlaying] = useState<Track | null>(null);
  const [pos, setPos] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const which = useRef<Track | null>(null);

  useEffect(
    () => () => {
      el.current?.pause();
      el.current?.removeAttribute("src");
      el.current = null;
    },
    [],
  );
  useEffect(() => {
    el.current?.pause();
    loaded.current = null;
    setPlaying(null);
    setPos(0);
  }, [cur?.id]);

  const audio = () => {
    if (el.current) return el.current;
    const a = new Audio();
    a.preload = "none";
    a.addEventListener("timeupdate", () => {
      if (which.current === "theme") setPos(a.currentTime);
    });
    a.addEventListener("ended", () => {
      setPlaying(null);
      if (which.current === "theme") setPos(0);
    });
    a.addEventListener("pause", () => setPlaying(null));
    a.addEventListener("play", () => setPlaying(which.current));
    el.current = a;
    return a;
  };

  const play = (t: Track, at?: number) => {
    if (!cur) return;
    const a = audio();
    if (which.current === t && !a.paused && at == null) {
      a.pause();
      return;
    }
    const url = audioUrl(t === "chime" ? cur.chime.url : cur.theme.url, ctx);
    if (loaded.current !== url) {
      a.pause();
      a.src = url;
      loaded.current = url;
    }
    which.current = t;
    try {
      a.currentTime = at != null ? at : t === "theme" && pos > 0 && pos < cur.theme.seconds - 0.5 ? pos : 0;
    } catch {
      /* not seekable before metadata: starts at 0 */
    }
    setError(null);
    if (t === "chime") setPos(0);
    a.play().catch((e: any) => {
      if (e?.name !== "AbortError") setError("This browser could not play the sound.");
    });
  };
  return { playing, pos, error, play };
}

export function SoundCard({ project, version, ctx, readOnly, compact = false }: SoundCardProps) {
  const owner = !readOnly && !ctx.share;
  const local = soundOf(project);
  const [live, setLive] = useState<SoundApiState | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [open, setOpen] = useState(!compact);
  const [now, setNow] = useState(() => Date.now());
  const [poll, setPoll] = useState(0);
  const sound: SoundState | null = owner && live ? live.sound : local;
  const status = soundStatus(sound, now);
  const cur = playable(sound);
  const { playing, pos, error, play } = usePlayer(cur, ctx);
  const jobId = local?.status === "generating" ? local.job?.id : null;

  // owner: the server's state (switch, limit), polled while a composition runs; the project is refreshed after
  useEffect(() => {
    if (!owner) return;
    let stop = false;
    let timer: number | undefined;
    let was = false;
    const tick = async () => {
      try {
        const s = await soundApi.state(ctx.pid);
        if (stop) return;
        setLive(s);
        if (s.status === "generating") {
          was = true;
          timer = window.setTimeout(tick, 3000);
        } else if (was) {
          void useProject.getState().refresh().catch(() => undefined);
        }
      } catch {
        if (!stop) timer = window.setTimeout(tick, 8000);
      }
    };
    void tick();
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [owner, ctx.pid, poll, jobId]);

  useEffect(() => {
    if (status !== "generating") return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [status]);

  if (!cur) {
    if (!owner) return null; // share links show a sound only once one exists
    if (status === "none" && !(live?.enabled && version?.state === "done")) return null; // nothing to offer yet
    if (live && !live.enabled) return null; // switched off on this server
  }

  const compose = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const s = await soundApi.compose(ctx.pid, version?.state === "done" ? version.n : null);
      setLive(s);
      setNow(Date.now());
      setPoll((k) => k + 1);
    } catch (e: any) {
      setMsg(e?.message || "The sound could not be started.");
    } finally {
      setBusy(false);
    }
  };

  const started = Date.parse(sound?.job?.started || "");
  const elapsed = Number.isFinite(started) ? Math.max(0, Math.round((now - started) / 1000)) : 0;
  const left = live?.session_left;
  const canCompose = owner && !!live?.enabled && status !== "generating" && !busy && left !== 0;
  const cost = live?.cost_usd_est != null ? `about $${live.cost_usd_est.toFixed(2)} per composition (estimate)` : "";
  const composeTitle = left === 0 ? "This browser's limit for this hour is used up." : [`One Lyria composition, ${cost}`, left != null ? `${left} left this hour` : ""].filter(Boolean).join(" · ");
  const theme = cur?.theme;
  const frac = theme && theme.seconds ? Math.min(1, pos / theme.seconds) : 0;

  const seek = (e: MouseEvent<HTMLDivElement>) => {
    if (!theme) return;
    const r = e.currentTarget.getBoundingClientRect();
    play("theme", Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * theme.seconds);
  };
  const seekKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!theme) return;
    const step = e.key === "ArrowRight" ? 5 : e.key === "ArrowLeft" ? -5 : 0;
    if (step) {
      e.preventDefault();
      play("theme", Math.max(0, Math.min(theme.seconds - 0.2, pos + step)));
    } else if (e.key === " " || e.key === "Enter") {
      e.preventDefault();
      play("theme");
    }
  };

  const players = cur ? (
    <>
      <button className={`btn sm${playing === "chime" ? " active" : ""}`} onClick={() => play("chime")} aria-pressed={playing === "chime"} title="The signature chime, cut from the theme's strongest moment">
        {playing === "chime" ? <Pause size={15} /> : <BellRing size={15} />} Chime <span className="muted">{cur.chime.seconds.toFixed(1)} s</span>
      </button>
      <button className={`btn sm${playing === "theme" ? " active" : ""}`} onClick={() => play("theme")} aria-pressed={playing === "theme"}>
        {playing === "theme" ? <Pause size={15} /> : <Play size={15} />} Theme <span className="muted">{playing === "theme" || pos > 0 ? `${clock(pos)} / ` : ""}{clock(cur.theme.seconds)}</span>
      </button>
      {theme?.peaks?.length ? (
        <div className="sound-wave" role="slider" tabIndex={0} aria-label="Theme position" aria-valuemin={0} aria-valuemax={Math.round(theme.seconds)} aria-valuenow={Math.round(pos)} aria-valuetext={`${clock(pos)} of ${clock(theme.seconds)}`} onClick={seek} onKeyDown={seekKey}>
          {theme.peaks.map((v, i) => (
            <i key={i} className={(i + 0.5) / theme.peaks!.length <= frac ? "on" : ""} style={{ height: `${Math.max(10, Math.round(v * 100))}%` }} />
          ))}
        </div>
      ) : null}
    </>
  ) : null;

  const composeBtn = owner && live?.enabled ? (
    <button className={`btn sm${cur ? "" : " primary"}`} onClick={() => void compose()} disabled={!canCompose} title={composeTitle}>
      {busy ? <Spinner /> : cur ? <RefreshCw size={15} /> : <Music2 size={15} />} {cur ? "Compose again" : "Compose its sound"}
    </button>
  ) : null;

  const working =
    status === "generating" ? (
      <span className="pill primary sound-working" role="status">
        <Spinner /> Lyria is composing{elapsed ? ` · ${elapsed} s` : ""} <span className="muted">(about 30 s)</span>
      </span>
    ) : null;

  const failed = owner && status === "error" ? <div className="callout danger small sound-msg">{sound?.error || "The last composition stopped before it finished."} {composeBtn ? "Try again." : ""}</div> : null;

  const details = cur ? (
    <div className="sound-details">
      <div className="muted small">Prompt sent to Lyria{cur.prompt_source === "safe_template" ? " (a general prompt: Lyria declined the first one)" : ""}</div>
      <p className="sound-prompt">{cur.prompt}</p>
      <div className="sound-meta">
        <span className="mono">{cur.model}</span>
        {cur.source?.seconds ? ` · ${cur.source.seconds.toFixed(1)} s composed at ${Math.round((cur.source.sample_rate || 48000) / 1000)} kHz` : ""}
        {cur.chime.onset_s != null ? ` · chime from ${cur.chime.onset_s.toFixed(1)} s` : ""}
        {` · for version ${cur.version}`}
        {cur.trigger === "auto" ? " · composed after its first build" : ""}
      </div>
      {cur.device_chime?.url ? (
        <a className="linkish small sound-device" href={`${audioUrl(cur.device_chime.url, ctx)}?download=1`} download>
          <Download size={13} /> Device-ready chime: {cur.device_chime.sample_rate ? `${cur.device_chime.sample_rate / 1000} kHz ` : ""}mono WAV, {kb(cur.device_chime.bytes)}
        </a>
      ) : null}
    </div>
  ) : null;

  if (compact)
    return (
      <section className="sound-strip" aria-label="Gadget sound" data-testid="sound-card">
        <div className="sound-row">
          <strong className="sound-title">
            <Music2 size={16} /> Sound
          </strong>
          {players}
          {working}
          {!cur && !working && owner && live?.enabled ? <span className="muted small">Lyria composes a theme from this design; its strongest moment becomes the chime.</span> : null}
          <span className="grow" />
          {cur ? (
            <button className="linkish small" onClick={() => setOpen(!open)} aria-expanded={open}>
              {open ? "Hide prompt" : "Prompt"}
            </button>
          ) : null}
          <span className="sound-credit">Composed by Lyria on Vertex AI</span>
          {composeBtn}
        </div>
        {open ? details : null}
        {failed}
        {msg || error ? <div className="callout danger small sound-msg">{msg || error}</div> : null}
      </section>
    );

  return (
    <section className="sound-card card pad" aria-label="Gadget sound" data-testid="sound-card">
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <Music2 size={17} />
        <strong className="grow">Its own sound</strong>
        <span className="sound-credit">Composed by Lyria on Vertex AI</span>
      </div>
      <p className="muted small" style={{ margin: "6px 0 10px" }}>
        {cur?.prompt_source === "safe_template" ? "A theme composed for this design." : "A theme written for this design from its name, purpose and colours."} Its strongest moment became the chime.
      </p>
      {cur ? <div className="sound-row">{players}</div> : null}
      {working ? <div className="sound-row">{working}</div> : null}
      {details}
      {composeBtn ? <div className="sound-row">{composeBtn}</div> : null}
      {failed}
      {msg || error ? <div className="callout danger small sound-msg">{msg || error}</div> : null}
    </section>
  );
}

export default SoundCard;
