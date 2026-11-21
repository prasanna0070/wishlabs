// Types for the gstudio-web API (docs/API.md) and the version output files (docs/ARCHITECTURE.md §5, §9).
// Optional fields marked "proposed" are listed in docs/REQUESTS.md; the UI degrades when they are absent.
import type { VoiceWish } from "./voice";

export type Stage = "clarify" | "concepts" | "brief" | "building" | "ready" | "error";
export type Mode = "pro" | "fast";
export type MsgKind = "text" | "choices" | "concepts" | "brief" | "progress" | "result" | "tweak" | "error";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  kind: MsgKind;
  text: string;
  payload: any;
  ts: string;
  draft?: boolean; // client-only: still streaming
  attachments?: Attachment[]; // images the user sent with this message (_notes/image-contract.md §3)
  attachments_hidden?: number; // share link without share_uploads: how many images this message had (Change 1)
}

// ---------------------------------------------------------------- start from an image (_notes/image-contract.md)

/** POST /api/projects/{pid}/uploads → 201. The server stores a normalised JPEG (long edge ≤ 2048 px) and a thumbnail. */
export interface Upload {
  id: string; // u_<10 lowercase alnum>
  name: string; // the original file name, sanitised
  mime: string; // always image/jpeg after normalising
  bytes: number;
  width: number;
  height: number;
  url: string; // /api/projects/{pid}/uploads/{id}
  thumb_url: string; // /api/projects/{pid}/uploads/{id}?thumb=1
}

/** An image on a stored user message. URLs are built from the id (lib/uploads.ts uploadUrl). */
export interface Attachment {
  id: string;
  name?: string;
  mime?: string;
  width?: number;
  height?: number;
}

export type ImageKind = "concept_sheet" | "sketch" | "product_photo" | "reference" | "other";
export type FeatureStatus = "buildable" | "closest" | "not_yet";

export interface ImageBriefFeature {
  text: string;
  status: FeatureStatus | string;
  catalog_parts?: string[];
  note?: string;
}

/** What the design agent read from the user's first images (tool record_image_brief), stored as project.image_brief
 * and sent live as the SSE event "image_brief". */
export interface ImageBrief {
  upload_ids?: string[];
  kind?: ImageKind | string;
  title?: string;
  purpose?: string;
  users?: string;
  form_factor?: string;
  features?: ImageBriefFeature[];
  text_seen?: string;
  out_of_scope?: string[];
  closest_buildable?: { title?: string; summary?: string } | null;
  created?: string;
}

export interface Question {
  id: string;
  question: string;
  consequence?: string;
  options: string[];
  allow_free_text?: boolean;
}

export interface Concept {
  id: string;
  title: string;
  blurb: string;
  spec?: any;
  render_url?: string | null;
  photo_url?: string | null;
  glb_url?: string | null;
  state?: "rendering" | "ready" | "failed" | string;
  warnings?: string[];
  photo_note?: string | null;
  tier?: string; // proposed: expected | different | wildcard
}

export interface Provenance {
  basis: "user" | "suggested" | string;
  evidence?: string;
}

/** A brief fact (API v1.1): {value, source, evidence}. Older briefs may hold plain strings. */
export type Fact<T = string> = { value: T; source?: "user" | "suggested" | string; evidence?: string; [k: string]: any } | T;

export interface Brief {
  version: number;
  concept_id?: string | null;
  title: string;
  summary?: Fact;
  purpose?: Fact;
  size?: { value?: string; w_mm?: number; d_mm?: number; h_mm?: number; text?: string; source?: string; evidence?: string } | null;
  functions?: { title?: string; value?: string; text?: string; source?: string; evidence?: string }[];
  interaction?: Fact;
  privacy_safety?: Fact;
  power?: Fact;
  details?: { topic: string; value: string; source?: string; evidence?: string }[];
  parts?: { module_id?: string; name: string; role?: string; qty?: number; implied?: boolean; source?: string; evidence?: string }[];
  limits?: Fact[];
  edited_by_user?: boolean;
  safety_flags?: string[];
  spec?: any;
  created?: string;
  approved?: boolean;
  approved_at?: string | null;
  provenance?: Record<string, Provenance>; // proposed
}

export interface Version {
  n: number;
  created: string;
  label: string;
  source?: "build" | "update" | "tweak" | "restore" | string;
  state: "queued" | "running" | "done" | "error" | string;
  summary?: string | null;
  spec_hash?: string;
  brief_version?: number | null;
  base_version?: number | null;
  percent?: number | null;
  error?: any;
  started?: string | null;
  ended?: string | null;
  restored_from?: number | null;
  finalized?: boolean;
  thumb?: string | null;
}

export interface StatusStage {
  id: string;
  label: string;
  state: "pending" | "running" | "done" | "error" | "skipped" | string;
  started?: string | null;
  ended?: string | null;
  message?: string;
  percent?: number | null;
}

export interface Status {
  state: "queued" | "running" | "done" | "error" | string;
  percent?: number | null;
  stages?: StatusStage[];
  error?: any;
  draft_url?: string | null; // live 3D draft GLB while the version builds
  messages?: { text: string; ts?: string; stage?: string; derived?: boolean }[]; // plain-English progress lines
  note?: string;
}

export interface OrderRequest {
  id: string;
  created: string;
  version: number;
  pincode: string;
  quantity: number;
  note?: string;
  status: string;
  status_text?: string;
  quote?: any;
}

export interface Project {
  schema?: string;
  id: string;
  name: string;
  name_source?: "auto" | "agent" | "user" | string;
  created: string;
  updated: string;
  stage: Stage;
  mode?: Mode;
  usage?: { basis?: string; turns?: any[]; totals?: any } | null;
  voice?: VoiceWish | null; // the voice chat (Gemini Live) the project started from
  turn?: { id: string; started: string } | null;
  questions_asked?: number;
  chat: ChatMessage[];
  concepts: Concept[];
  selected_concept?: string | null;
  brief?: Brief | null;
  brief_history?: Brief[];
  versions: Version[];
  current_version?: number | null;
  checklist?: { assembly?: Record<string, boolean>; wiring?: Record<string, boolean> };
  order_requests?: OrderRequest[];
  share?: { token: string; created: string } | null;
  read_only?: boolean;
  showcase?: boolean; // a magic-link guest viewing a showcase: read-only (server app/guest.py)
  image_brief?: ImageBrief | null; // what the agent read from the starting image(s)
  share_uploads?: boolean; // the owner lets share links show the uploaded images (missing = false)
  uploads?: (Upload & { source_mime?: string; created?: string })[]; // owner JSON: every upload, oldest first
}

export interface ProjectSummary {
  id: string;
  name: string;
  created: string;
  updated: string;
  stage: Stage;
  current_version?: number | null;
  thumb_url?: string | null;
  showcase?: boolean; // a guest's list: a showcase (read-only), listed first
  owner?: string; // a project a magic-link guest made
}

/** A magic-link guest's day (GET /api/session, /api/me): what is left of each daily limit. */
export interface GuestInfo {
  left: { projects: number; turns: number; builds: number; voice: number; sound: number };
  limits: { projects: number; turns: number; builds: number; voice: number; sound: number };
  day: string;
}

export interface FileEntry {
  path: string;
  bytes: number;
  sha256?: string;
}

export interface VersionDetail extends Version {
  status?: Status | null;
  files?: FileEntry[];
  urls?: Record<string, string>;
}

// ---------------------------------------------------------------- build.json (gadget-build/1)

export interface Camera {
  position: number[];
  target: number[];
}

export interface Visual {
  path: string;
  alt?: string;
  caption?: string;
}

export interface BuildStep {
  id: string;
  title: string;
  instruction?: string;
  text?: string;
  expected?: string;
  parts?: string[];
  highlight?: string[];
  labels?: string[];
  camera?: Camera | null;
  visuals?: Visual[];
}

export interface Purchased {
  name: string;
  quantity: number;
  packSize?: number;
  packsToBuy?: number;
  kind?: "module" | "cable" | "hardware" | string;
  module_id?: string | null;
  catalog_row?: string | null;
  refs?: string[];
  use?: string | null;
  role?: string | null;
  requiresSoldering?: boolean;
  url?: string | null;
  vendor?: string | null;
  sku?: string | null;
  price_inr_planning?: number[] | null;
  price_basis?: string | null;
  price_inr_seen?: number | null;
  seen_on?: string | null;
  estimate?: boolean;
  evidence_tier?: "seen" | "estimate" | "not_confirmed" | string;
  listing?: { url?: string; vendor?: string; sku?: string; packQuantity?: number; seen_on?: string; status?: string } | null;
  alternatives?: { vendor?: string; url?: string; sku?: string; price_inr?: number; seen_on?: string; pack?: number }[];
  notes?: string;
  modelPart?: string | null;
  uncertain?: any;
}

export interface Printed {
  name: string;
  file?: string | null;
  kind?: string;
  material?: string;
  color?: string;
  color_name?: string | null;
  grams?: number | null;
  hours?: number | null;
  print_time?: string | null;
  supports?: boolean | null;
  support_g?: number | null;
  size_mm?: number[] | null;
  orientation_note?: string;
}

export interface Test {
  title: string;
  expected?: string;
  kind?: "digital" | "physical" | string;
  status?: "passed" | "failed" | "not_run" | string;
  detail?: string;
}

export interface WiringConnection {
  from: string;
  to: string;
  kind?: string;
  via?: string;
  color?: string;
  note?: string;
  net?: string;
}

export interface Build {
  schema?: string;
  purchased?: Purchased[];
  printed?: Printed[];
  steps?: BuildStep[];
  wiring?: {
    runtime?: string;
    board?: string;
    code?: string | null;
    parts?: { id: string; name: string; kind?: string; item?: string; module_id?: string; sizeMm?: number[] }[];
    connections?: WiringConnection[];
    steps?: BuildStep[];
  } | null;
  tests?: Test[];
  firmware?: {
    framework?: string;
    board?: string;
    compiled?: boolean;
    size?: any;
    tests_passed?: number;
    tests_total?: number;
  } | null;
  pcb?: {
    layers?: number;
    size_mm?: number[] | null;
    drc_errors?: number | null;
    erc_errors?: number | null;
    routed?: boolean;
    unrouted?: number | null;
    files?: string[];
  } | null;
  order?: { list_price_inr?: number | null; status?: string; steady_state_price_inr?: number; lead_time_days?: number[] } | null;
  placeholder?: boolean;
}

export interface ComponentInfo {
  kind?: "PRINT" | "PURCHASE" | "REFERENCE" | string;
  label?: string;
  ref?: string;
  module_id?: string;
  feature_id?: string;
  color?: string | number[];
  bbox?: number[];
  volume_mm3?: number;
  /** REVIEW-1 MAJ-35: decor regions of the shell (ears, face plate...), each pickable as `<name>` in assembly.glb */
  decor?: { name?: string; spec_path?: string; label?: string; style?: string; type?: string }[];
}

export type Components = Record<string, ComponentInfo>;

export interface CableRoute {
  id?: string;
  from?: string;
  to?: string;
  cable?: string;
  diameter?: number;
  diameterEnvelopeMm?: number;
  length_mm?: number;
  color?: string;
  points: number[][];
}

// ---------------------------------------------------------------- order/quote.json (gadget-quote/1)

export interface QuoteScenario {
  name: string;
  note?: string;
  breakdown_mean: Record<string, number>;
  p5: number;
  p50: number;
  p95: number;
  list_price_incl_gst: number;
  status?: string;
  reasons?: string[];
  parts_basis?: string;
  margin_at_list_p50?: number;
}

export interface Quote {
  schema?: string;
  currency?: string;
  status: string;
  status_label?: string;
  binding?: boolean;
  reasons?: string[];
  complete?: boolean;
  missing_prices?: string[];
  delivered_price_inr?: {
    pilot_one_off?: number;
    steady_state?: number;
    includes_gst?: boolean;
    label?: string;
    includes?: string[];
    excludes?: string[];
  } | null;
  list_price_inr?: number | null;
  lead_time_days?: { low: number; high: number; reason?: string; steady_state?: { low: number; high: number } } | null;
  scenarios?: QuoteScenario[];
  inputs?: {
    print?: { grams?: number; hours?: number; parts?: Record<string, { filament_g?: number; print_h?: number; supports?: boolean; support_g?: number }> };
    parts?: any[];
  };
  limits?: string[];
}

export interface DigitalCheck {
  id?: string;
  stage?: string;
  title: string;
  status: "passed" | "failed" | "warning" | "not_run" | string;
  detail?: string;
}
