import { useState } from "react";
import { CheckCircle2, Clock, Cpu, Info, MapPin, Package, TriangleAlert, Truck } from "lucide-react";
import { useProject } from "../../store/project";
import { api, ApiError } from "../../lib/api";
import { useVersionFile } from "../../lib/hooks";
import { dateTime, excludesText, humanPart, inr, partsTotal, plainText, statusWords } from "../../lib/format";
import type { Build, Quote, QuoteScenario } from "../../lib/types";
import { Spinner } from "../ui";
import type { TabProps } from "./RightPanel";

const SCEN_LABELS = ["One-off", "Batch of 10", "Steady state", "Toy returns"];

// Fixed categorical order (dataviz reference palette, validated): identity follows the cost component.
const PARTS: { key: string; label: string; color: string }[] = [
  { key: "parts", label: "Parts", color: "#2a78d6" },
  { key: "print", label: "Print", color: "#eb6834" },
  { key: "assembly_test", label: "Assembly and test", color: "#1baf7a" },
  { key: "setup_nre", label: "Setup", color: "#eda100" },
  { key: "courier", label: "Courier", color: "#e87ba4" },
  { key: "warranty_reserve", label: "Warranty", color: "#008300" },
  { key: "returns", label: "Returns", color: "#6250d6" },
];

const STATUS_TEXT: Record<string, { tone: string; label: string }> = {
  ESTIMATE_REVIEW_REQUIRED: { tone: "warning", label: "Estimate · review required" },
  ESTIMATE: { tone: "warning", label: "Estimate" },
  ESTIMATE_INCOMPLETE: { tone: "danger", label: "Incomplete estimate" },
  UNSUPPORTED_AT_LAUNCH: { tone: "danger", label: "Not offered at launch" },
  NOT_QUOTED: { tone: "danger", label: "Not quoted" },
};

export default function OrderTab({ version }: TabProps) {
  const ctx = useProject((s) => s.ctx)!;
  const project = useProject((s) => s.project)!;
  const readOnly = useProject((s) => s.readOnly);
  const quote = useVersionFile<Quote>(ctx, version.n, "order/quote.json");
  const build = useVersionFile<Build>(ctx, version.n, "build.json");
  const [si, setSi] = useState(0);

  if (quote.loading)
    return (
      <div className="empty">
        <Spinner lg />
      </div>
    );
  const q = quote.data;
  if (!q) {
    const why = (build.data?.order as any)?.reason;
    return (
      <div className="tab-scroll">
        <div className="tab-inner">
          <div className="callout danger">
            <strong>No delivered price for this version.</strong> {why || "order/quote.json is missing."}
          </div>
        </div>
      </div>
    );
  }
  const scen = q.scenarios || [];
  const s: QuoteScenario | undefined = scen[si];
  const st = STATUS_TEXT[q.status] || { tone: "warning", label: statusWords(q.status) };
  const parts = partsTotal(q, build.data);
  const excludes = excludesText(q.delivered_price_inr?.excludes, q.missing_prices);
  const headline = q.delivered_price_inr?.pilot_one_off ?? q.list_price_inr ?? scen[0]?.list_price_incl_gst;
  const lt = q.lead_time_days;
  const printParts = Object.entries(q.inputs?.print?.parts || {});
  const myRequests = (project.order_requests || []).filter((r) => r.version === version.n);

  return (
    <div className="tab-scroll">
      <div className="tab-inner order">
        <div className="price-hero card">
          <div className="price-hero-main">
            <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
              <span className="eyebrow">
                <MapPin size={14} /> Delivered to your door in India
              </span>
              <span className={`pill ${st.tone}`}>
                <TriangleAlert size={13} /> {st.label}
              </span>
            </div>
            <div className="hero-figure">{inr(headline)}</div>
            <div className="hero-sub">
              One all-in price, <strong>incl. GST</strong>: parts, printing, assembly, flashing and test, courier, warranty. First unit of this design.
            </div>
            <div className="hero-facts">
              {q.delivered_price_inr?.steady_state ? (
                <span>
                  <Package size={15} /> {inr(q.delivered_price_inr.steady_state)} each at 100+ a month
                </span>
              ) : null}
              {lt ? (
                <span>
                  <Clock size={15} /> Arrives in {lt.low}–{lt.high} days
                </span>
              ) : null}
              {parts ? (
                <span title={parts.basis} data-testid="order-parts-total">
                  <Cpu size={15} /> Parts {inr(parts.inr)} of this
                </span>
              ) : null}
              <span>
                <Truck size={15} /> Courier within India
              </span>
            </div>
          </div>
          <div className="hero-note">
            <Info size={15} />
            <span>
              {plainText(q.status_label) || "Estimated total"}. {q.binding === false ? "Non-binding: nothing is charged or reserved." : ""}
            </span>
          </div>
        </div>

        <div className="card pad">
          <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
            <h3 className="grow" style={{ fontSize: 15 }}>
              Cost by scenario
            </h3>
            <div className="seg" role="tablist" aria-label="Scenario">
              {scen.map((x, i) => (
                <button key={x.name} role="tab" aria-selected={i === si} className={i === si ? "on" : ""} onClick={() => setSi(i)} title={x.name}>
                  {SCEN_LABELS[i] || x.name}
                </button>
              ))}
            </div>
          </div>
          {s ? (
            <>
              <div className="muted small" style={{ margin: "8px 0 14px" }}>
                {s.name}
                {s.note ? `: ${s.note}` : ""}
              </div>
              <div className="tiles3">
                <div className="stat">
                  <div className="stat-k">List price incl. GST</div>
                  <div className="stat-v">{inr(s.list_price_incl_gst)}</div>
                </div>
                <div className="stat">
                  <div className="stat-k">Landed cost, typical (p50)</div>
                  <div className="stat-v">{inr(s.p50)}</div>
                </div>
                <div className="stat">
                  <div className="stat-k">Landed cost, bad case (p95)</div>
                  <div className="stat-v">{inr(s.p95)}</div>
                </div>
              </div>
              <Breakdown b={s.breakdown_mean} />
            </>
          ) : null}
        </div>

        <div className="grid2">
          <div className="card pad">
            <h3 style={{ fontSize: 15, marginBottom: 8 }}>Why it is an estimate</h3>
            <ul className="reasons">
              {(q.reasons || []).map((r, i) => (
                <li key={i}>{plainText(r)}</li>
              ))}
            </ul>
            {q.missing_prices?.length && !q.delivered_price_inr?.includes?.length ? <div className="callout danger small">Parts without a price yet (not in the total): {q.missing_prices.join(", ")}</div> : null}
          </div>
          <div className="card pad">
            <h3 style={{ fontSize: 15, marginBottom: 8 }}>Printing</h3>
            <table className="table">
              <thead>
                <tr>
                  <th>Part</th>
                  <th className="num">Filament</th>
                  <th className="num">Time</th>
                  <th>Supports</th>
                </tr>
              </thead>
              <tbody>
                {printParts.map(([name, p]) => (
                  <tr key={name}>
                    <td>{humanPart(name)}</td>
                    <td className="num">{p.filament_g != null ? `${Math.round(p.filament_g)} g` : "—"}</td>
                    <td className="num">{p.print_h != null ? `${p.print_h.toFixed(1)} h` : "—"}</td>
                    <td>{p.supports ? "yes" : "no"}</td>
                  </tr>
                ))}
                <tr className="total-row">
                  <td>Total</td>
                  <td className="num">{q.inputs?.print?.grams != null ? `${Math.round(q.inputs.print.grams)} g` : ""}</td>
                  <td className="num">{q.inputs?.print?.hours != null ? `${q.inputs.print.hours.toFixed(1)} h` : ""}</td>
                  <td />
                </tr>
              </tbody>
            </table>
            <div className="muted small">From the slicer run on this version's STL files.</div>
          </div>
        </div>

        {q.delivered_price_inr?.includes?.length ? (
          <div className="grid2">
            <div className="card pad">
              <h3 style={{ fontSize: 15, marginBottom: 8 }}>Included</h3>
              <ul className="reasons ok">
                {q.delivered_price_inr.includes.map((x, i) => (
                  <li key={i}>{plainText(x)}</li>
                ))}
              </ul>
            </div>
            <div className="card pad">
              <h3 style={{ fontSize: 15, marginBottom: 8 }}>Not included</h3>
              <ul className="reasons">
                {excludes.map((x, i) => (
                  <li key={i}>{x}</li>
                ))}
              </ul>
              {lt?.reason ? <p className="muted small" style={{ marginBottom: 0 }}>{lt.reason}</p> : null}
            </div>
          </div>
        ) : null}

        {!readOnly ? <RequestForm versionN={version.n} /> : null}

        {myRequests.length ? (
          <div className="card pad">
            <h3 style={{ fontSize: 15, marginBottom: 8 }}>Your build requests for v{version.n}</h3>
            {myRequests.map((r) => (
              <div key={r.id} className="req-row">
                <CheckCircle2 size={16} className="ok" />
                <span className="grow">
                  {r.quantity} × to {r.pincode} · {dateTime(r.created)}
                  {r.note ? <span className="muted"> · {r.note}</span> : null}
                </span>
                <span className="badge">{r.status}</span>
              </div>
            ))}
          </div>
        ) : null}

        {q.limits?.length ? (
          <details className="fine">
            <summary>Limits of this price</summary>
            <ul>
              {q.limits.map((l, i) => (
                <li key={i}>{plainText(l)}</li>
              ))}
            </ul>
          </details>
        ) : null}
      </div>
    </div>
  );
}

function Breakdown({ b }: { b: Record<string, number> }) {
  const [hover, setHover] = useState<string | null>(null);
  const parts = PARTS.map((p) => ({ ...p, v: Math.max(0, b[p.key] || 0) }));
  const total = parts.reduce((a, p) => a + p.v, 0) || 1;
  const visible = parts.filter((p) => p.v > 0);
  const h = hover ? parts.find((p) => p.key === hover) : null;
  return (
    <div className="breakdown">
      <div className="row small muted" style={{ marginBottom: 6 }}>
        <span className="grow">Average cost per unit, before margin and GST</span>
        <span>{inr(total)}</span>
      </div>
      <div className="seg-bar" role="img" aria-label={`Cost breakdown: ${visible.map((p) => `${p.label} ${inr(p.v)} (${Math.round((p.v / total) * 100)}%)`).join(", ")}`}>
        {visible.map((p) => {
          const share = p.v / total;
          return (
            <div
              key={p.key}
              className="seg-part"
              tabIndex={0}
              style={{ flexGrow: p.v, background: p.color }}
              onMouseEnter={() => setHover(p.key)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(p.key)}
              onBlur={() => setHover(null)}
              aria-label={`${p.label}: ${inr(p.v)}, ${Math.round(share * 100)}%`}
            >
              {share >= 0.09 ? <span>{Math.round(share * 100)}%</span> : null}
            </div>
          );
        })}
      </div>
      <div className="seg-tip" aria-live="polite">
        {h ? (
          <>
            <span className="swatch" style={{ background: h.color }} /> {h.label}: <strong>{inr(h.v)}</strong> · {Math.round((h.v / total) * 100)}%
          </>
        ) : (
          <span className="muted">Hover a block for its amount</span>
        )}
      </div>
      <table className="table legend-table">
        <tbody>
          {parts.map((p) => (
            <tr key={p.key} className={hover === p.key ? "hl" : ""}>
              <td>
                <span className="swatch" style={{ background: p.color }} /> {p.label}
              </td>
              <td className="num">{inr(p.v)}</td>
              <td className="num muted">{Math.round((p.v / total) * 100)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RequestForm({ versionN }: { versionN: number }) {
  const ctx = useProject((s) => s.ctx)!;
  const refresh = useProject((s) => s.refresh);
  const [pin, setPin] = useState("");
  const [qty, setQty] = useState(1);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const pinOk = /^[1-9]\d{5}$/.test(pin);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!pinOk || busy) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await api.orderRequest(ctx.pid, { pincode: pin, quantity: qty, note: note.trim() || undefined, version: versionN });
      setDone(r?.request?.status_text || "Request recorded. It is non-binding: nothing is charged or reserved. We will confirm the final price and delivery date before anything is made.");
      void refresh();
    } catch (e: any) {
      setErr(e instanceof ApiError && e.errors.length ? e.errors.join("; ") : e?.message || "The request failed.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="card pad request" onSubmit={submit}>
      <h3 style={{ fontSize: 15 }}>Request a build</h3>
      <p className="muted small" style={{ margin: "4px 0 12px" }}>
        We build, flash, test and courier it to you. A request is non-binding: nothing is charged or reserved.
      </p>
      <div className="row" style={{ alignItems: "flex-end", flexWrap: "wrap", gap: 12 }}>
        <div className="field" style={{ width: 150 }}>
          <label className="label" htmlFor="pin">
            Delivery pincode
          </label>
          <input id="pin" className={`input${pin && !pinOk ? " invalid" : ""}`} inputMode="numeric" maxLength={6} placeholder="500032" value={pin} onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))} />
        </div>
        <div className="field" style={{ width: 110 }}>
          <label className="label" htmlFor="qty">
            Quantity
          </label>
          <input id="qty" className="input" type="number" min={1} max={20} value={qty} onChange={(e) => setQty(Math.max(1, Math.min(20, +e.target.value || 1)))} />
        </div>
        <div className="field grow">
          <label className="label" htmlFor="note">
            Note (optional)
          </label>
          <input id="note" className="input" maxLength={1000} placeholder="Gift wrap, a deadline, a colour change" value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
        <button className="btn primary lg" disabled={!pinOk || busy}>
          {busy ? <Spinner /> : null} Request build
        </button>
      </div>
      {pin && !pinOk ? <div className="small" style={{ color: "var(--danger)", marginTop: 6 }}>A pincode is 6 digits and does not start with 0.</div> : null}
      {err ? <div className="callout danger small" style={{ marginTop: 10 }}>{err}</div> : null}
      {done ? (
        <div className="callout success small" style={{ marginTop: 10 }}>
          <CheckCircle2 size={14} /> {done}
        </div>
      ) : null}
    </form>
  );
}
