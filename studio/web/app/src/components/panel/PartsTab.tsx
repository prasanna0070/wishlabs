import { useMemo } from "react";
import { Box, Cable, Cpu, Download, ExternalLink, FileCode2, Info, Package, Printer, ShoppingCart, Wrench, Flame, Layers } from "lucide-react";
import { useProject } from "../../store/project";
import { useFileIndex, useVersionFile } from "../../lib/hooks";
import { exportUrl, fileUrl } from "../../lib/urls";
import { dims, humanPart, inr, inrRange } from "../../lib/format";
import type { Build, Components, Purchased, Printed } from "../../lib/types";
import { Dropdown, Spinner } from "../ui";
import { ChecksCard } from "./Checks";
import type { TabProps } from "./RightPanel";

export default function PartsTab({ version, onTab }: TabProps) {
  const ctx = useProject((s) => s.ctx)!;
  const build = useVersionFile<Build>(ctx, version.n, "build.json");
  const comps = useVersionFile<Components>(ctx, version.n, "cad/components.json");
  const idx = useFileIndex(ctx, version.n);
  const has = (p: string) => (idx instanceof Set ? idx.has(p) : true);
  const purchased = build.data?.purchased || [];
  const printed = (build.data?.printed || []).filter((p) => p.kind !== "test_coupon" || true);
  const groups = useMemo(() => {
    const elec = purchased.filter((p) => p.kind !== "hardware");
    const hw = purchased.filter((p) => p.kind === "hardware");
    return { elec, hw };
  }, [purchased]);
  const refs = Object.entries(comps.data || {}).filter(([n, c]) => (c.kind || n.split("_")[0]).toUpperCase() === "REFERENCE");

  if (build.loading)
    return (
      <div className="empty">
        <Spinner lg />
      </div>
    );
  if (!build.data) return <div className="empty">This version has no parts list (build.json is missing).</div>;

  const tot = (rows: Purchased[]) =>
    rows.reduce(
      (a, r) => {
        const q = r.quantity || 1;
        if (r.price_inr_planning?.length === 2) return [a[0] + r.price_inr_planning[0] * q, a[1] + r.price_inr_planning[1] * q, a[2]];
        return [a[0], a[1], a[2] + 1];
      },
      [0, 0, 0],
    );
  const all = tot(purchased);
  const unconfirmed = purchased.filter((p) => p.evidence_tier === "not_confirmed" || (!p.price_inr_planning && p.price_inr_seen == null));
  const grams = printed.reduce((a, p) => a + (p.grams || 0), 0);
  const hours = printed.reduce((a, p) => a + (p.hours || 0), 0);
  const stls = printed.filter((p) => p.file && has(p.file));
  const cadFiles = ["cad/gadget_cad.py", "cad/params.json", "cad/assembly.step", "cad/assembly.glb"].filter(has);

  return (
    <div className="tab-scroll">
      <div className="tab-inner">
        <ChecksCard version={version} />

        <div className="sec-head" style={{ marginTop: 24 }}>
          <h2>Parts</h2>
          <div className="grow" />
          <button className="btn primary" onClick={() => onTab("order")}>
            <ShoppingCart size={15} /> See delivered price
          </button>
        </div>
        <div className="row" style={{ flexWrap: "wrap", marginBottom: 14 }}>
          <span className="muted small" style={{ marginRight: 4 }}>
            Download
          </span>
          <Dropdown
            align="left"
            width={300}
            trigger={({ toggle }) => (
              <button className="btn sm" onClick={toggle} disabled={!stls.length}>
                <Box size={14} /> Print files
              </button>
            )}
          >
            {stls.map((p) => (
              <a key={p.name} className="menu-item" href={fileUrl(ctx, version.n, p.file!, true)}>
                <Printer size={14} /> <span className="grow">{p.file!.split("/").pop()}</span>
              </a>
            ))}
          </Dropdown>
          <Dropdown
            align="left"
            width={300}
            trigger={({ toggle }) => (
              <button className="btn sm" onClick={toggle} disabled={!cadFiles.length}>
                <FileCode2 size={14} /> CAD source
              </button>
            )}
          >
            {cadFiles.map((f) => (
              <a key={f} className="menu-item" href={fileUrl(ctx, version.n, f, true)}>
                <FileCode2 size={14} /> <span className="grow">{f}</span>
              </a>
            ))}
          </Dropdown>
          <a className="btn sm" href={exportUrl(ctx, version.n)} download>
            <Download size={14} /> Complete project
          </a>
        </div>

        {unconfirmed.length ? (
          <div className="callout" style={{ marginBottom: 16 }}>
            {unconfirmed.length} item{unconfirmed.length > 1 ? "s have" : " has"} no confirmed price. The parts total is a planning estimate; this buy list is not a quote.
          </div>
        ) : null}

        <PurchasedGroup title="Electronics" icon={<Cpu size={16} />} rows={groups.elec} />
        <PrintedGroup rows={printed} comps={comps.data} ctxN={version.n} />
        {groups.hw.length ? <PurchasedGroup title="Hardware and consumables" icon={<Wrench size={16} />} rows={groups.hw} /> : null}

        {refs.length ? (
          <div className="group">
            <div className="group-head">
              <Layers size={16} /> <strong>Reference geometry</strong>
              <span className="muted small">Shown in 3D for routing and clearance; nothing to buy.</span>
            </div>
            <table className="table">
              <tbody>
                {refs.map(([n, c]) => (
                  <tr key={n}>
                    <td>{c.label || humanPart(n)}</td>
                    <td className="muted">Reference</td>
                    <td className="num muted">{c.bbox?.length === 6 ? dims(c.bbox[1] - c.bbox[0], c.bbox[3] - c.bbox[2], c.bbox[5] - c.bbox[4]) : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}

        <div className="card totals">
          <div>
            <div className="muted small">Purchased parts, planning range</div>
            <div className="big">{inrRange(all[0], all[1])}</div>
            {all[2] ? <div className="muted small">{all[2]} item(s) without a price are not included</div> : null}
          </div>
          <div>
            <div className="muted small">Printed parts</div>
            <div className="big">
              {grams ? `${Math.round(grams)} g` : "—"} · {hours ? `${hours.toFixed(1)} h` : "—"}
            </div>
            <div className="muted small">Slicer estimate, generic 0.4 mm FDM profile</div>
          </div>
          <div>
            <div className="muted small">Delivered in India</div>
            <button className="linkish big" onClick={() => onTab("order")}>
              {build.data.order?.list_price_inr ? inr(build.data.order.list_price_inr) : "See Order"} →
            </button>
            <div className="muted small">All-in: parts, print, assembly, courier, GST</div>
          </div>
        </div>
      </div>
    </div>
  );
}

function tierBadge(p: Purchased) {
  if (p.evidence_tier === "seen" && p.price_inr_seen != null) return null;
  if (p.evidence_tier === "not_confirmed") return <span className="badge danger">not confirmed</span>;
  return <span className="badge warning">estimate</span>;
}

function PurchasedGroup({ title, icon, rows }: { title: string; icon: React.ReactNode; rows: Purchased[] }) {
  const lo = rows.reduce((a, r) => a + (r.price_inr_planning?.[0] || 0) * (r.quantity || 1), 0);
  const hi = rows.reduce((a, r) => a + (r.price_inr_planning?.[1] || 0) * (r.quantity || 1), 0);
  return (
    <div className="group">
      <div className="group-head">
        {icon} <strong>{title}</strong>
        <span className="muted small">{rows.length} items</span>
        <div className="grow" />
        <span className="small">
          <strong>{inrRange(lo, hi)}</strong> <span className="muted">planning total</span>
        </span>
      </div>
      <table className="table parts-table">
        <thead>
          <tr>
            <th>Part</th>
            <th>Used for</th>
            <th className="num">Qty</th>
            <th className="num">Planning price</th>
            <th className="num">Price seen</th>
            <th>Vendor</th>
            <th aria-label="Evidence" />
          </tr>
        </thead>
        <tbody>
          {rows.map((p, i) => (
            <tr key={`${p.name}-${i}`}>
              <td>
                <div className="pname">
                  {p.kind === "cable" ? <Cable size={14} className="muted" /> : <Package size={14} className="muted" />}
                  {p.name}
                </div>
                <div className="row" style={{ gap: 6, marginTop: 3, flexWrap: "wrap" }}>
                  {p.sku ? <span className="muted small mono">{p.sku}</span> : null}
                  {p.requiresSoldering ? (
                    <span className="badge warning" title="This part needs soldering">
                      <Flame size={11} /> requires soldering
                    </span>
                  ) : null}
                </div>
              </td>
              <td className="muted">{(p.role || p.use || (p.refs || []).join(", ") || "—").replace(/_/g, " ")}</td>
              <td className="num">
                ×{p.quantity}
                {p.packSize && p.packSize > 1 ? <div className="muted small">pack of {p.packSize}</div> : null}
              </td>
              <td className="num">
                {p.price_inr_planning ? inrRange(p.price_inr_planning[0], p.price_inr_planning[1]) : "—"}
                {p.price_inr_planning && (p.quantity || 1) > 1 ? <div className="muted small">each</div> : null}
              </td>
              <td className="num">
                {p.price_inr_seen != null ? (
                  <>
                    {inr(p.price_inr_seen)}
                    {p.seen_on ? <div className="muted small">{p.seen_on}</div> : null}
                  </>
                ) : (
                  tierBadge(p)
                )}
              </td>
              <td>
                {p.url ? (
                  <a href={p.url} target="_blank" rel="noreferrer" className="row" style={{ gap: 4 }}>
                    {p.vendor || hostOf(p.url)} <ExternalLink size={12} />
                  </a>
                ) : (
                  <span className="muted">{p.vendor || "—"}</span>
                )}
              </td>
              <td>
                <Evidence p={p} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function hostOf(u: string) {
  try {
    return new URL(u).hostname.replace(/^www\./, "");
  } catch {
    return "link";
  }
}

function Evidence({ p }: { p: Purchased }) {
  return (
    <Dropdown
      align="right"
      width={340}
      trigger={({ toggle, open }) => (
        <button className={`icon-btn sm${p.uncertain || p.evidence_tier !== "seen" ? " warn" : ""}`} aria-label={`Evidence for ${p.name}`} aria-expanded={open} onClick={toggle}>
          <Info size={15} />
        </button>
      )}
    >
      <div className="evidence">
        <div className="row">
          <strong className="grow">{p.name}</strong>
          {tierBadge(p) || <span className="badge success">price seen</span>}
        </div>
        {p.notes ? <p>{p.notes}</p> : null}
        <dl>
          {p.price_basis ? (
            <>
              <dt>Price basis</dt>
              <dd>{p.price_basis}</dd>
            </>
          ) : null}
          {p.listing?.status ? (
            <>
              <dt>Listing</dt>
              <dd>
                {p.listing.status}
                {p.listing.seen_on ? `, seen ${p.listing.seen_on}` : ""}
              </dd>
            </>
          ) : null}
          {p.catalog_row ? (
            <>
              <dt>Catalog row</dt>
              <dd>{p.catalog_row}</dd>
            </>
          ) : null}
          {p.module_id ? (
            <>
              <dt>Library id</dt>
              <dd className="mono small">{p.module_id}</dd>
            </>
          ) : null}
          {p.uncertain ? (
            <>
              <dt>Uncertain</dt>
              <dd>{typeof p.uncertain === "string" ? p.uncertain : JSON.stringify(p.uncertain)}</dd>
            </>
          ) : null}
        </dl>
        {p.alternatives?.length ? (
          <>
            <div className="muted small" style={{ marginTop: 8 }}>
              Alternatives
            </div>
            <ul>
              {p.alternatives.map((a, i) => (
                <li key={i}>
                  {a.url ? (
                    <a href={a.url} target="_blank" rel="noreferrer">
                      {a.vendor || hostOf(a.url)}
                    </a>
                  ) : (
                    a.vendor
                  )}
                  {a.price_inr != null ? ` · ${inr(a.price_inr)}` : ""}
                  {a.seen_on ? ` · ${a.seen_on}` : ""}
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </div>
    </Dropdown>
  );
}

function PrintedGroup({ rows, comps, ctxN }: { rows: Printed[]; comps: Components | null; ctxN: number }) {
  const ctx = useProject((s) => s.ctx)!;
  if (!rows.length) return null;
  return (
    <div className="group">
      <div className="group-head">
        <Printer size={16} /> <strong>Printed parts</strong>
        <span className="muted small">{rows.length} files</span>
      </div>
      <table className="table">
        <thead>
          <tr>
            <th>Part</th>
            <th>Material</th>
            <th className="num">Size</th>
            <th className="num">Filament</th>
            <th className="num">Print time</th>
            <th>Supports</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => {
            const c = comps?.[p.name];
            const s = p.size_mm || (c?.bbox?.length === 6 ? [c.bbox[1] - c.bbox[0], c.bbox[3] - c.bbox[2], c.bbox[5] - c.bbox[4]] : null);
            return (
              <tr key={p.name}>
                <td>
                  <div className="pname">
                    <span className="iso" style={{ background: p.color || "#ccc" }} />
                    {c?.label || humanPart(p.name)}
                    {p.kind === "test_coupon" ? <span className="badge">fit test</span> : null}
                  </div>
                  {p.orientation_note ? <div className="muted small" style={{ marginTop: 3 }}>{p.orientation_note}</div> : null}
                </td>
                <td>
                  {p.material || "—"}
                  {p.color_name ? <div className="muted small">{p.color_name}</div> : null}
                </td>
                <td className="num">{s ? dims(s[0], s[1], s[2]) : "—"}</td>
                <td className="num">{p.grams != null ? `${Math.round(p.grams)} g` : "—"}</td>
                <td className="num">{p.hours != null ? `${p.hours.toFixed(1)} h` : p.print_time || "—"}</td>
                <td>{p.supports == null ? "—" : p.supports ? `yes${p.support_g ? `, ${Math.round(p.support_g)} g` : ""}` : "no"}</td>
                <td>
                  {p.file ? (
                    <a className="icon-btn sm" href={fileUrl(ctx, ctxN, p.file, true)} aria-label={`Download ${p.name} STL`} title="Download STL">
                      <Download size={15} />
                    </a>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
