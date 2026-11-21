import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowUp, FileText, Mic, Paperclip, X } from "lucide-react";
import { useProject } from "../../store/project";
import { useNow } from "../../lib/hooks";
import { agoText } from "../../lib/working";
import { ModeSelect } from "../ModeSelect";
import { Spinner } from "../ui";
import { VoiceWishDialog } from "../voice/VoiceWishDialog";
import { primeVoiceAudio } from "../../lib/voiceCall";
import { useAuth } from "../../store/auth";
import { chatBody, clipboardHasText, clipboardImages, dragHasFiles, FILE_ACCEPT, imageRoom, sortFiles, storedImageCount, uploadUrl } from "../../lib/uploads";
import { DraftImages, useImageQueue, useWindowFileDrop, useWindowPaste } from "./ImageAttachments";
import type { Upload } from "../../lib/types";

const TEXT_TYPES = /\.(txt|md|markdown|json|csv|ini|yaml|yml|toml)$/i;
const MAX_ATTACH = 60_000;

/** Text files become part of the message text; images are uploaded and sent as attachments (lib/uploads.ts). */
export async function readAttachment(f: File): Promise<{ text?: string; error?: string }> {
  if (!(f.type.startsWith("text/") || TEXT_TYPES.test(f.name) || f.type === "application/json")) {
    return { error: `${f.name}: attach an image (JPEG, PNG, WebP, HEIC) or a text file (notes, a spec, CSV, JSON).` };
  }
  if (f.size > MAX_ATTACH) return { error: `${f.name} is larger than 60 KB. Paste the part that matters instead.` };
  const body = await f.text();
  return { text: `Attached file ${f.name}:\n\`\`\`\n${body.trim()}\n\`\`\`` };
}

export interface ComposerHandle {
  focus: (prefill?: string) => void;
}

export const Composer = forwardRef<ComposerHandle, { placeholder: string; disabled?: boolean }>(function Composer({ placeholder, disabled }, ref) {
  const isGuest = useAuth((s) => s.guest !== null); // magic-link guests design in Standard mode only
  const project = useProject((s) => s.project)!;
  const turn = useProject((s) => s.turn);
  const send = useProject((s) => s.send);
  const setMode = useProject((s) => s.setMode);
  const [text, setText] = useState("");
  const [files, setFiles] = useState<{ name: string; text: string }[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const ta = useRef<HTMLTextAreaElement>(null);
  const fileIn = useRef<HTMLInputElement>(null);
  const talkBtn = useRef<HTMLButtonElement>(null);
  const [talking, setTalking] = useState(false);
  const nav = useNavigate();
  const running = !!turn?.running;
  useNow(15000, running); // re-render so "started N min ago" stays current
  // a typed message the studio did not take (a 409, or the connection dropped before it arrived) comes back as typed,
  // attachments included, unless something new is already in the box
  const unsent = useProject((s) => s.unsent);
  // images upload as soon as they are added (the project exists), one at a time; Send waits for them
  const { q, items: images } = useImageQueue(project.id);
  const lastSubmit = useRef<{ body: string; typed: string; files: { name: string; text: string }[]; images: Upload[] } | null>(null);
  useEffect(() => {
    if (!unsent) return;
    useProject.setState({ unsent: null });
    if (ta.current?.value || q.count) return;
    const mine = lastSubmit.current;
    if (mine && mine.body === unsent.text) {
      setText(mine.typed);
      setFiles(mine.files);
      if (mine.images.length) q.restore(mine.images);
    } else {
      setText(unsent.text);
      if (unsent.attachments?.length) q.restore(unsent.attachments.map((a) => ({ id: a.id, thumb_url: uploadUrl({ pid: project.id }, a.id, true) })));
    }
    lastSubmit.current = null;
  }, [unsent, q, project.id]);
  // "Talk" while the project is still empty: the voice chat becomes this project's first message
  const voiceOff = useAuth((s) => s.voiceOff);
  const canTalk = project.chat.length === 0 && !running && !project.voice && !voiceOff;

  useImperativeHandle(ref, () => ({
    focus: (prefill) => {
      if (prefill != null) setText(prefill);
      window.setTimeout(() => {
        const el = ta.current;
        if (el) {
          el.focus();
          el.setSelectionRange(el.value.length, el.value.length);
        }
      }, 0);
    },
  }));

  useEffect(() => {
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(220, Math.max(46, el.scrollHeight))}px`;
  }, [text]);

  const add = async (list: FileList | File[]) => {
    if (disabled) return;
    setErr(null);
    const mine = q.items.map((x) => x.upload?.id).filter((x): x is string => !!x);
    const { room, projectFull } = imageRoom(q.count, storedImageCount(project, mine));
    const picked = sortFiles(Array.from(list), room, projectFull);
    if (picked.images.length) q.add(picked.images);
    const errors = [...picked.errors];
    for (const f of picked.texts) {
      const r = await readAttachment(f);
      if (r.error) errors.push(r.error);
      else if (r.text) setFiles((fs) => [...fs, { name: f.name, text: r.text! }]);
    }
    if (errors.length) setErr(errors.join(" "));
  };

  // a file dropped anywhere on the project page lands here (and the composer lights up while one is dragged); so
  // does an image pasted while the focus is not in a text field. Always on: a drop must never open the file instead.
  useWindowFileDrop((list) => void add(list), true, setDrag);
  useWindowPaste((list) => void add(list));

  const uploading = q.busy;
  const failed = images.find((x) => x.state === "error");
  const ready = images.filter((x) => x.state === "done").length;
  const submit = () => {
    if (running || disabled) return;
    if (uploading) {
      setErr("The image is still uploading; send once it is done.");
      return;
    }
    if (failed) {
      setErr(`${failed.error || `${failed.name} did not upload.`} Remove it or try it again.`);
      return;
    }
    const ups = q.uploads();
    const body = chatBody(text, files.map((f) => f.text), ups);
    if (!body) return;
    setErr(null);
    lastSubmit.current = { body: body.text, typed: text, files, images: ups };
    void send(body);
    setText("");
    setFiles([]);
    q.clear();
  };

  return (
    <div className="composer-wrap">
      {running ? (
        // a turn is running (here or on the server after a reload): sending now would only get a 409. Only the fixed
        // words are a live region, so the minute counter is not announced again every minute.
        <div className="composer-busy">
          <Spinner />
          <span>
            <span role="status">Still working on your last message</span> (started {agoText((Date.now() - turn!.startedAt) / 1000)})
          </span>
        </div>
      ) : null}
      <form
        className={`composer${drag ? " drag" : ""}`}
        onSubmit={(e) => {
          e.preventDefault();
          submit();
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
          if (disabled) return;
          void add(e.dataTransfer.files);
        }}
      >
        <DraftImages items={images} onRemove={(k) => q.remove(k)} onRetry={(k) => q.retry(k)} />
        {files.length ? (
          <div className="attach-row">
            {files.map((f, i) => (
              <span key={i} className="file-chip">
                <FileText size={13} /> {f.name}
                <button type="button" aria-label={`Remove ${f.name}`} onClick={() => setFiles((fs) => fs.filter((_, j) => j !== i))}>
                  <X size={12} />
                </button>
              </span>
            ))}
          </div>
        ) : null}
        <textarea
          ref={ta}
          rows={1}
          value={text}
          placeholder={images.length && !text ? "Add a line about it (optional)" : placeholder}
          disabled={disabled}
          onChange={(e) => setText(e.target.value)}
          onPaste={(e) => {
            // Cmd+V of a screenshot or a copied image (ChatGPT, a browser): attach it; any words in the paste still paste
            const pics = clipboardImages(e.clipboardData);
            if (!pics.length) return;
            if (!clipboardHasText(e.clipboardData)) e.preventDefault();
            void add(pics);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          aria-label="Message"
        />
        <div className="composer-row">
          <button
            type="button"
            className="icon-btn sm"
            aria-label="Add images or files"
            title="Add an image (a sketch, a photo, a ChatGPT concept: JPEG, PNG, WebP, HEIC) or a text file. You can also paste or drop images."
            onClick={() => fileIn.current?.click()}
            disabled={disabled}
          >
            <Paperclip size={17} />
          </button>
          <input
            ref={fileIn}
            type="file"
            hidden
            multiple
            accept={FILE_ACCEPT}
            onChange={(e) => {
              if (e.target.files) void add(e.target.files);
              e.target.value = "";
            }}
          />
          {canTalk ? (
            <button
              ref={talkBtn}
              type="button"
              className="talk-btn"
              onClick={() => {
                primeVoiceAudio(); // inside the click: Safari starts audio only from a user gesture
                setTalking(true);
              }}
              disabled={disabled}
              title="Talk to make a wish: a short voice chat with Gemini Live"
            >
              <Mic size={16} /> Talk
            </button>
          ) : null}
          <div className="grow" />
          {isGuest ? null : <ModeSelect value={project.mode || "pro"} onChange={(m) => void setMode(m)} up disabled={disabled} />}
          <button
            className="send-btn"
            aria-label={running ? "Working" : uploading ? "Uploading the image" : "Send"}
            title={uploading ? "Uploading the image…" : undefined}
            disabled={running || disabled || uploading || !!failed || (!text.trim() && !files.length && !ready)}
          >
            {running || uploading ? <Spinner /> : <ArrowUp size={18} />}
          </button>
        </div>
      </form>
      {err ? (
        <div className="composer-err" role="alert">
          {err}
        </div>
      ) : failed ? (
        <div className="composer-err" role="alert">
          {failed.error || `${failed.name} did not upload.`}
        </div>
      ) : null}
      {talking ? (
        <VoiceWishDialog
          open={talking}
          mode={project.mode || "pro"}
          text={text}
          projectId={project.id}
          onSubmitted={({ projectId, text: first }) => {
            setTalking(false);
            setText("");
            if (projectId === project.id) {
              void send({ text: first }); // the normal chat turn, streamed here
              return;
            }
            try {
              sessionStorage.setItem(`gs.pending.${projectId}`, first);
            } catch {
              /* no storage: the new project opens with an empty chat */
            }
            nav(`/p/${projectId}`);
          }}
          onClose={(said) => {
            setTalking(false);
            if (said && !text.trim()) setText(said);
            requestAnimationFrame(() => (said ? ta.current : talkBtn.current)?.focus());
          }}
        />
      ) : null}
      <div className="disclaimer">Wish Labs can make mistakes. Double-check builds and responses.</div>
    </div>
  );
});
