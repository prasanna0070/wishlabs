import { Fragment, useEffect, useMemo, useState } from "react";
import { ChevronRight, CircleCheck, CircleX, Download, File, FileCode2, Folder, FolderOpen } from "lucide-react";
import { useProject } from "../../store/project";
import { api, fetchFile } from "../../lib/api";
import { useVersionFile } from "../../lib/hooks";
import { fileUrl } from "../../lib/urls";
import { bytes } from "../../lib/format";
import type { Build, FileEntry } from "../../lib/types";
import { Spinner } from "../ui";
import type { TabProps } from "./RightPanel";

const TEXT_EXT = /\.(cpp|cc|c|h|hpp|ino|py|json|ini|md|txt|csv|toml|ya?ml|svg|kicad_pro|kicad_sch|kicad_pcb|net|sh|cfg|log|xml|html|css|js|ts|gbr|drl|step|stp)$/i;
const NO_HL_LIMIT = 200_000;
const MAX_SHOW = 600_000;

function langOf(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase() || "";
  if (["cpp", "cc", "c", "h", "hpp", "ino"].includes(ext)) return "cpp";
  if (ext === "py") return "python";
  if (ext === "json" || ext === "kicad_pro") return "json";
  if (ext === "ini" || ext === "cfg") return "ini";
  if (ext === "md") return "markdown";
  if (ext === "toml") return "toml";
  if (ext === "yaml" || ext === "yml") return "yaml";
  if (ext === "svg" || ext === "xml" || ext === "html") return "xml";
  if (ext === "sh") return "shellscript";
  return "text";
}

let hlPromise: Promise<any> | null = null;
function highlighter() {
  // fine-grained shiki: only our grammars. Oniguruma (wasm, lazy-loaded here only): the JavaScript regex engine
  // cannot compile the C++ grammar ("Invalid named capture referenced by subroutine").
  if (!hlPromise)
    hlPromise = Promise.all([import("shiki/core"), import("shiki/engine/oniguruma")]).then(([core, eng]) =>
      core.createHighlighterCore({
        themes: [import("shiki/themes/github-light.mjs")],
        langs: [
          import("shiki/langs/cpp.mjs"),
          import("shiki/langs/python.mjs"),
          import("shiki/langs/json.mjs"),
          import("shiki/langs/ini.mjs"),
          import("shiki/langs/markdown.mjs"),
          import("shiki/langs/toml.mjs"),
          import("shiki/langs/yaml.mjs"),
          import("shiki/langs/xml.mjs"),
          import("shiki/langs/shellscript.mjs"),
        ],
        engine: eng.createOnigurumaEngine(import("shiki/wasm")),
      }),
    );
  return hlPromise;
}

interface Node {
  name: string;
  path: string;
  file?: FileEntry;
  children: Map<string, Node>;
}

function tree(files: FileEntry[]): Node {
  const root: Node = { name: "", path: "", children: new Map() };
  for (const f of files) {
    const parts = f.path.split("/");
    let cur = root;
    parts.forEach((p, i) => {
      const path = parts.slice(0, i + 1).join("/");
      if (!cur.children.has(p)) cur.children.set(p, { name: p, path, children: new Map(), ...(i === parts.length - 1 ? { file: f } : {}) });
      cur = cur.children.get(p)!;
    });
  }
  return root;
}

const ORDER = ["firmware", "cad", "pcb", "manual", "print", "checks", "order"];

export default function CodeTab({ version }: TabProps) {
  const ctx = useProject((s) => s.ctx)!;
  const build = useVersionFile<Build>(ctx, version.n, "build.json");
  const size = useVersionFile<any>(ctx, version.n, "firmware/size.json");
  const [files, setFiles] = useState<FileEntry[] | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set(["firmware", "firmware/src", "firmware/include"]));

  useEffect(() => {
    let alive = true;
    const fromManifest = () => fetchFile(fileUrl(ctx, version.n, "manifest.json")).then((m: any) => m?.files || []);
    (ctx.share ? fromManifest() : api.versionDetail(ctx.pid, version.n).then((d) => d.files || []).then((f) => (f.length ? f : fromManifest())))
      .catch(() => fromManifest().catch(() => []))
      .then((f: FileEntry[]) => {
        if (!alive) return;
        const list = f.filter((x) => !/(^|\/)\./.test(x.path) && x.path !== "status.json");
        setFiles(list);
        const main = list.find((x) => /^firmware\/src\/main\.(cpp|ino)$/.test(x.path)) || list.find((x) => x.path.startsWith("firmware/src/")) || list.find((x) => x.path === "cad/gadget_cad.py");
        setSel(main?.path || list.find((x) => TEXT_EXT.test(x.path))?.path || null);
      });
    return () => {
      alive = false;
    };
  }, [ctx, version.n]);

  const root = useMemo(() => (files ? tree(files) : null), [files]);
  const fw = build.data?.firmware;
  const selFile = files?.find((f) => f.path === sel) || null;

  return (
    <div className="code-tab">
      <div className="fw-bar">
        <strong>Firmware</strong>
        {fw ? (
          <>
            <span className={`pill ${fw.compiled ? "success" : "danger"}`}>
              {fw.compiled ? <CircleCheck size={13} /> : <CircleX size={13} />} {fw.compiled ? "Compiled" : "Not compiled"} · {fw.board || "esp32-s3"}
            </span>
            {fmtSize(size.data, fw.size) ? <span className="pill">{fmtSize(size.data, fw.size)}</span> : null}
            {fw.tests_total ? (
              <span className={`pill ${fw.tests_passed === fw.tests_total ? "success" : "warning"}`}>
                Unit tests {fw.tests_passed}/{fw.tests_total} passed
              </span>
            ) : null}
            <span className="muted small">{fw.framework ? `${fw.framework} on PlatformIO` : ""}</span>
          </>
        ) : (
          <span className="muted small">No firmware block in build.json.</span>
        )}
        <div className="grow" />
        {files?.some((f) => f.path === "firmware/firmware.bin") ? (
          <a className="btn sm" href={fileUrl(ctx, version.n, "firmware/firmware.bin", true)}>
            <Download size={14} /> firmware.bin
          </a>
        ) : null}
      </div>
      <div className="code-split">
        <nav className="file-tree" aria-label="Files">
          {!root ? (
            <div className="row muted small" style={{ padding: 12 }}>
              <Spinner /> Loading files
            </div>
          ) : (
            <TreeNodes node={root} depth={0} open={open} setOpen={setOpen} sel={sel} onSel={setSel} />
          )}
        </nav>
        <div className="code-view">{selFile ? <FileView file={selFile} n={version.n} /> : <div className="empty">Choose a file.</div>}</div>
      </div>
    </div>
  );
}

function fmtSize(s: any, b: any): string | null {
  const src = s || b;
  if (!src || typeof src !== "object") return null;
  const flash = src.flash_bytes ?? src.flash?.used ?? src.program;
  const flashT = src.flash_total ?? src.flash?.total;
  const ram = src.ram_bytes ?? src.ram?.used ?? src.data;
  const ramT = src.ram_total ?? src.ram?.total;
  const bits: string[] = [];
  if (flash) bits.push(`Flash ${bytes(flash)}${flashT ? ` of ${bytes(flashT)}` : ""}`);
  if (ram) bits.push(`RAM ${bytes(ram)}${ramT ? ` of ${bytes(ramT)}` : ""}`);
  return bits.length ? bits.join(" · ") : null;
}

function TreeNodes({ node, depth, open, setOpen, sel, onSel }: { node: Node; depth: number; open: Set<string>; setOpen: (s: Set<string>) => void; sel: string | null; onSel: (p: string) => void }) {
  const kids = [...node.children.values()].sort((a, b) => {
    const ad = a.file ? 1 : 0, bd = b.file ? 1 : 0;
    if (ad !== bd) return ad - bd;
    if (depth === 0 && !a.file && !b.file) {
      const ra = ORDER.indexOf(a.name) < 0 ? 99 : ORDER.indexOf(a.name);
      const rb = ORDER.indexOf(b.name) < 0 ? 99 : ORDER.indexOf(b.name);
      if (ra !== rb) return ra - rb;
    }
    return a.name.localeCompare(b.name);
  });
  return (
    <>
      {kids.map((k) =>
        k.file ? (
          <button key={k.path} className={`tree-row file${sel === k.path ? " on" : ""}`} style={{ paddingLeft: 10 + depth * 14 }} onClick={() => onSel(k.path)} title={k.path}>
            {TEXT_EXT.test(k.name) ? <FileCode2 size={14} /> : <File size={14} />}
            <span className="grow">{k.name}</span>
          </button>
        ) : (
          <div key={k.path}>
            <button
              className="tree-row dir"
              style={{ paddingLeft: 10 + depth * 14 }}
              onClick={() => {
                const s = new Set(open);
                if (s.has(k.path)) s.delete(k.path);
                else s.add(k.path);
                setOpen(s);
              }}
              aria-expanded={open.has(k.path)}
            >
              <ChevronRight size={13} className={open.has(k.path) ? "rot" : ""} />
              {open.has(k.path) ? <FolderOpen size={14} /> : <Folder size={14} />}
              <span className="grow">{k.name}</span>
            </button>
            {open.has(k.path) ? <TreeNodes node={k} depth={depth + 1} open={open} setOpen={setOpen} sel={sel} onSel={onSel} /> : null}
          </div>
        ),
      )}
    </>
  );
}

function FileView({ file, n }: { file: FileEntry; n: number }) {
  const ctx = useProject((s) => s.ctx)!;
  const isText = TEXT_EXT.test(file.path) && file.bytes < MAX_SHOW;
  const isImg = /\.(png|jpe?g|webp|gif)$/i.test(file.path);
  const txt = useVersionFile<string>(isText ? ctx : null, n, file.path, "text");
  const [html, setHtml] = useState<string | null>(null);
  useEffect(() => {
    setHtml(null);
    if (!txt.data || file.bytes > NO_HL_LIMIT) return;
    const lang = langOf(file.path);
    if (lang === "text") return;
    let alive = true;
    highlighter()
      .then((h) => alive && setHtml(h.codeToHtml(txt.data!, { lang, theme: "github-light" })))
      .catch((e) => {
        hlPromise = null; // allow a retry; the plain view stays
        console.warn("syntax highlighting unavailable:", e?.message || e);
      });
    return () => {
      alive = false;
    };
  }, [txt.data, file.path, file.bytes]);
  const lines = txt.data ? txt.data.split("\n").length : 0;
  return (
    <div className="file-view">
      <div className="file-head">
        <span className="mono small grow">{file.path}</span>
        <span className="muted small">
          {lines ? `${lines} lines · ` : ""}
          {bytes(file.bytes)}
        </span>
        <a className="btn sm" href={fileUrl(ctx, n, file.path, true)}>
          <Download size={14} /> Download original · {bytes(file.bytes)}
        </a>
      </div>
      <div className="file-body">
        {isImg ? (
          <div style={{ padding: 16 }}>
            <img src={fileUrl(ctx, n, file.path)} alt={file.path} style={{ maxWidth: "100%" }} />
          </div>
        ) : !isText ? (
          <div className="empty">
            <File size={28} />
            <div>Binary file. Download it to open it in its own tool.</div>
          </div>
        ) : txt.loading ? (
          <div className="row muted small" style={{ padding: 16 }}>
            <Spinner /> Loading
          </div>
        ) : html ? (
          <div className="shiki-wrap with-lines" dangerouslySetInnerHTML={{ __html: html }} />
        ) : (
          <pre className="plain-code with-lines">
            <code>
              {(txt.data || "").split("\n").map((l, i) => (
                <Fragment key={i}>
                  <span className="line">{l}</span>
                  {"\n"}
                </Fragment>
              ))}
            </code>
          </pre>
        )}
      </div>
    </div>
  );
}
