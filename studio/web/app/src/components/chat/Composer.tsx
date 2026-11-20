import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowUp, Mic, Paperclip, Plus, X } from "lucide-react";
import { useProject } from "../../store/project";
import { ModeSelect } from "../ModeSelect";
import { Spinner } from "../ui";
import { VoiceWishDialog } from "../voice/VoiceWishDialog";

const TEXT_TYPES = /\.(txt|md|markdown|json|csv|ini|yaml|yml|toml)$/i;
const MAX_ATTACH = 60_000;

/** Attachments become part of the message text (the API has no file upload yet). */
export async function readAttachment(f: File): Promise<{ text?: string; error?: string }> {
  if (!(f.type.startsWith("text/") || TEXT_TYPES.test(f.name) || f.type === "application/json")) {
    return { error: `${f.name}: only text files (notes, specs, CSV, JSON) can be attached for now.` };
  }
  if (f.size > MAX_ATTACH) return { error: `${f.name} is larger than 60 KB. Paste the part that matters instead.` };
  const body = await f.text();
  return { text: `Attached file ${f.name}:\n\`\`\`\n${body.trim()}\n\`\`\`` };
}

export interface ComposerHandle {
  focus: (prefill?: string) => void;
}

export const Composer = forwardRef<ComposerHandle, { placeholder: string; disabled?: boolean }>(function Composer({ placeholder, disabled }, ref) {
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
  // "Talk" while the project is still empty: the voice chat becomes this project's first message
  const canTalk = project.chat.length === 0 && !running && !(project as { voice?: unknown }).voice;

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
    setErr(null);
    for (const f of Array.from(list)) {
      const r = await readAttachment(f);
      if (r.error) setErr(r.error);
      else if (r.text) setFiles((fs) => [...fs, { name: f.name, text: r.text! }]);
    }
  };

  const submit = () => {
    const body = [text.trim(), ...files.map((f) => f.text)].filter(Boolean).join("\n\n");
    if (!body || running || disabled) return;
    void send({ text: body });
    setText("");
    setFiles([]);
  };

  return (
    <div className="composer-wrap">
      <form
        className={`composer${drag ? " drag" : ""}`}
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          void add(e.dataTransfer.files);
        }}
      >
        {files.length ? (
          <div className="attach-row">
            {files.map((f, i) => (
              <span key={i} className="file-chip">
                <Paperclip size={13} /> {f.name}
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
          placeholder={placeholder}
          disabled={disabled}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          aria-label="Message"
        />
        <div className="composer-row">
          <button type="button" className="icon-btn sm" aria-label="Add files" title="Attach a text file (notes, a spec)" onClick={() => fileIn.current?.click()} disabled={disabled}>
            <Plus size={18} />
          </button>
          <input
            ref={fileIn}
            type="file"
            hidden
            multiple
            accept=".txt,.md,.json,.csv,.ini,.yaml,.yml,.toml,text/*"
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
              onClick={() => setTalking(true)}
              disabled={disabled}
              title="Talk to make a wish: a short voice chat with Gemini Live"
            >
              <Mic size={16} /> Talk
            </button>
          ) : null}
          <div className="grow" />
          <ModeSelect value={project.mode || "pro"} onChange={(m) => void setMode(m)} up disabled={disabled} />
          <button className="send-btn" aria-label={running ? "Working" : "Send"} disabled={running || disabled || (!text.trim() && !files.length)}>
            {running ? <Spinner /> : <ArrowUp size={18} />}
          </button>
        </div>
      </form>
      {err ? <div className="composer-err">{err}</div> : null}
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
