// REVIEW-1 (web-frontend) unit tests: MAJ-39, MIN-27, MIN-29, MIN-43, MIN-44, MIN-45, MAJ-36, MAJ-8, BLK-4, MAJ-33.
import { describe, expect, it } from "vitest";
import { checkTally, excludesText, partsTotal, plainText, stageHasFailures, statusWords } from "./format";
import { buildSeconds, conceptSeconds, minuteRange, waitText } from "./estimate";
import { behindLatest, briefFor, briefSpecHash, compareFiles, designChangedSinceBrief, originText, triggerMessage } from "./versions";
import { groupConcepts } from "./concepts";
import { overlaps, placeLabels, placementProblems, type Rect } from "./place";
import { groupUse, splitUse } from "./steps";
import { readNudges } from "../components/panel/TweakDrawer";
import { missingClips, nameStates } from "../components/panel/StoryAudio";
import type { Build, Version } from "./types";

// ---------------------------------------------------------------- fixtures from the bedtime-robot benchmark (_out/e2e)
const VERSIONS: Version[] = [
  { n: 1, created: "2026-11-01T12:00:37Z", started: "2026-11-01T12:00:37Z", ended: "2026-11-01T12:02:38Z", label: "Build from brief v1", source: "build", state: "done", spec_hash: "sha256:e5c5", brief_version: 1, base_version: null },
  { n: 2, created: "2026-11-01T12:03:38Z", started: "2026-11-01T12:03:38Z", ended: "2026-11-01T12:05:50Z", label: "Larger face and rounder ears", source: "update", state: "done", spec_hash: "sha256:9b61", brief_version: 1, base_version: 1 },
  { n: 3, created: "2026-11-01T12:05:59Z", started: "2026-11-01T12:05:59Z", ended: "2026-11-01T12:08:11Z", label: "Pale teal body", source: "tweak", state: "done", spec_hash: "sha256:c755", brief_version: 1, base_version: 2 },
];
// phases.json 02-answers (the concept turn): three text calls, six photos side by side; wall 316.8 s
const CONCEPT_TURN = {
  turn_id: "t_rwxiz9rvqp",
  action: "answers",
  images: 6,
  calls: [
    { kind: "text", seconds: 121.66 },
    { kind: "text", seconds: 97.88 },
    { kind: "text", seconds: 54.42 },
    { kind: "image", seconds: 19.53 },
    { kind: "image", seconds: 20.85 },
    { kind: "image", seconds: 19.87 },
    { kind: "image", seconds: 20.73 },
    { kind: "image", seconds: 19.14 },
    { kind: "image", seconds: 19.83 },
  ],
};
const PROMPT_TURN = { turn_id: "t_pbv4fraq4g", action: "text", images: 0, calls: [{ kind: "text", seconds: 11.84 }] };

describe("MIN-45 wait estimates come from measured durations", () => {
  it("derives the build estimate from the fixture builds", () => {
    expect(buildSeconds(VERSIONS)).toEqual([121, 132, 132]);
    const r = minuteRange(buildSeconds(VERSIONS));
    expect(r).toEqual({ lo: 2, hi: 3, n: 3 });
    expect(waitText(r)).toBe("about 2 to 3 minutes");
  });
  it("derives the concept estimate from the fixture concept turn", () => {
    const s = conceptSeconds([PROMPT_TURN, CONCEPT_TURN]);
    expect(s.length).toBe(1);
    expect(s[0]).toBeCloseTo(294.81, 1); // text calls + slowest photo; the measured wall was 316.8 s
    expect(waitText(minuteRange(s))).toBe("about 5 to 7 minutes");
  });
  it("says nothing numeric without data, and skips restores", () => {
    expect(waitText(minuteRange([]))).toBe("a few minutes");
    expect(buildSeconds([{ ...VERSIONS[0], source: "restore" }])).toEqual([]);
  });
});

describe("MIN-29 / MIN-27 status words and checks", () => {
  it("maps raw status enums to words", () => {
    expect(statusWords("ESTIMATE_REVIEW_REQUIRED")).toBe("estimate, review needed");
    expect(plainText("one-off ₹18,999 (ESTIMATE_REVIEW_REQUIRED)")).toBe("one-off ₹18,999 (estimate, review needed)");
    expect(plainText("anything in missing_prices")).not.toContain("missing_prices");
  });
  it("never shows missing_prices and names unpriced parts", () => {
    const ex = ["import duties (all parts assumed bought in India)", "anything in missing_prices", "USB-C power adapter"];
    expect(excludesText(ex, [])).toEqual(["import duties (all parts assumed bought in India)", "USB-C power adapter"]);
    expect(excludesText(ex, ["Backplane PCB"]).join("|")).toContain("Parts without a price yet: Backplane PCB");
    expect(excludesText(ex, ["X"]).join("|")).not.toContain("missing_prices");
  });
  it("tallies failed and warned checks in both summary shapes", () => {
    expect(checkTally({ digital_passed: 85, digital_total: 87, digital_failed: ["Collisions", "Clearance"] })).toMatchObject({ failed: 2, failedTitles: ["Collisions", "Clearance"] });
    expect(checkTally({ digital_passed: 83, digital_total: 90, digital_failed: 4, digital_warned: ["a", "b", "c"] })).toMatchObject({ failed: 4, warned: 3 });
  });
  it("flags a finished stage whose result has failures", () => {
    expect(stageHasFailures({ message: "85 passed, 2 failed, 3 warnings, 0 not run" })).toBe(true);
    expect(stageHasFailures({ message: "88 passed, 0 failed, 2 warnings, 0 not run" })).toBe(false);
    expect(stageHasFailures({ message: "done", failed: 1 })).toBe(true);
    expect(stageHasFailures({ message: "x", flag: "warn" })).toBe(true);
  });
  it("uses one parts figure, preferring the pipeline's parts total", () => {
    const q = { scenarios: [{ name: "Pilot one-off", breakdown_mean: { parts: 3831 } }] };
    // MAJ-23 shape published by pipeline
    expect(partsTotal({ ...q, parts_total_inr: { one_off: 4947, batch_unit: 4410, steady_state_unit: 3600, basis: "GST-inclusive, seen prices first" } }, null)).toEqual({ inr: 4947, basis: "GST-inclusive, seen prices first", source: "quote" });
    expect(partsTotal({ ...q, parts_total_inr: 4900 }, null)?.inr).toBe(4900);
    expect(partsTotal(q, { order: { parts_total_inr: 4800 } } as any)?.inr).toBe(4800);
    expect(partsTotal(q, null)).toMatchObject({ inr: 3831, source: "quote-breakdown" });
    expect(partsTotal(null, null)).toBeNull();
  });
});

describe("MAJ-39 versions", () => {
  it("shows the behind-latest banner only when current is older than the newest", () => {
    expect(behindLatest({ versions: VERSIONS, current_version: 1 })).toEqual({ cur: 1, latest: 3 });
    expect(behindLatest({ versions: VERSIONS, current_version: 3 })).toBeNull();
  });
  it("labels where a version came from", () => {
    expect(originText(VERSIONS[2])).toBe("from v2");
    expect(originText(VERSIONS[0])).toBeNull();
    expect(originText({ ...VERSIONS[0], n: 4, source: "restore", base_version: 1 })).toBe("restored from v1");
  });
  it("compares two manifests", () => {
    const a = [{ path: "a", bytes: 1, sha256: "x" }, { path: "b", bytes: 2, sha256: "y" }, { path: "c", bytes: 3 }];
    const b = [{ path: "a", bytes: 1, sha256: "x" }, { path: "b", bytes: 2, sha256: "z" }, { path: "d", bytes: 3 }];
    expect(compareFiles(a, b)).toEqual({ added: ["c"], removed: ["d"], changed: ["b"], same: 1 });
  });
  it("finds the message that started a version", () => {
    const chat: any[] = [
      { id: "u1", role: "user", kind: "text", text: "make the ears rounder", payload: null },
      { id: "p2", role: "assistant", kind: "progress", text: "", payload: { version: 2 } },
    ];
    expect(triggerMessage(chat, 2)?.id).toBe("u1");
    expect(triggerMessage(chat, 9)).toBeNull();
  });
});

describe("MAJ-36 design changed since this brief", () => {
  const brief: any = { version: 1, title: "Bedtime Story Robot" };
  it("anchors a brief to the version built from it, never to its input-spec hash", () => {
    expect(briefSpecHash(brief, VERSIONS)).toBe("sha256:e5c5");
    // an edit brief (MAJ-36) names the version it was written for
    expect(briefSpecHash({ version: 2, auto_for_version: 2, spec_hash: "sha256:input" } as any, VERSIONS)).toBe("sha256:9b61");
    expect(briefSpecHash({ version: 9, spec_hash: "sha256:input" } as any, VERSIONS)).toBeNull();
  });
  it("flags v3 against brief v1 and not v1", () => {
    expect(designChangedSinceBrief(brief, VERSIONS[2], VERSIONS)).toBe(true);
    expect(designChangedSinceBrief(brief, VERSIONS[0], VERSIONS)).toBe(false);
    expect(designChangedSinceBrief({ version: 7 } as any, VERSIONS[2], VERSIONS)).toBe(false); // unknown hash: no banner
  });
  it("picks the brief that belongs to a version", () => {
    const p: any = { brief: { version: 3 }, brief_history: [{ version: 1 }, { version: 2 }, { version: 3 }] };
    expect(briefFor(p, { ...VERSIONS[1], brief_version: 2 })?.version).toBe(2);
    expect(briefFor(p, null)?.version).toBe(3);
  });
});

describe("MIN-43 concept tiers", () => {
  const cs: any[] = [
    { id: "a", title: "A", tier: "expected" },
    { id: "b", title: "B", tier: "different" },
    { id: "c", title: "C", tier: "wildcard" },
    { id: "d", title: "D", tier: "expected" },
  ];
  it("groups by tier with the dock's labels", () => {
    expect(groupConcepts(cs as any).map((g) => [g.label, g.items.map((c) => c.id)])).toEqual([
      ["Expected", ["a", "d"]],
      ["Different ideas", ["b"]],
      ["Wildcards", ["c"]],
    ]);
  });
  it("honours payload.tiers and leaves untiered sets as one group", () => {
    expect(groupConcepts(cs as any, { expected: ["c"], different: [], wildcard: ["a"] }).map((g) => g.label)).toEqual(["Expected", "Wildcards", "More"]);
    expect(groupConcepts([{ id: "x", title: "X" }] as any)).toEqual([{ id: "all", label: "", items: [{ id: "x", title: "X" }] }]);
  });
});

describe("MIN-44 callout placement", () => {
  const bounds: Rect = { x: 0, y: 0, w: 1050, h: 558 };
  const card: Rect = { x: 16, y: 32, w: 390, h: 510 };
  const ctl: Rect = { x: 900, y: 500, w: 134, h: 42 };
  // 12 anchors clustered on the model, as in the step-1 screenshot
  const anchors = Array.from({ length: 12 }, (_, i) => ({ id: `p${i}`, ax: 560 + (i % 4) * 30, ay: 180 + Math.floor(i / 4) * 70, w: 230 + (i % 3) * 40, h: 34 }));
  it("places labels with no overlap of labels, the card, the controls or the anchor dots", () => {
    const r = placeLabels(anchors, bounds, [card, ctl]);
    expect(r.placed.length + r.hidden.length).toBe(12);
    expect(r.placed.length).toBeGreaterThanOrEqual(6);
    expect(placementProblems(r, [card, ctl])).toEqual([]);
  });
  it("hides a label whose anchor sits under the card, and one that cannot fit", () => {
    const r = placeLabels([{ id: "under", ax: 200, ay: 200, w: 120, h: 30 }, { id: "huge", ax: 700, ay: 300, w: 2000, h: 30 }], bounds, [card]);
    expect(r.hidden.sort()).toEqual(["huge", "under"]);
  });
  it("keeps a label off its own anchor dot", () => {
    const r = placeLabels([{ id: "led", ax: 600, ay: 300, w: 120, h: 30 }], bounds, []);
    const p = r.placed[0];
    expect(overlaps(p.rect, { x: 593, y: 293, w: 14, h: 14 })).toBe(false);
  });
});

describe("MIN-44 Use list grouping", () => {
  const md =
    "**Use:** 1 × **Speaker 40 mm round, 4 ohm, 3 W**, 1 × **Big 30 mm snap-in arcade push button (microswitch)**, 1 × **1.3 in 128x64 mono OLED, SH1106, I2C 4-pin**, 1 × **JST-PH 2.0 4-pin harness, 300 mm, pre-crimped both ends**, 17 × **M2.5 heat-set inserts**, 9 × **M2.5 module clamp screws**, 1 × `PRINT_Shell` and 4 × **adhesive bumper feet**.\n\n**Tools:** None.\n\nLay every purchased item on a clear table.";
  const build: Build = { purchased: [{ name: "Speaker 40 mm round, 4 ohm, 3 W", quantity: 1, kind: "module" }, { name: "JST-PH 2.0 4-pin harness, 300 mm, pre-crimped both ends", quantity: 1, kind: "cable" }] };
  it("splits the Use list from the step text and groups it by role", () => {
    const s = splitUse(md, build)!;
    expect(s.items.length).toBe(8);
    expect(s.rest.startsWith("**Tools:** None.")).toBe(true);
    expect(groupUse(s.items).map((g) => [g.role, g.pieces])).toEqual([
      ["electronics", 3],
      ["cables", 1],
      ["printed", 1],
      ["hardware", 30],
    ]);
  });
  it("counts gaskets and feet as hardware, not electronics", () => {
    const s = splitUse("**Use:** 1 × **Speaker gasket: single-sided adhesive foam strip 25 x 3 mm**, 1 × **INMP441 I2S MEMS microphone module**, 4 × **adhesive bumper feet**, 1 × `PRINT_MicClip` and 2 × **M2.5 module clamp screws**.", { purchased: [{ name: "Speaker gasket: single-sided adhesive foam strip 25 x 3 mm (+ printed clamp ring)", quantity: 1, kind: "hardware" }] })!;
    expect(s.items.map((i) => i.role)).toEqual(["hardware", "electronics", "hardware", "printed", "hardware"]);
  });
  it("leaves text without a Use list alone", () => {
    expect(splitUse("Plug it in.", build)).toBeNull();
  });
});

describe("MAJ-8 drawer nudges", () => {
  it("reads nudges in the proposed shapes", () => {
    expect(readNudges({ ok: true, warnings: [] })).toEqual([]);
    expect(readNudges({ nudges: ["Night light touches the speaker ring: move it up 10 mm or more"] })[0].text).toContain("move it up 10 mm");
    const n = readNudges({ conflicts: [{ pair: ["PURCHASE_Led", "PRINT_SpeakerRing"], message: "Night light touches the speaker ring", nudge: { feature: "night_light", dz_mm: 10, text: "move it up 10 mm or more" } }] })[0];
    expect(n).toMatchObject({ feature: "night_light", dz: 10, pair: ["PURCHASE_Led", "PRINT_SpeakerRing"] });
    expect(n.text).toBe("Night light touches the speaker ring: move it up 10 mm or more");
  });
  it("reads web-backend's nudge rows (direction + move_mm on the arrow axes)", () => {
    const rows = readNudges({ ok: true, nudges: [
      { parts: ["PURCHASE_Led", "PRINT_SpeakerRing"], labels: ["Night light", "Speaker clamp ring"], feature: "night_light", spec_path: "features[1]", move_mm: 7, direction: "up", resolved: false, message: "Night light touches the Speaker clamp ring: move it up 7 mm or more." },
      { parts: ["A", "B"], feature: "button", move_mm: 3, direction: "left", resolved: true, message: "Button sits too close to the display: move it left 3 mm or more. Your change already moves it that far." },
    ] });
    expect(rows[0]).toMatchObject({ feature: "night_light", dz: 7, pair: ["PURCHASE_Led", "PRINT_SpeakerRing"], resolved: false });
    expect(rows[0].dx).toBeUndefined();
    expect(rows[1]).toMatchObject({ dx: -3, resolved: true });
  });
  it("reads cad's footprint.layout_conflicts rows", () => {
    const n = readNudges({ ok: true, conflicts: [{ a: "night_light", b: "speaker", a_part: "PURCHASE_Led", a_what: "night light", b_part: "PRINT_SpeakerRing", b_what: "speaker clamp ring tab", gap_mm: -1.5, overlap_v_mm: 4, nudge: "move night_light up 6 mm or more" }] })[0];
    expect(n.text).toBe("night light and speaker clamp ring tab overlap by 1.5 mm: move night_light up 6 mm or more");
    expect(n.pair).toEqual(["PURCHASE_Led", "PRINT_SpeakerRing"]);
    expect(n.dz).toBeUndefined(); // text-only nudge: no Apply button
  });
});

describe("BLK-4 / MAJ-33 story card state", () => {
  const pack: any = { audio: false, default_name: { name: "little star", file: "names/_default.wav" }, names: [{ name: "Asha", file: "names/asha.wav" }], stories: [{ id: "s", files: ["stories/s/s00.wav"] }] };
  const none = () => false;
  it("marks every name not spoken while the pack has no audio", () => {
    expect(nameStates(pack, none).map((n) => [n.name, n.spoken])).toEqual([
      ["little star", false],
      ["Asha", false],
    ]);
  });
  it("marks names spoken when their clip is in the version, and lists missing story clips", () => {
    const files = new Set(["firmware/sdcard/names/_default.wav", "firmware/sdcard/stories/s/s00.wav"]);
    const has = (p: string) => files.has(p);
    const withAudio = { ...pack, audio: true };
    expect(nameStates(withAudio, has).map((n) => n.spoken)).toEqual([true, false]);
    expect(missingClips(withAudio, has)).toEqual([]);
    expect(missingClips({ ...withAudio, stories: [{ id: "x", files: ["stories/x/s00.wav"] }] }, has)).toEqual(["stories/x/s00.wav"]);
    expect(nameStates({ ...withAudio, names: [{ name: "Ravi", name_audio: false }] }, has)[1].spoken).toBe(false);
  });
});
