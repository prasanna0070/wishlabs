// Overview: the first tab of a read-only share link (/studio/s/<token>), the page a juror or a visitor reads first.
// One scrollable story: the wish, the concepts and the chosen product photo, the 3D model, the engineering pack with
// its check results and files, the engine's live price, and the AI receipts (which Google model did what).
//
// Cards that other features add (VoiceWishCard, SoundCard) mount here through ./cards.ts (import.meta.glob), so this
// tab builds with or without them; a card that throws renders nothing.
import { Component, lazy, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowRight, CircuitBoard, CodeXml, Download, FileArchive, MessagesSquare, Package, Quote as QuoteIcon, Receipt, ShieldCheck, Sparkles, Wrench } from "lucide-react";
import { useProject } from "../../store/project";
import { useFileIndex, useVersionFile, useVersionFiles } from "../../lib/hooks";
import { exportUrl, fileUrl, type Ctx } from "../../lib/urls";
import { bytes, checkTally, inr, plainText, statusWords } from "../../lib/format";
import { aiReceipts, compact, usd, type Receipts } from "../../lib/receipts";
import { buildTime, chosenConcept, conceptImage, fileGroups, originalWish, PRICE_LINE, resultMessage, showcaseVersion, testCount } from "../../lib/showcase";
import type { Build, Concept, Quote, Version } from "../../lib/types";
import { Logo, Spinner } from "../ui";
import type { TabId } from "./RightPanel";
import { cardWanted, pickCard, type CardProps, type MountedCard } from "./cards";
import "./OverviewTab.css";

const ShareViewer = lazy(() => import("../share/ShareViewer"));
// Gemini Live voice wish and Lyria gadget sound cards, when their features are part of this build (see ./cards.ts).
const VOICE_CARD = pickCard(import.meta.glob<Record<string, unknown>>("/src/**/VoiceWishCard.tsx", { eager: true }), "VoiceWishCard", "hasVoiceWish");
const SOUND_CARD = pickCard(import.meta.glob<Record<string, unknown>>("/src/**/SoundCard.tsx", { eager: true }), "SoundCard", "hasSound");

class Quiet extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(e: unknown) {
    console.warn("overview card failed", e);
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

function Mount({ m, props, id }: { m: MountedCard | null; props: CardProps; id: string }) {
  if (!m || !cardWanted(m, props.project, props.version)) return null;
  return (
    <Quiet>
      <div className="ov-mount" id={id}>
        <m.C {...props} />
      </div>
    </Quiet>
  );
}

const SECTIONS = [
  { id: "ov-wish", label: "Wish" },
  { id: "ov-concepts", label: "Concepts" },
  { id: "ov-model", label: "3D" },
  { id: "ov-engineering", label: "Engineering" },
  { id: "ov-price", label: "Price" },
  { id: "ov-ai", label: "AI receipts" },
];

export default function OverviewTab({ onTab }: { onTab: (t: TabId) => void }) {
  const project = useProject((s) => s.project)!;
  const ctx = useProject((s) => s.ctx)!;
  const readOnly = useProject((s) => s.readOnly);
  const v = showcaseVersion(project);
  const wish = originalWish(project);
  const chosen = chosenConcept(project, v);
  const result = resultMessage(project, v?.n);
  const summary = result?.payload?.summary || null;
  const receipts = useMemo(() => aiReceipts(project.usage as any), [project.usage]);
  const photosModel = receipts?.rows.find((r) => r.role === "photos")?.modelName || null;
  const agentModels = [...new Set(receipts?.rows.filter((r) => r.role === "agent").map((r) => r.modelName) || [])];
  const build = useVersionFile<Build>(v ? ctx : null, v?.n, "build.json");
  const quote = useVersionFile<Quote>(v ? ctx : null, v?.n, "order/quote.json");
  const b = build.data;
  const fwTests = b?.firmware?.tests_total ? { passed: b.firmware.tests_passed ?? 0, total: b.firmware.tests_total } : testCount(summary?.checks?.firmware_tests);
  const drc = b?.pcb?.drc_errors ?? summary?.checks?.pcb_drc_errors ?? null;
  const erc = b?.pcb?.erc_errors ?? summary?.checks?.pcb_erc_errors ?? null;
  const priceInr = quote.data?.delivered_price_inr?.pilot_one_off ?? summary?.price?.inr ?? null;
  const priced = !!quote.data && isPriced(quote.data) && priceInr != null;
  const shown = project.concepts.filter((c) => conceptImage(c) && c.state !== "failed");
  const building = project.versions.some((x) => x.state === "queued" || x.state === "running");
  const cardProps: CardProps = { project, version: v, ctx, readOnly, compact: false };
  const scroller = useRef<HTMLDivElement>(null);
  const questions = project.questions_asked || 0;
  const time = v ? buildTime(v.started, v.ended) : null;

  const facts: string[] = [];
  if (project.concepts.length) facts.push(`${project.concepts.length} concept${project.concepts.length === 1 ? "" : "s"}`);
  if (b?.printed?.length) facts.push(`${b.printed.length} printed parts`);
  if (b?.pcb?.routed && b.pcb.layers) facts.push(`${b.pcb.layers}-layer routed PCB`);
  if (fwTests) facts.push(`${fwTests.passed}/${fwTests.total} firmware tests`);
  if (b?.steps?.length) facts.push(`${b.steps.length}-step manual`);
  if (priced) facts.push(`${inr(priceInr)} first unit`);

  const go = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });

  return (
    <div className="ov-scroll" ref={scroller}>
      <nav className="ov-nav" aria-label="Overview sections">
        {SECTIONS.map((s) => (
          <button key={s.id} type="button" onClick={() => go(s.id)}>
            {s.label}
          </button>
        ))}
      </nav>
      <div className="ov">
        <section className="ov-hero ov-sec" id="ov-wish" aria-labelledby="ov-title">
          <div className="ov-hero-text">
            <div className="ov-eyebrow">
              <Sparkles size={14} /> One wish, designed by AI
            </div>
            <h2 id="ov-title" className="ov-h1">
              {project.name || "A Wish Labs gadget"}
            </h2>
            {wish ? (
              <blockquote className="ov-wish">
                <QuoteIcon size={18} aria-hidden="true" />
                <p>{wish}</p>
                <footer>The wish, as typed{project.created ? `, ${dateOnly(project.created)}` : ""}</footer>
              </blockquote>
            ) : null}
            <p className="ov-lede">
              From that sentence, the Wish Labs design agent{agentModels.length ? ` (${agentModels.join(" and ")} on Vertex AI)` : ""}
              {questions ? ` asked ${questions} question${questions === 1 ? "" : "s"},` : ""} proposed {project.concepts.length || "its"} concept
              {project.concepts.length === 1 ? "" : "s"}, each a complete engineering spec.
              {chosen ? ` “${chosen.title}” was chosen` : ""}
              {v ? `${chosen ? " and the" : " The"} build pipeline made everything below${time ? ` in ${time}` : ""}.` : chosen ? "." : ""}
            </p>
            {facts.length ? (
              <ul className="ov-facts" aria-label="What this build produced">
                {facts.map((f) => (
                  <li key={f}>{f}</li>
                ))}
              </ul>
            ) : null}
            <button className="btn sm ov-chat-link" type="button" onClick={() => onTab("preview")}>
              <MessagesSquare size={15} /> Read the conversation
            </button>
          </div>
          <figure className="ov-photo">
            {chosen && conceptImage(chosen) ? (
              <>
                <img src={conceptImage(chosen)!} alt={`${chosen.title}: ${chosen.photo_url ? "product photo" : "CAD render"}`} />
                <figcaption>
                  <strong>{chosen.title}</strong>
                  <span>{chosen.photo_url ? `Product photo made by ${photosModel || "an image model"} from the CAD render` : "Render of the CAD model"}</span>
                </figcaption>
              </>
            ) : (
              <div className="ov-photo-none">No picture for this design yet.</div>
            )}
          </figure>
        </section>

        <Mount m={VOICE_CARD} props={cardProps} id="ov-voice" />

        {building ? (
          <div className="callout row ov-note" role="status">
            <Spinner /> A new version of this design is building right now. This page shows the last finished one.
          </div>
        ) : null}

        <Concepts concepts={project.concepts} shown={shown} chosen={chosen} />

        {v ? (
          <>
            <ModelSection ctx={ctx} v={v} b={b} title={chosen?.title || project.name} onTab={onTab} />
            <Mount m={SOUND_CARD} props={cardProps} id="ov-sound" />
            <Engineering ctx={ctx} v={v} b={b} summary={summary} fwTests={fwTests} drc={drc} erc={erc} onTab={onTab} />
            <Price quote={quote.data} loading={quote.loading} priced={priced} priceInr={priceInr} onTab={onTab} />
          </>
        ) : (
          <>
            <Mount m={SOUND_CARD} props={cardProps} id="ov-sound" />
            <section className="ov-sec" id="ov-model">
              <div className="callout">This design has no finished build yet, so there is no 3D model, engineering pack or engine quote to show.</div>
            </section>
            <section className="ov-sec" id="ov-engineering" hidden />
            <PriceLineOnly />
          </>
        )}

        <AiReceipts receipts={receipts} />

        <footer className="ov-foot">
          <Logo size={22} />
          <p>Describe a gadget in plain words; Wish Labs designs it: the printed shell, the circuit board, the firmware, a build manual and a price.</p>
          <a className="btn" href="/">
            Visit Wish Labs <ArrowRight size={15} />
          </a>
        </footer>
      </div>
    </div>
  );
}

function dateOnly(iso: string): string {
  const t = new Date(iso);
  return Number.isNaN(t.getTime()) ? "" : t.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

function isPriced(q: Quote): boolean {
  return !!q.delivered_price_inr?.pilot_one_off && !/HELD|NOT_QUOTED|UNSUPPORTED/i.test(q.status || "");
}

function SecHead({ icon, title, sub, children }: { icon: ReactNode; title: string; sub?: ReactNode; children?: ReactNode }) {
  return (
    <div className="ov-sec-head">
      <div className="ov-sec-icon" aria-hidden="true">
        {icon}
      </div>
      <div className="grow">
        <h3>{title}</h3>
        {sub ? <p>{sub}</p> : null}
      </div>
      {children}
    </div>
  );
}

const TIER_WORDS: Record<string, string> = { expected: "Expected", different: "Different idea", wildcard: "Wildcard" };

function Concepts({ concepts, shown, chosen }: { concepts: Concept[]; shown: Concept[]; chosen: Concept | null }) {
  if (!concepts.length) return null;
  const missing = concepts.length - shown.length;
  return (
    <section className="ov-sec" id="ov-concepts">
      <SecHead
        icon={<Sparkles size={18} />}
        title={`${concepts.length} concept${concepts.length === 1 ? "" : "s"} from one wish`}
        sub={
          <>
            Each concept is a complete design spec, checked and rendered from CAD before it is shown.{chosen ? ` “${chosen.title}” was chosen and built.` : ""}
            {missing > 0 ? ` ${missing} more ${missing === 1 ? "idea" : "ideas"} could not be rendered, so ${missing === 1 ? "it has" : "they have"} no picture.` : ""}
          </>
        }
      />
      <div className="ov-concepts">
        {shown.map((c) => (
          <figure key={c.id} className={`ov-concept${chosen?.id === c.id ? " chosen" : ""}`}>
            <img src={conceptImage(c)!} alt={c.title} loading="lazy" decoding="async" />
            <figcaption>
              <strong>{c.title}</strong>
              <span>{chosen?.id === c.id ? "Chosen" : TIER_WORDS[c.tier || ""] || "Concept"}</span>
            </figcaption>
          </figure>
        ))}
      </div>
    </section>
  );
}

/** Mounts the 3D viewer only when its section comes near the screen: the model file is several MB. */
function useNear(margin = "400px"): [React.RefObject<HTMLDivElement>, boolean] {
  const ref = useRef<HTMLDivElement>(null);
  const [near, setNear] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || near) return;
    if (typeof IntersectionObserver === "undefined") {
      setNear(true);
      return;
    }
    const io = new IntersectionObserver((es) => es.some((e) => e.isIntersecting) && setNear(true), { rootMargin: margin });
    io.observe(el);
    return () => io.disconnect();
  }, [near, margin]);
  return [ref, near];
}

function ModelSection({ ctx, v, b, title, onTab }: { ctx: Ctx; v: Version; b: Build | null; title: string; onTab: (t: TabId) => void }) {
  const idx = useFileIndex(ctx, v.n);
  const [ref, near] = useNear();
  const has = idx === undefined ? null : idx === null ? true : idx.has("cad/assembly.glb");
  const printed = b?.printed?.length || 0;
  const bought = b?.purchased?.length || 0;
  return (
    <section className="ov-sec" id="ov-model">
      <SecHead
        icon={<Package size={18} />}
        title="The design in 3D"
        sub={`The assembled CAD model from this build${printed || bought ? `: ${printed} printed parts and ${bought} bought parts` : ""}. Drag to turn it; pinch or scroll to zoom.`}
      >
        <button className="btn sm" type="button" onClick={() => onTab("preview")}>
          Explode and inspect <ArrowRight size={14} />
        </button>
      </SecHead>
      <div className="ov-viewer" ref={ref}>
        {has === false ? (
          <div className="empty">This version has no 3D model file.</div>
        ) : near && has ? (
          <Suspense
            fallback={
              <div className="empty">
                <Spinner lg />
              </div>
            }
          >
            <ShareViewer url={fileUrl(ctx, v.n, "cad/assembly.glb")} label={`3D model of ${title}`} />
          </Suspense>
        ) : (
          <div className="empty">
            <Spinner lg />
          </div>
        )}
      </div>
    </section>
  );
}

function Engineering({
  ctx,
  v,
  b,
  summary,
  fwTests,
  drc,
  erc,
  onTab,
}: {
  ctx: Ctx;
  v: Version;
  b: Build | null;
  summary: any;
  fwTests: { passed: number; total: number } | null;
  drc: number | null;
  erc: number | null;
  onTab: (t: TabId) => void;
}) {
  const files = useVersionFiles(ctx, v.n);
  const idx = useFileIndex(ctx, v.n);
  const groups = fileGroups(files || []);
  const total = (files || []).reduce((a, f) => a + (f.bytes || 0), 0);
  const pcb = b?.pcb as (Build["pcb"] & { vias?: number; track_length_mm?: number }) | null | undefined;
  const fw = b?.firmware;
  const tally = checkTally(summary?.checks);
  const boardImg = idx instanceof Set && idx.has("pcb/board-top.png") ? fileUrl(ctx, v.n, "pcb/board-top.png") : null;
  const manualImages: number | null = summary?.checks?.manual_images ?? null;
  const flashPct: number | null = fw?.size?.app_slot_used_percent ?? fw?.size?.flash?.percent ?? null;
  return (
    <section className="ov-sec" id="ov-engineering">
      <SecHead icon={<Wrench size={18} />} title="The engineering pack" sub="What the build pipeline made and checked for this design. Each card opens its full tab." />
      <div className="ov-cards">
        <button type="button" className="ov-card" onClick={() => onTab("pcb")}>
          <span className="ov-card-head">
            <CircuitBoard size={18} /> Circuit board
          </span>
          {boardImg ? <img className="ov-board" src={boardImg} alt="Top of the routed circuit board" loading="lazy" decoding="async" /> : null}
          <span className="ov-card-text">
            {pcb
              ? `${pcb.layers || 2}-layer ESP32-S3 backplane${pcb.size_mm?.length === 2 ? `, ${pcb.size_mm[0]} × ${pcb.size_mm[1]} mm` : ""}${pcb.routed ? ", fully routed" : ""}${pcb.vias ? ` (${pcb.vias} vias, ${Math.round(pcb.track_length_mm || 0).toLocaleString("en-IN")} mm of track)` : ""}. Made in KiCad, routed by Freerouting.`
              : "Backplane made in KiCad, with Gerbers."}
          </span>
          <span className="ov-badges">
            <Badge label="DRC" value={drc} />
            <Badge label="ERC" value={erc} />
          </span>
        </button>
        <button type="button" className="ov-card" onClick={() => onTab("code")}>
          <span className="ov-card-head">
            <CodeXml size={18} /> Firmware
          </span>
          {fwTests ? (
            <span className={`ov-big ${fwTests.passed === fwTests.total ? "ok" : "warn"}`}>
              {fwTests.passed}/{fwTests.total}
              <span>unit tests passed</span>
            </span>
          ) : null}
          <span className="ov-card-text">
            {fw
              ? `ESP32-S3 firmware (${fw.framework === "arduino" ? "Arduino" : fw.framework || "C++"} on PlatformIO), ${fw.compiled ? "compiled" : "not compiled"}${flashPct != null ? `, using ${Math.round(flashPct)}% of the app flash` : ""}.`
              : summary?.checks?.firmware_compiled
                ? "ESP32-S3 firmware, compiled."
                : "Firmware for the ESP32-S3."}
          </span>
        </button>
        <button type="button" className="ov-card" onClick={() => onTab("build")}>
          <span className="ov-card-head">
            <Wrench size={18} /> Build manual
          </span>
          {b?.steps?.length ? (
            <span className="ov-big">
              {b.steps.length}
              <span>illustrated steps</span>
            </span>
          ) : null}
          <span className="ov-card-text">{manualImages ? `${manualImages} pictures rendered from the CAD, with the parts to use at each step.` : "Step-by-step assembly with pictures from the CAD."}</span>
        </button>
        <button type="button" className="ov-card" onClick={() => onTab("parts")}>
          <span className="ov-card-head">
            <Package size={18} /> Parts
          </span>
          {b ? (
            <span className="ov-big">
              {(b.purchased?.length || 0) + (b.printed?.length || 0)}
              <span>parts</span>
            </span>
          ) : null}
          <span className="ov-card-text">{b ? `${b.purchased?.length || 0} bought parts and ${b.printed?.length || 0} printed parts.` : "Bought and printed parts."}</span>
        </button>
        {tally ? (
          <div className="ov-card static">
            <span className="ov-card-head">
              <ShieldCheck size={18} /> Digital checks
            </span>
            <span className={`ov-big ${tally.failed ? "warn" : "ok"}`}>
              {tally.passed}/{tally.total}
              <span>passed</span>
            </span>
            <span className="ov-card-text">
              {tally.failed ? `${tally.failed} failed. ` : "None failed. "}
              {tally.warned ? `${tally.warned} warning${tally.warned === 1 ? "" : "s"} to review. ` : ""}Physical checks need the real parts.
            </span>
          </div>
        ) : null}
      </div>

      <div className="ov-files card">
        <div className="ov-files-head">
          <FileArchive size={18} />
          <div className="grow">
            <h4>All files</h4>
            <p className="muted small">{files ? `${files.length} files, ${bytes(total)} before zipping` : files === undefined ? "Loading the file list…" : "The file list is not available."}</p>
          </div>
          <a className="btn primary" href={exportUrl(ctx, v.n)} download>
            <Download size={15} /> Download all files (.zip)
          </a>
        </div>
        {groups.length ? (
          <ul className="ov-groups">
            {groups.map((g) => (
              <li key={g.folder || "root"}>
                <span className="mono">{g.folder ? `${g.folder}/` : "/"}</span>
                <span className="grow">{g.label}</span>
                <span className="muted nowrap">
                  {g.count} file{g.count === 1 ? "" : "s"} · {bytes(g.bytes)}
                </span>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </section>
  );
}

function Badge({ label, value }: { label: string; value: number | null }) {
  if (value == null) return null;
  return (
    <span className={`pill ${value === 0 ? "success" : "warning"}`}>
      {label}: {value} error{value === 1 ? "" : "s"}
    </span>
  );
}

function Price({ quote, loading, priced, priceInr, onTab }: { quote: Quote | null; loading: boolean; priced: boolean; priceInr: number | null; onTab: (t: TabId) => void }) {
  // a held quote's reason can carry an engine dict such as ({'parts': [...], 'mm3': 14.8}); keep the words only
  const reason = quote && !priced ? plainText((quote.reasons || [])[0] || quote.status_label || statusWords(quote.status)).replace(/\s*\(\{.*?\}\)/g, "") : null;
  const lead = quote?.lead_time_days;
  return (
    <section className="ov-sec" id="ov-price">
      <SecHead icon={<Receipt size={18} />} title="What it costs" sub="The price engine prices each design from its own parts list, print time, assembly, courier, warranty and GST." />
      <div className="ov-price card">
        <div className="ov-price-main">
          <div className="ov-eyebrow">This design: live engine quote</div>
          {loading ? (
            <Spinner />
          ) : priced ? (
            <>
              <div className="ov-price-figure">{inr(priceInr)}</div>
              <p>
                First unit, delivered in India, including GST.{quote!.binding === false ? " An estimate: nothing is charged or reserved." : ""}
                {lead?.low && lead?.high ? ` Lead time ${lead.low} to ${lead.high} days (estimate).` : ""}
              </p>
            </>
          ) : (
            <p>{reason ? `No price yet: ${reason}` : "This design has no price yet."}</p>
          )}
          <button className="btn sm" type="button" onClick={() => onTab("order")}>
            See the cost breakdown <ArrowRight size={14} />
          </button>
        </div>
        <div className="ov-price-line">
          <strong>Wish Labs pricing</strong>
          <span>{PRICE_LINE}</span>
        </div>
      </div>
    </section>
  );
}

function PriceLineOnly() {
  return (
    <section className="ov-sec" id="ov-price">
      <div className="ov-price card">
        <div className="ov-price-line">
          <strong>Wish Labs pricing</strong>
          <span>{PRICE_LINE}</span>
        </div>
      </div>
    </section>
  );
}

function AiReceipts({ receipts }: { receipts: Receipts | null }) {
  return (
    <section className="ov-sec" id="ov-ai">
      <SecHead
        icon={<Sparkles size={18} />}
        title="AI receipts"
        sub="Which Google model did what in this project. Every call ran on Vertex AI; the counts come from the usage metadata Vertex AI returned with each call."
      />
      {receipts ? (
        <div className="ov-receipts card" role="table" aria-label="Google AI calls in this project">
          <div className="rc-row rc-head" role="row">
            <span role="columnheader">Job</span>
            <span role="columnheader">Google model</span>
            <span role="columnheader" className="num">
              Calls
            </span>
            <span role="columnheader" className="num">
              Tokens in
            </span>
            <span role="columnheader" className="num">
              Tokens out
            </span>
            <span role="columnheader" className="num">
              Est. cost
            </span>
          </div>
          {receipts.rows.map((r) => (
            <div className="rc-row" role="row" key={r.key}>
              <span className="rc-job" role="cell">
                <strong>{r.label}</strong>
                <span>{r.what}</span>
              </span>
              <span className="rc-model" role="cell">
                {r.modelName}
                <code>{r.model}</code>
              </span>
              <span className="num" role="cell" data-l="Calls">
                {r.calls}
                {r.images ? <em> · {r.images} image{r.images === 1 ? "" : "s"}</em> : null}
              </span>
              <span className="num" role="cell" data-l="Tokens in">
                {compact(r.inputTokens)}
              </span>
              <span className="num" role="cell" data-l="Tokens out" title={r.thinkingTokens ? `${r.thinkingTokens.toLocaleString("en-IN")} of them thinking tokens` : undefined}>
                {compact(r.outputTokens + r.thinkingTokens)}
              </span>
              <span className="num" role="cell" data-l="Est. cost" title={r.costUsd ? undefined : "No rate is configured for this model"}>
                {r.costUsd ? usd(r.costUsd) : "–"}
              </span>
            </div>
          ))}
          <div className="rc-row rc-total" role="row">
            <span className="rc-job" role="cell">
              <strong>Total</strong>
              <span>
                {receipts.calls} model call{receipts.calls === 1 ? "" : "s"}
                {receipts.turns ? ` over ${receipts.turns} agent turn${receipts.turns === 1 ? "" : "s"}` : ""}
              </span>
            </span>
            <span role="cell" className="rc-blank" />
            <span className="num" role="cell" data-l="Calls">
              {receipts.calls}
            </span>
            <span className="num" role="cell" data-l="Tokens in">
              {compact(receipts.inputTokens)}
            </span>
            <span className="num" role="cell" data-l="Tokens out">
              {compact(receipts.outputTokens)}
            </span>
            <span className="num" role="cell" data-l="Est. cost">
              {usd(receipts.costUsd)}
            </span>
          </div>
          <p className="muted small rc-note">Costs are estimates from per-token rates set in our server, not a bill. Tokens out include the model's thinking tokens.</p>
        </div>
      ) : (
        <div className="callout">No model usage was recorded for this project.</div>
      )}
    </section>
  );
}
