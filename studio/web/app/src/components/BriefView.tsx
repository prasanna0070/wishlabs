// Renders every brief section (API v1.1 §2.1) with its provenance badge ("From you" / "Suggested") and evidence.
// Section order: Summary, Purpose, Size, Function 1..n, Interaction, Privacy & safety, Power, Details, Parts, Limits.
import type { Brief, Provenance } from "../lib/types";
import { dims } from "../lib/format";

function isFact(x: any): boolean {
  return !!x && typeof x === "object" && !Array.isArray(x) && ("value" in x || "source" in x || "basis" in x);
}

export function factValue(x: any): any {
  return isFact(x) ? x.value : x;
}

function provOf(brief: Brief, key: string, raw: any): Provenance | null {
  const p = brief.provenance?.[key];
  if (p) return p;
  if (isFact(raw) && (raw.source || raw.basis)) return { basis: raw.source || raw.basis, evidence: raw.evidence };
  return null;
}

function Tag({ p }: { p: Provenance | null }) {
  if (!p) return null;
  const user = p.basis === "user";
  return (
    <span className="prov" title={p.evidence || undefined}>
      <span className={`prov-tag ${user ? "user" : "sugg"}`}>{user ? "From you" : "Suggested"}</span>
      {p.evidence ? <span className="prov-ev">{p.evidence}</span> : null}
    </span>
  );
}

export interface BriefSection {
  key: string;
  label: string;
  body: React.ReactNode;
  prov: Provenance | null;
}

export function briefSections(brief: Brief): BriefSection[] {
  const out: BriefSection[] = [];
  const text = (key: string, label: string, raw: any) => {
    const v = factValue(raw);
    if (v == null || v === "") return;
    out.push({ key, label, body: typeof v === "string" ? v : JSON.stringify(v), prov: provOf(brief, key, raw) });
  };
  text("summary", "Summary", brief.summary);
  text("purpose", "Purpose", brief.purpose);
  if (brief.size) {
    const s: any = brief.size;
    const body = (typeof s.value === "string" && s.value) || s.text || dims(s.w_mm, s.d_mm, s.h_mm);
    out.push({ key: "size", label: "Size", body, prov: provOf(brief, "size", s) });
  }
  (brief.functions || []).forEach((f: any, i) => {
    const v = typeof f === "string" ? f : f.value ?? f.text ?? "";
    out.push({
      key: `functions.${i}`,
      label: `Function ${i + 1}${f?.title ? `: ${f.title}` : ""}`,
      body: v,
      prov: provOf(brief, `functions.${i}`, f),
    });
  });
  text("interaction", "Interaction", brief.interaction);
  text("privacy_safety", "Privacy and safety", brief.privacy_safety);
  text("power", "Power", brief.power);
  (brief.details || []).forEach((d: any, i) => {
    out.push({ key: `details.${i}`, label: d.topic || `Detail ${i + 1}`, body: String(factValue(d) ?? ""), prov: provOf(brief, `details.${i}`, d) });
  });
  if (brief.parts?.length) {
    out.push({
      key: "parts",
      label: "Parts",
      body: (
        <ul className="brief-parts">
          {brief.parts.map((p: any, i) => (
            <li key={i}>
              {p.name}
              {p.qty && p.qty > 1 ? ` ×${p.qty}` : ""}
              {p.role ? <span className="muted"> · {p.role}</span> : null}
              {p.implied ? <span className="badge" style={{ marginLeft: 6 }}>added for you</span> : null}
            </li>
          ))}
        </ul>
      ),
      prov: brief.provenance?.parts || provOf(brief, "parts", brief.parts[0]) || { basis: "suggested", evidence: "From the parts catalog" },
    });
  }
  if (brief.limits?.length) {
    out.push({
      key: "limits",
      label: "Limits",
      body: (
        <ul className="brief-parts">
          {brief.limits.map((l, i) => (
            <li key={i}>{String(factValue(l))}</li>
          ))}
        </ul>
      ),
      prov: brief.provenance?.limits || provOf(brief, "limits", brief.limits[0]),
    });
  }
  return out;
}

export function BriefView({ brief, compact = false }: { brief: Brief; compact?: boolean }) {
  const secs = briefSections(brief);
  return (
    <div className={`brief${compact ? " compact" : ""}`}>
      <div className="brief-title">
        <strong>{brief.title}</strong>
        {brief.edited_by_user ? <span className="badge" style={{ marginLeft: 8 }}>edited by you</span> : null}
      </div>
      <dl>
        {secs.map((s) => (
          <div key={s.key} className="brief-sec">
            <dt>
              <span>{s.label}</span>
              <Tag p={s.prov} />
            </dt>
            <dd>{s.body}</dd>
          </div>
        ))}
      </dl>
      {brief.safety_flags?.length ? (
        <div className="row" style={{ flexWrap: "wrap", gap: 6, marginTop: 8 }}>
          {brief.safety_flags.map((f) => (
            <span key={f} className="pill warning">
              {f.replace(/_/g, " ").toLowerCase()}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
