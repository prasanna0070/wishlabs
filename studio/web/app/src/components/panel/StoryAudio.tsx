// The story card's audio (BLK-4) and the names it can speak (MAJ-33). Reads firmware/sdcard/stories.json of the
// version. When the stories have no audio, "Generate story audio" calls
// POST /api/projects/{pid}/versions/{n}/story-audio, which reruns only the firmware stage with stories_audio=true and
// answers with the new version.
import { useState } from "react";
import { AudioLines, CircleCheck, TriangleAlert } from "lucide-react";
import { useProject } from "../../store/project";
import { api, ApiError } from "../../lib/api";
import { useFileIndex, useVersionFile } from "../../lib/hooks";
import type { Version } from "../../lib/types";
import { Spinner } from "../ui";

export const STORIES = "firmware/sdcard/stories.json";

export interface StoryPack {
  audio?: boolean;
  voice?: string | null;
  language?: string;
  default_name?: { name?: string; file?: string; name_audio?: boolean } | null;
  names?: ({ name?: string; file?: string; name_audio?: boolean } | string)[];
  stories?: { id: string; title?: string; files?: string[] }[];
}

export interface NameState {
  name: string;
  spoken: boolean;
  isDefault?: boolean;
}

/** Each name the pack knows, and whether its clip is in this version (name_audio, or the file is in the version). */
export function nameStates(pack: StoryPack | null | undefined, has: (path: string) => boolean | null): NameState[] {
  if (!pack) return [];
  const out: NameState[] = [];
  const one = (x: any, isDefault = false) => {
    const name = typeof x === "string" ? x : x?.name;
    if (!name) return;
    const file = typeof x === "string" ? null : x?.file;
    const flag = typeof x === "string" ? undefined : x?.name_audio;
    const spoken = flag === true || (flag !== false && !!pack.audio && !!file && has(`firmware/sdcard/${file}`) === true);
    out.push({ name, spoken, isDefault });
  };
  if (pack.default_name) one(pack.default_name, true);
  for (const n of pack.names || []) one(n);
  return out;
}

/** Story files listed by the pack that the version lacks (a story with audio:true but missing clips still fails). */
export function missingClips(pack: StoryPack | null | undefined, has: (path: string) => boolean | null): string[] {
  if (!pack?.audio) return [];
  const all = (pack.stories || []).flatMap((s) => s.files || []);
  return all.filter((f) => has(`firmware/sdcard/${f}`) === false);
}

export function StoryAudio({ version, compact = false }: { version: Version; compact?: boolean }) {
  const ctx = useProject((s) => s.ctx)!;
  const readOnly = useProject((s) => s.readOnly);
  const afterTweak = useProject((s) => s.afterTweak);
  const building = useProject((s) => !!s.project?.versions.some((v) => v.state === "queued" || v.state === "running"));
  const pack = useVersionFile<StoryPack>(ctx, version.n, STORIES);
  const idx = useFileIndex(ctx, version.n);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "danger" | "success"; text: string } | null>(null);
  if (pack.loading || !pack.data) return null; // not a story player, or no pack in this version
  const p = pack.data;
  const has = (path: string) => (idx instanceof Set ? idx.has(path) : null);
  const stories = p.stories || [];
  const missing = missingClips(p, has);
  const ok = !!p.audio && !missing.length;
  const names = nameStates(p, has);

  const generate = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.storyAudio(ctx.pid, version.n);
      const v = r?.version || (r?.n != null ? r : null);
      if (r?.already) setMsg({ tone: "success", text: r.message || `Version ${version.n} already has story audio.` });
      else if (v) {
        afterTweak(v);
        setMsg({ tone: "success", text: `Generating story audio as v${v.n}${r?.plan?.mode === "partial" ? ": only the firmware stage runs again, then the checks and export" : ""}.` });
      } else setMsg({ tone: "success", text: "Story audio generation started." });
    } catch (e: any) {
      const missingRoute = e instanceof ApiError && (e.status === 404 || e.status === 405) && e.code !== "conflict";
      setMsg({ tone: "danger", text: missingRoute ? "This server cannot generate story audio yet." : e?.message || "Story audio could not be started." });
    } finally {
      setBusy(false);
    }
  };

  const button =
    !ok && !readOnly ? (
      <button className={`btn ${compact ? "sm" : ""} primary`} disabled={busy || building} onClick={() => void generate()} title={building ? "Wait for the running build to finish" : "Synthesise every story and name clip, then rebuild the firmware stage"}>
        {busy ? <Spinner /> : <AudioLines size={15} />} Generate story audio
      </button>
    ) : null;

  if (compact)
    return (
      <span className="row story-audio-compact" style={{ gap: 8 }}>
        {ok ? (
          <span className="pill success">
            <CircleCheck size={13} /> Story audio · {stories.length} stories
          </span>
        ) : (
          <span className="pill warning" title="The story card plays an error chime until its stories have audio">
            <TriangleAlert size={13} /> Stories have no audio yet
          </span>
        )}
        {button}
        {msg ? <span className={`small ${msg.tone === "danger" ? "bad" : "ok"}`}>{msg.text}</span> : null}
      </span>
    );

  return (
    <div className="story-audio card pad" data-testid="story-audio">
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <AudioLines size={16} />
        <strong className="grow">Story card (microSD)</strong>
        {ok ? (
          <span className="pill success">
            <CircleCheck size={13} /> Audio for {stories.length} stories{p.voice ? ` · voice ${p.voice}` : ""}
          </span>
        ) : (
          <span className="pill warning">
            <TriangleAlert size={13} /> No audio yet
          </span>
        )}
      </div>
      {!ok ? (
        <p className="small" style={{ margin: "8px 0" }}>
          {p.audio ? `${missing.length} story clip${missing.length === 1 ? " is" : "s are"} missing from this version.` : `The ${stories.length} stories on this card have text but no audio.`} Until audio is generated, a tap on the story button plays an error chime instead of a story.
        </p>
      ) : (
        <p className="muted small" style={{ margin: "8px 0" }}>
          Copy the files in firmware/sdcard/ to the root of the microSD card before you power the robot.
        </p>
      )}
      {button ? <div style={{ margin: "6px 0" }}>{button}</div> : null}
      {msg ? <div className={`callout ${msg.tone} small`} style={{ marginTop: 8 }}>{msg.text}</div> : null}
      {names.length ? (
        <div style={{ marginTop: 10 }}>
          <div className="muted small" style={{ marginBottom: 4 }}>
            Names the robot can say
          </div>
          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            {names.map((n) => (
              <span key={n.name} className={`pill ${n.spoken ? "" : "warning"}`} data-spoken={n.spoken ? "1" : "0"}>
                {n.name}
                {n.isDefault ? <span className="muted"> (default)</span> : null}
                {n.spoken ? "" : " · not spoken yet"}
              </span>
            ))}
          </div>
          <div className="muted small" style={{ marginTop: 6 }}>
            Names you add on the robot's parent page are spoken once their clip is made; until then the robot uses a soft chime.
          </div>
        </div>
      ) : null}
    </div>
  );
}
