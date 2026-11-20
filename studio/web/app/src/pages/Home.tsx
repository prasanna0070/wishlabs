import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ArrowUp, Box, CloudRain, LogOut, Mic, Moon, Paperclip, Sprout, Trash2, Eye, ImagePlus } from "lucide-react";
import { api } from "../lib/api";
import type { Mode, ProjectSummary, Upload } from "../lib/types";
import { stageLabel, timeAgo } from "../lib/format";
import { useAuth } from "../store/auth";
import { Logo, Spinner, Dropdown } from "../components/ui";
import { ModeSelect } from "../components/ModeSelect";
import { readAttachment } from "../components/chat/Composer";
import { searchWithoutWish, takeWish } from "../lib/wish";
import { VoiceWishDialog } from "../components/voice/VoiceWishDialog";
import { primeVoiceAudio } from "../lib/voiceCall";
import { chatBody, clipboardHasText, clipboardImages, dragHasFiles, FILE_ACCEPT, IMAGE_ACCEPT, imageRoom, savePending, sortFiles } from "../lib/uploads";
import { DraftImages, useImageQueue, useWindowFileDrop, useWindowPaste } from "../components/chat/ImageAttachments";

const EXAMPLES = [
  { icon: Moon, label: "Bedtime story robot", name: "Bedtime Story Robot", prompt: "I want a cute robot that tells my daughter bedtime stories and remembers her favourite characters." },
  { icon: CloudRain, label: "Cloud rain lamp", name: "Cloud Rain Lamp", prompt: "A small cloud-shaped desk lamp that glows blue when it is going to rain tomorrow." },
  { icon: Eye, label: "Desk buddy with animated eyes", name: "Desk Buddy", prompt: "A little desk buddy with animated eyes on a small screen that reacts when I tap it and chimes for my calendar." },
  { icon: Sprout, label: "Plant-mood light", name: "Plant Mood Light", prompt: "A light that sits by my plant and changes colour to show whether the plant is thirsty, happy or too cold." },
];

export function HomePage() {
  const [text, setText] = useState("");
  const [mode, setMode] = useState<Mode>("fast");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [name, setName] = useState<string | undefined>();
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [talking, setTalking] = useState(false);
  const talkBtn = useRef<HTMLButtonElement>(null);
  const ta = useRef<HTMLTextAreaElement>(null);
  const file = useRef<HTMLInputElement>(null);
  const imgIn = useRef<HTMLInputElement>(null);
  // start from an image: images wait here until Start creates the project, then upload one by one (with progress)
  const { q, items: images } = useImageQueue(null);
  const made = useRef<string | null>(null); // the project an earlier Start created (reused when an upload failed)
  const [drag, setDrag] = useState(false);
  const nav = useNavigate();
  const loc = useLocation();
  const setAuthed = useAuth((s) => s.setAuthed);
  const voiceOff = useAuth((s) => s.voiceOff);
  const wishTaken = useRef(false);

  // A wish from the landing page (?wish=..., or kept through sign-in) fills the composer. It is never sent for the
  // user: they read it, edit it and press Start themselves.
  useEffect(() => {
    if (wishTaken.current) return;
    wishTaken.current = true;
    const w = takeWish(loc.search);
    if (loc.search.includes("wish=")) nav({ pathname: loc.pathname, search: searchWithoutWish(loc.search) }, { replace: true });
    if (!w) return;
    setText(w);
    setName(undefined);
    requestAnimationFrame(() => {
      const el = ta.current;
      if (el) {
        el.focus();
        el.setSelectionRange(el.value.length, el.value.length);
      }
    });
  }, [loc.pathname, loc.search, nav]);

  useEffect(() => {
    api.projects().then(
      (r) => setProjects(r.projects),
      () => setProjects([]),
    );
  }, []);

  useEffect(() => {
    const el = ta.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${Math.min(260, el.scrollHeight)}px`;
    }
  }, [text]);

  /** Images (and text files) from the paperclip, a drop or a paste. Text files are read into the box as before. */
  const addFiles = async (list: FileList | File[]) => {
    if (busy) return;
    setErr(null);
    const { room, projectFull } = imageRoom(q.count, 0);
    const picked = sortFiles(Array.from(list), room, projectFull);
    if (picked.images.length) q.add(picked.images);
    const errors = [...picked.errors];
    for (const f of picked.texts) {
      const r = await readAttachment(f);
      if (r.error) errors.push(r.error);
      else if (r.text) setText((t) => `${t}${t ? "\n\n" : ""}${r.text}`);
    }
    if (errors.length) setErr(errors.join(" "));
    requestAnimationFrame(() => ta.current?.focus());
  };
  // drop anywhere on the page, or paste with the focus outside the box: the image is attached all the same (while
  // Start runs it is ignored, but a drop still never opens the file in place of the studio)
  useWindowFileDrop((list) => void addFiles(list), true, setDrag);
  useWindowPaste((list) => void addFiles(list));

  const start = async () => {
    const prompt = text.trim();
    const withImages = q.count > 0;
    if ((!prompt && !withImages) || busy) return;
    setBusy(true);
    setErr(null);
    try {
      let pid = made.current;
      if (!pid) {
        const p = await api.createProject({ mode, ...(name ? { name } : {}) });
        pid = made.current = p.id;
      }
      let ups: Upload[] = [];
      if (withImages) {
        for (const f of q.failed) q.retry(f.key); // Start again retries an image that did not go up
        q.setProject(pid);
        ups = await q.whenSettled();
      }
      const first = chatBody(prompt, [], ups);
      if (!first) throw new Error("Add a line or an image to start.");
      // the project page sends it as the first turn; router state backs up storage (private windows)
      if (!ups.length) {
        try {
          sessionStorage.setItem(`gs.pending.${pid}`, prompt);
        } catch {
          /* the router state below still carries it */
        }
      } else savePending(pid, first.text, ups);
      nav(`/p/${pid}`, { state: { first } });
    } catch (e: any) {
      setErr(e?.message || "Could not start a project.");
      setBusy(false);
    }
  };
  const upCount = images.length;
  const upDone = images.filter((x) => x.state === "done").length;
  const upNow = images.find((x) => x.state === "uploading");

  const remove = async (id: string) => {
    await api.deleteProject(id);
    setProjects((ps) => (ps || []).filter((p) => p.id !== id));
  };

  return (
    <div className="home">
      <header className="home-top">
        <Logo />
        <div className="grow" />
        <button
          className="btn ghost sm"
          onClick={async () => {
            await api.logout().catch(() => {});
            setAuthed(false);
          }}
        >
          <LogOut size={15} /> Sign out
        </button>
      </header>

      <section className="hero">
        <div className="hero-art" aria-hidden="true">
          <span className="shape s1" />
          <span className="shape s2" />
          <span className="shape s3" />
          <span className="shape s4" />
        </div>
        <h1>What will you make today?</h1>
        <p className="muted hero-sub">From an idea to a printed shell, a circuit board, firmware and a delivered price in India.</p>
        <form
          className={`home-composer${drag ? " drag" : ""}`}
          onSubmit={(e) => {
            e.preventDefault();
            void start();
          }}
          onDragOver={(e) => {
            if (!dragHasFiles(e.dataTransfer)) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = "copy";
          }}
          onDrop={(e) => {
            setDrag(false);
            if (!e.dataTransfer.files?.length) return;
            e.preventDefault();
            void addFiles(e.dataTransfer.files);
          }}
        >
          <DraftImages items={images} onRemove={(k) => q.remove(k)} onRetry={(k) => q.retry(k)} />
          <textarea
            ref={ta}
            value={text}
            rows={3}
            placeholder={images.length ? "Add a line about it: what it is for, who will use it (optional)" : "Describe the gadget you want to make"}
            onChange={(e) => {
              setText(e.target.value);
              setName(undefined);
            }}
            onPaste={(e) => {
              // Cmd+V of a screenshot or an image copied from ChatGPT: attach it; words in the paste still paste
              const pics = clipboardImages(e.clipboardData);
              if (!pics.length) return;
              if (!clipboardHasText(e.clipboardData)) e.preventDefault();
              void addFiles(pics);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void start();
              }
            }}
            aria-label="Describe the gadget you want to make"
          />
          <div className="composer-row">
            <button
              type="button"
              className="icon-btn sm"
              aria-label="Add images or files"
              title="Add an image (a sketch, a photo, a ChatGPT concept: JPEG, PNG, WebP, HEIC) or a text file. You can also paste or drop images."
              onClick={() => file.current?.click()}
              disabled={busy}
            >
              <Paperclip size={17} />
            </button>
            <input
              ref={file}
              type="file"
              hidden
              multiple
              accept={FILE_ACCEPT}
              onChange={(e) => {
                const list = e.target.files ? Array.from(e.target.files) : [];
                e.target.value = "";
                if (list.length) void addFiles(list);
              }}
            />
            {voiceOff ? null : (
              <button
                ref={talkBtn}
                type="button"
                className="talk-btn"
                onClick={() => {
                  primeVoiceAudio(); // inside the click: Safari starts audio only from a user gesture
                  setTalking(true);
                }}
                disabled={busy}
                title="Talk to make a wish: a short voice chat with Gemini Live"
              >
                <Mic size={16} /> Talk
              </button>
            )}
            <div className="grow" />
            <ModeSelect value={mode} onChange={setMode} />
            <button className="send-btn" aria-label="Start" disabled={(!text.trim() && !images.length) || busy}>
              {busy ? <Spinner /> : <ArrowUp size={18} />}
            </button>
          </div>
        </form>
        {busy && upCount ? (
          <div className="muted small home-img-note" role="status">
            {upNow
              ? `Uploading ${upCount === 1 ? "your image" : `image ${Math.min(upCount, upDone + 1)} of ${upCount}`} (${Math.round(upNow.progress * 100)}%)…`
              : upDone === upCount
                ? "Opening your project…"
                : "Creating your project…"}
          </div>
        ) : null}
        {err ? (
          <div className="callout danger" role="alert" style={{ maxWidth: 760, margin: "12px auto 0" }}>
            {err}
          </div>
        ) : null}
        <button type="button" className={`from-image${drag ? " drag" : ""}`} onClick={() => imgIn.current?.click()} disabled={busy}>
          <span className="from-image-icon" aria-hidden="true">
            <ImagePlus size={17} />
          </span>
          <span className="from-image-text">
            <strong>Start from an image</strong>
            <span className="fi-desk">
              Have a sketch, a product photo or a ChatGPT concept? Drop it here, paste it, or <span className="fi-cta">choose an image</span>
            </span>
            <span className="fi-touch">
              A sketch, a product photo or a ChatGPT concept: <span className="fi-cta">take a photo or choose one</span>
            </span>
          </span>
        </button>
        <input
          ref={imgIn}
          type="file"
          hidden
          multiple
          accept={IMAGE_ACCEPT}
          aria-hidden="true"
          tabIndex={-1}
          onChange={(e) => {
            const list = e.target.files ? Array.from(e.target.files) : [];
            e.target.value = "";
            if (list.length) void addFiles(list);
          }}
        />
        {talking ? (
          <VoiceWishDialog
            open={talking}
            mode={mode}
            text={text}
            onSubmitted={({ projectId, text: first }) => {
              // the first turn goes through the normal chat path: the project page sends it, like a typed wish
              try {
                sessionStorage.setItem(`gs.pending.${projectId}`, first);
              } catch {
                /* no storage (private mode): the project opens and the wish can be sent again from its chat */
              }
              setTalking(false);
              nav(`/p/${projectId}`);
            }}
            onClose={(said) => {
              setTalking(false);
              if (said && !text.trim()) {
                setText(said);
                setName(undefined);
              }
              requestAnimationFrame(() => (said ? ta.current : talkBtn.current)?.focus());
            }}
          />
        ) : null}
        <div className="examples">
          {EXAMPLES.map((x) => (
            <button
              key={x.label}
              className="chip"
              onClick={() => {
                setText(x.prompt);
                setName(x.name);
                ta.current?.focus();
              }}
            >
              <x.icon size={15} className="muted" /> {x.label}
            </button>
          ))}
        </div>
      </section>

      <section className="projects">
        <div className="row" style={{ marginBottom: 14 }}>
          <h2 style={{ fontSize: 18 }}>Your projects</h2>
          <span className="muted small">{projects ? `${projects.length}` : ""}</span>
        </div>
        {projects === null ? (
          <div className="row muted">
            <Spinner /> Loading
          </div>
        ) : projects.length === 0 ? (
          <div className="card pad muted" style={{ textAlign: "center", padding: 32 }}>
            Every gadget starts with a sentence. Describe one above, or pick an example.
          </div>
        ) : (
          <div className="proj-grid">
            {projects.map((p) => (
              <div key={p.id} className="proj-card">
                <Link to={`/p/${p.id}`} className="proj-link" aria-label={`Open ${p.name}`}>
                  <div className="proj-thumb">
                    {p.thumb_url ? <img src={p.thumb_url} alt="" loading="lazy" /> : <Box size={34} strokeWidth={1.4} />}
                  </div>
                  <div className="proj-meta">
                    <div className="proj-name">{p.name || "Untitled gadget"}</div>
                    <div className="row small muted">
                      <span className={`dot-stage ${p.stage}`} /> {stageLabel(p.stage)}
                      {p.current_version ? <span>· v{p.current_version}</span> : null}
                      <span className="grow" />
                      <span>{timeAgo(p.updated)}</span>
                    </div>
                  </div>
                </Link>
                <Dropdown
                  align="right"
                  trigger={({ toggle }) => (
                    <button className="icon-btn sm proj-menu" aria-label="Project actions" onClick={toggle}>
                      ⋯
                    </button>
                  )}
                >
                  {(close) => (
                    <button
                      className="menu-item danger"
                      onClick={() => {
                        close();
                        if (confirm(`Remove “${p.name}” from your projects? Its files are kept.`)) void remove(p.id);
                      }}
                    >
                      <Trash2 size={15} /> Remove from list
                    </button>
                  )}
                </Dropdown>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
