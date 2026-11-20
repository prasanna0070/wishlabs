// "What I read from your image": the SSE event's two shapes, the labels, where the card sits in the chat, its states,
// and the share gate (no owner URL ever on a share link; the image itself only with share_uploads on).
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ImageBriefCard } from "./ImageBriefCard";
import * as cardModule from "./ImageBriefCard";
import { cardWanted, pickCard } from "../panel/cards";
import { MessageImages } from "../chat/ImageAttachments";
import { briefFromEvent, countLine, featureCounts, hasImageBrief, imageBriefAnchor, imageStart, kindLabel, sortedFeatures, statusInfo } from "../../lib/imageBrief";
import type { ImageBrief, Project } from "../../lib/types";

const brief: ImageBrief = {
  upload_ids: ["u_sheet00001"],
  kind: "concept_sheet",
  title: "Bike helmet with a turn-signal visor",
  purpose: "Show turns and speed while cycling, with voice alerts",
  users: "Cyclists commuting in a city",
  form_factor: "Helmet, about 300 g",
  features: [
    { text: "Crash detection in the shell", status: "not_yet", catalog_parts: [], note: "No crash sensor in the catalog today" },
    { text: "Voice alerts", status: "buildable", catalog_parts: ["MIC_INMP441", "SPK_28MM"], note: "" },
    { text: "Visor display", status: "closest", catalog_parts: ["OLED_096_I2C"], note: "A small OLED on a desk stand, not in the visor" },
    { text: "Phone app over BLE", status: "buildable", catalog_parts: ["ESP32_S3"] },
  ],
  text_seen: "ACME helmet sketch v2 — visor: turns, speed",
  out_of_scope: ["A battery-powered wearable: we build USB-powered desk gadgets"],
  closest_buildable: { title: "Ride signal desk display", summary: "A USB-powered desk display that shows your last ride and alerts you by voice." },
  created: "2026-11-20T10:00:00Z",
};

const project = (o: Partial<Project> = {}): Project =>
  ({
    id: "p_1",
    name: "Acme",
    created: "2026-11-20T10:00:00Z",
    updated: "2026-11-20T10:00:00Z",
    stage: "clarify",
    chat: [
      { id: "m_1", role: "user", kind: "text", text: "Here is what I have in mind.", payload: null, ts: "", attachments: [{ id: "u_sheet00001", name: "acme-concept.png", mime: "image/jpeg", width: 1200, height: 1600 }] },
      { id: "m_2", role: "assistant", kind: "text", text: "Here is what I read.", payload: null, ts: "" },
    ],
    concepts: [],
    versions: [],
    image_brief: brief,
    ...o,
  }) as Project;

describe("image brief helpers", () => {
  it("reads the SSE event flat or wrapped, and ignores anything else", () => {
    expect(briefFromEvent({ seq: 4, ...brief })).toEqual(brief);
    expect(briefFromEvent({ seq: 4, image_brief: brief })).toEqual(brief);
    expect(briefFromEvent({ seq: 4 })).toBeNull();
    expect(briefFromEvent(null)).toBeNull();
  });

  it("labels kinds and statuses, orders features and counts them", () => {
    expect(kindLabel("concept_sheet")).toBe("Concept sheet");
    expect(kindLabel("product_photo")).toBe("Product photo");
    expect(kindLabel("mood_board")).toBe("Mood board");
    expect(kindLabel(undefined)).toBe("Image");
    expect(statusInfo("buildable").label).toBe("Buildable");
    expect(statusInfo("closest").label).toBe("Closest");
    expect(statusInfo("not_yet").label).toBe("Not yet");
    expect(statusInfo("Not yet").key).toBe("not_yet");
    expect(statusInfo("maybe").key).toBe("other");
    expect(sortedFeatures(brief.features).map((f) => f.status)).toEqual(["buildable", "buildable", "closest", "not_yet"]);
    expect(featureCounts(brief.features)).toEqual({ buildable: 2, closest: 1, not_yet: 1 });
    expect(countLine(brief.features)).toBe("2 buildable · 1 closest · 1 not yet");
  });

  it("knows when there is a brief worth showing", () => {
    expect(hasImageBrief(project())).toBe(true);
    expect(hasImageBrief(project({ image_brief: null }))).toBe(false);
    expect(hasImageBrief(project({ image_brief: { kind: "sketch" } }))).toBe(false); // nothing to say yet
  });

  it("tells an image start (with or without words) from a typed one", () => {
    expect(imageStart(project())).toBe("image-only"); // stored with the stand-in words
    const typed = project({ chat: [{ id: "m_1", role: "user", kind: "text", text: "A lamp", payload: null, ts: "" }] });
    expect(imageStart(typed)).toBeNull();
    const both = project({ chat: [{ id: "m_1", role: "user", kind: "text", text: "Like this, but blue", payload: null, ts: "", attachments: [{ id: "u_1" }] }] });
    expect(imageStart(both)).toBe("image");
    const hidden = project({ chat: [{ id: "m_1", role: "user", kind: "text", text: "Here is what I have in mind.", payload: null, ts: "", attachments_hidden: 1 }] });
    expect(imageStart(hidden)).toBe("image-only");
  });

  it("sits under the message that holds the image it read", () => {
    expect(imageBriefAnchor(project())).toBe("m_1");
    const later = project({
      chat: [
        { id: "m_1", role: "user", kind: "text", text: "A lamp", payload: null, ts: "" },
        { id: "m_3", role: "user", kind: "text", text: "x", payload: null, ts: "", attachments: [{ id: "u_other" }] },
        { id: "m_4", role: "user", kind: "text", text: "y", payload: null, ts: "", attachments: [{ id: "u_sheet00001" }] },
      ],
    });
    expect(imageBriefAnchor(later)).toBe("m_4");
    expect(imageBriefAnchor(project({ chat: [] }))).toBeNull();
    // a share link without share_uploads: only the count is left on the message
    const hidden = project({ chat: [{ id: "m_7", role: "user", kind: "text", text: "Here is what I have in mind.", payload: null, ts: "", attachments_hidden: 1 }] });
    expect(imageBriefAnchor(hidden)).toBe("m_7");
  });
});

describe("ImageBriefCard", () => {
  it("renders nothing without a brief", () => {
    expect(renderToStaticMarkup(<ImageBriefCard project={project({ image_brief: null })} ctx={{ pid: "p_1" }} />)).toBe("");
  });

  it("shows what it read: title, kind, facts, status chips with catalog parts, out of scope, the closest gadget", () => {
    const html = renderToStaticMarkup(<ImageBriefCard project={project()} ctx={{ pid: "p_1" }} />);
    expect(html).toContain("What I read from your image</h3>");
    expect(html).toContain("Concept sheet");
    expect(html).toContain("2 buildable · 1 closest · 1 not yet");
    expect(html).toContain("Bike helmet with a turn-signal visor");
    expect(html).toMatch(/<dt>Purpose<\/dt><dd>Show turns and speed/);
    expect(html).toMatch(/<dt>For<\/dt><dd>Cyclists commuting/);
    for (const chip of ["Buildable", "Closest", "Not yet"]) expect(html).toContain(`</svg> ${chip}</span>`);
    expect(html).toContain("<code>OLED_096_I2C</code>");
    expect(html).toContain("No crash sensor in the catalog today");
    expect(html).toContain("A battery-powered wearable: we build USB-powered desk gadgets");
    expect(html).toContain("The closest gadget we can build");
    expect(html).toContain("<strong>Ride signal desk display</strong>");
    // buildable first, not yet last
    expect(html.indexOf("Voice alerts")).toBeLessThan(html.indexOf("Visor display"));
    expect(html.indexOf("Visor display")).toBeLessThan(html.indexOf("Crash detection in the shell"));
  });

  it("keeps the text seen in the image collapsed", () => {
    const html = renderToStaticMarkup(<ImageBriefCard project={project()} ctx={{ pid: "p_1" }} />);
    expect(html).toMatch(/<details class="ib-seen"><summary>Text seen in the image<\/summary><blockquote>ACME helmet sketch v2/);
    expect(html).not.toMatch(/<details class="ib-seen" open/);
  });

  it("shows the owner the image it read, by its owner URL; in the chat the parts are one plain line", () => {
    const html = renderToStaticMarkup(<ImageBriefCard project={project()} ctx={{ pid: "p_1" }} compact />);
    expect(html).toContain('<span class="ib-parts-line" aria-label="Catalog parts">MIC_INMP441 · SPK_28MM</span>');
    expect(html).not.toContain("<code>");
    expect(html).toContain('src="/api/projects/p_1/uploads/u_sheet00001?thumb=1"');
    expect(html).toContain('alt="acme concept"');
    expect(html).toContain("ib-card compact");
  });

  it("on a share link keeps the image private unless the owner shared it, and never uses owner URLs", () => {
    const off = renderToStaticMarkup(<ImageBriefCard project={project({ share_uploads: false, read_only: true })} ctx={{ pid: "p_1", share: "s_tok" }} readOnly />);
    expect(off).toContain("Bike helmet"); // the brief itself is shared
    expect(off).not.toContain("<img");
    expect(off).not.toContain("/api/projects/");
    expect(off).toContain("The image is not shared on this link.");
    expect(off).toContain("What the assistant read from the starting image</h3>");
    expect(off).not.toContain("What I read from your image");
    // the server leaves text_seen and upload_ids out of a share view without shared images: no "Text seen" block
    const { text_seen: _seen, upload_ids: _ids, ...shared } = brief;
    const hidden = renderToStaticMarkup(<ImageBriefCard project={project({ share_uploads: false, read_only: true, image_brief: shared as ImageBrief })} ctx={{ pid: "p_1", share: "s_tok" }} readOnly />);
    expect(hidden).toContain("Bike helmet");
    expect(hidden).not.toContain("Text seen in the image");
    expect(hidden).not.toContain("ACME helmet sketch");
    const on = renderToStaticMarkup(<ImageBriefCard project={project({ share_uploads: true, read_only: true })} ctx={{ pid: "p_1", share: "s_tok" }} readOnly />);
    expect(on).toContain('src="/api/share/s_tok/uploads/u_sheet00001?thumb=1"');
    expect(on).not.toContain("/api/projects/");
    const noToken = renderToStaticMarkup(<ImageBriefCard project={project({ share_uploads: true })} readOnly />);
    expect(noToken).not.toContain("/api/projects/");
  });

  it("copes with a thin brief (title only) and odd values", () => {
    const html = renderToStaticMarkup(
      <ImageBriefCard project={project({ image_brief: { title: "A desk lamp sketch", kind: "sketch", features: [{ text: "Glow", status: "maybe" } as any, { text: "" } as any], out_of_scope: [], text_seen: "" } })} ctx={{ pid: "p_1" }} />,
    );
    expect(html).toContain("A desk lamp sketch");
    expect(html).toContain("Sketch");
    expect(html).toContain("maybe");
    expect(html).not.toContain("Outside what Wish Labs");
    expect(html).not.toContain("Text seen in the image");
    expect(html).not.toContain("The closest gadget");
  });

  it("a share link without share_uploads says how many images stay private, with no URL at all", () => {
    const html = renderToStaticMarkup(<MessageImages atts={[]} ctx={{ pid: "p_1", share: "s_tok" }} shown="no" hidden={2} />);
    expect(html).toContain("2 images are not shared on this link.");
    expect(html).not.toContain("/api/");
    expect(renderToStaticMarkup(<MessageImages atts={[]} ctx={{ pid: "p_1" }} shown="yes" />)).toBe("");
  });

  it("mounts on the share Overview through the cards.ts glob contract", () => {
    const m = pickCard({ "/src/components/cards/ImageBriefCard.tsx": cardModule as unknown as Record<string, unknown> }, "ImageBriefCard", "hasImageBrief");
    expect(m).not.toBeNull();
    expect(cardWanted(m, project(), null)).toBe(true);
    expect(cardWanted(m, project({ image_brief: null }), null)).toBe(false);
  });
});
