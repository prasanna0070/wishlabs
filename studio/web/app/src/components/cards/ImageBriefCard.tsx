// "What I read from your image": the design agent's reading of the user's starting image (project.image_brief, the
// record_image_brief tool, _notes/image-contract.md §5-6). Shown in the chat under the image and on share links,
// where the Overview tab mounts it through import.meta.glob by file name (panel/cards.ts contract). Returns null when
// there is no brief. The image itself shows on a share link only when the owner turned share_uploads on.
import { useId } from "react";
import { Ban, CircleCheck, CircleDot, ScanEye, Sparkles } from "lucide-react";
import type { Project, Version } from "../../lib/types";
import type { Ctx } from "../../lib/urls";
import { countLine, hasImageBrief, kindLabel, sortedFeatures, statusInfo } from "../../lib/imageBrief";
import { imagesShown, projectImages } from "../../lib/uploads";
import { MessageImages } from "../chat/ImageAttachments";
import "./imageBrief.css";

export { hasImageBrief };

export interface ImageBriefCardProps {
  project: Project;
  version?: Version | null;
  ctx?: Ctx | null;
  readOnly?: boolean;
  compact?: boolean;
}

const STATUS_ICON = { buildable: CircleCheck, closest: CircleDot, not_yet: Ban, other: CircleDot };

export function ImageBriefCard({ project, ctx, readOnly = false, compact = false }: ImageBriefCardProps) {
  const headId = useId(); // unique per mount: the card can be on the page twice (chat and share Overview)
  if (!hasImageBrief(project)) return null;
  const b = project.image_brief!;
  const view: Ctx = ctx ?? (readOnly && project.share?.token ? { pid: project.id, share: project.share.token } : { pid: project.id });
  const all = projectImages(project);
  const wanted = (b.upload_ids || []).filter((x) => typeof x === "string" && x);
  const read = wanted.length ? wanted.map((id) => all.find((a) => a.id === id) || { id }) : all.slice(0, 1);
  // read-only without a share token: never point at the owner's /api/projects URLs
  const shown = readOnly && !view.share ? "no" : imagesShown(view, project);
  const features = sortedFeatures(b.features);
  const counts = countLine(b.features);
  const many = read.length > 1;
  const facts = [
    { k: "Purpose", v: b.purpose },
    { k: "For", v: b.users },
    { k: "Form", v: b.form_factor },
  ].filter((f) => typeof f.v === "string" && f.v.trim());
  const out = (b.out_of_scope || []).filter((x) => typeof x === "string" && x.trim());
  const closest = b.closest_buildable && (b.closest_buildable.title || b.closest_buildable.summary) ? b.closest_buildable : null;
  const seen = typeof b.text_seen === "string" ? b.text_seen.trim().slice(0, 600) : "";

  return (
    <section className={`ib-card${compact ? " compact" : ""}`} aria-labelledby={headId}>
      <header className="ib-head">
        <span className="ib-icon" aria-hidden="true">
          <ScanEye size={16} />
        </span>
        <div className="grow">
          <h3 id={headId}>{readOnly ? "What the assistant read from the starting image" : "What I read from your image"}{many ? "s" : ""}</h3>
          <div className="muted small">
            <span className="ib-kind">{kindLabel(b.kind)}</span>
            {counts ? <span> · {counts}</span> : null}
          </div>
        </div>
      </header>

      <div className={`ib-top${read.length && shown !== "no" ? " with-img" : ""}`}>
        <div className="ib-what">
          {b.title ? <p className="ib-title">{b.title}</p> : null}
          {facts.length ? (
            <dl className="ib-facts">
              {facts.map((f) => (
                <div key={f.k}>
                  <dt>{f.k}</dt>
                  <dd>{f.v}</dd>
                </div>
              ))}
            </dl>
          ) : null}
        </div>
        {read.length > 0 ? (
          <div className="ib-imgs">
            <MessageImages atts={read} ctx={view} shown={shown} />
          </div>
        ) : null}
      </div>

      {features.length ? (
        <div className="ib-sec">
          <h4>What it does, against our parts catalog</h4>
          <ul className="ib-features">
            {features.map((f, i) => {
              const s = statusInfo(f.status);
              const Icon = STATUS_ICON[s.key];
              const parts = (f.catalog_parts || []).filter((p) => typeof p === "string" && p.trim());
              return (
                <li key={i} className={`ib-feature ${s.key}`}>
                  <span className={`ib-chip ${s.key}`} title={s.hint || undefined}>
                    <Icon size={12} aria-hidden="true" /> {s.label}
                  </span>
                  <div className="ib-ftext">
                    <span className="ib-fmain">{f.text}</span>
                    {f.note ? <span className="ib-note">{f.note}</span> : null}
                    {parts.length && compact ? (
                      // in the chat a plain line wraps tighter than chips (long catalog ids on a phone)
                      <span className="ib-parts-line" aria-label="Catalog parts">
                        {parts.join(" · ")}
                      </span>
                    ) : parts.length ? (
                      <span className="ib-parts" aria-label="Catalog parts">
                        {parts.map((p) => (
                          <code key={p}>{p}</code>
                        ))}
                      </span>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}

      {out.length ? (
        <div className="ib-sec ib-out">
          <h4>Outside what Wish Labs can build today</h4>
          <ul>
            {out.map((x, i) => (
              <li key={i}>{x}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {closest ? (
        <div className="ib-closest">
          <div className="ib-closest-label">
            <Sparkles size={13} aria-hidden="true" /> The closest gadget we can build
          </div>
          {closest.title ? <strong>{closest.title}</strong> : null}
          {closest.summary ? <p>{closest.summary}</p> : null}
        </div>
      ) : null}

      {seen ? (
        <details className="ib-seen">
          <summary>Text seen in the image</summary>
          <blockquote>{seen}</blockquote>
        </details>
      ) : null}
    </section>
  );
}

export default ImageBriefCard;
