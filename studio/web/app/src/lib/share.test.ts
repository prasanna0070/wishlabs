import { describe, expect, it } from "vitest";
import { aiReceipts, compact, modelName, roleOf, usd } from "./receipts";
import { bestStageErrorText, engineFix, friendlyError, isTechnical, stageErrorText } from "./errors";
import { buildTime, chosenConcept, fileGroups, originalWish, resultMessage, showcaseVersion, testCount, PRICE_LINE } from "./showcase";
import { safeShareUrl } from "./urls";
import { cardWanted, pickCard } from "../components/panel/cards";

const call = (model: string, kind: string, extra: Record<string, number> = {}) => ({
  model,
  kind,
  input_tokens: 1000,
  output_tokens: 100,
  thinking_tokens: 50,
  images: kind === "image" ? 1 : 0,
  seconds: 2,
  cost_usd_est: 0.01,
  ...extra,
});

describe("AI receipts", () => {
  it("groups calls by job and model, design agent first", () => {
    const r = aiReceipts({
      basis: "x",
      turns: [
        { calls: [call("gemini-3.5-flash", "text"), call("gemini-3.5-flash", "text"), call("gemini-3-pro-image", "image")] },
        { calls: [call("gemini-3.1-pro-preview", "text", { cost_usd_est: 0.5 })] },
      ],
      totals: { turns: 2 },
    })!;
    expect(r.rows.map((x) => [x.role, x.model, x.calls])).toEqual([
      ["agent", "gemini-3.1-pro-preview", 1],
      ["agent", "gemini-3.5-flash", 2],
      ["photos", "gemini-3-pro-image", 1],
    ]);
    expect(r.calls).toBe(4);
    expect(r.images).toBe(1);
    expect(r.turns).toBe(2);
    expect(r.tokens).toBe(4 * 1150);
    expect(r.inputTokens).toBe(4000);
    expect(r.outputTokens).toBe(600);
    expect(r.costUsd).toBeCloseTo(0.53, 6);
  });
  it("recognises voice, sound and speech calls, including ones kept outside a turn", () => {
    const r = aiReceipts({ calls: [call("gemini-live-2.5-flash-native-audio", "live"), call("lyria-002", "music"), call("gemini-2.5-flash-tts", "tts")] })!;
    expect(r.rows.map((x) => x.role)).toEqual(["voice", "sound", "speech"]);
    expect(roleOf("some-model", "voice")).toBe("voice");
    expect(roleOf("gemini-3.8-live", "text")).toBe("voice");
    expect(roleOf("some-model", "lyria")).toBe("sound");
    expect(roleOf("gemini-3-pro-image", "image")).toBe("photos");
    expect(roleOf("gemini-3.5-flash", "text")).toBe("agent");
  });
  it("returns null when nothing was recorded", () => {
    expect(aiReceipts(null)).toBeNull();
    expect(aiReceipts({ turns: [] })).toBeNull();
    expect(aiReceipts({ turns: [{ calls: [] }] })).toBeNull();
  });
  it("names models and formats numbers", () => {
    expect(modelName("gemini-3.1-pro-preview")).toBe("Gemini 3.1 Pro (preview)");
    expect(modelName("gemini-3.5-flash")).toBe("Gemini 3.5 Flash");
    expect(modelName("gemini-3-pro-image")).toBe("Gemini 3 Pro Image (Nano Banana Pro)");
    expect(modelName("gemini-2.5-flash-tts")).toBe("Gemini-TTS");
    expect(modelName("lyria-002")).toBe("Lyria");
    expect(modelName("brand-new-model")).toBe("brand-new-model");
    expect(compact(341757)).toBe("342k");
    expect(compact(1234567)).toBe("1.2M");
    expect(compact(1500)).toBe("1.5k");
    expect(usd(1.291129)).toBe("$1.29");
    expect(usd(0.004)).toBe("<$0.01");
  });
});

const SPEC_TEXT =
  "The build stopped during Check the design: 1 stage(s) failed: normalize: SpecError: features[3].at: speaker: its kit (ring tab at 270 deg) reaches 10.2 mm into the shell wall or floor near x 0, y -51, z -9 Fix: move speaker toward th\n\nNothing from this attempt should be ordered or printed. You can ask me to change the design, or try the build again.";
const FULL =
  "SpecError: features[3].at: speaker: its kit (ring tab at 270 deg) reaches 10.2 mm into the shell wall or floor near x 0, y -51, z -9 Fix: move speaker toward the middle of the front face, away from the rounded edge (by 13 mm or more)";

describe("friendly errors", () => {
  it("turns a SpecError card into a plain sentence with the raw text kept as details", () => {
    const f = friendlyError(SPEC_TEXT, FULL);
    expect(f.headline).toBe("The build stopped during Check the design: the design did not pass one of its own engineering checks, so this version was not built.");
    expect(f.headline).not.toMatch(/SpecError|features\[/);
    expect(f.rest).toMatch(/^Nothing from this attempt/);
    expect(f.details).toBe(FULL);
    expect(f.fix).toBe("Move speaker toward the middle of the front face, away from the rounded edge (by 13 mm or more).");
  });
  it("handles the bare version error and keeps truncated text out of the fix", () => {
    const f = friendlyError("1 stage(s) failed: normalize: SpecError: features[1].at: night_light: no clear place Fix: move th");
    expect(f.headline).toBe("The design did not pass one of its own engineering checks, so this version was not built.");
    expect(f.fix).toBeNull();
    expect(f.details).toMatch(/^1 stage\(s\) failed/);
  });
  it("leaves plain messages alone", () => {
    const f = friendlyError("The design assistant is unavailable right now.");
    expect(f).toEqual({ headline: "The design assistant is unavailable right now.", rest: "", fix: null, details: null });
    expect(isTechnical("RuntimeError: boom")).toBe(true);
    expect(friendlyError("RuntimeError: boom").headline).toBe("One step of the pipeline reported an error, so this version was not finished.");
  });
  it("takes the longer stage text and the first fix line", () => {
    const st = { state: "error", stages: [{ id: "normalize", label: "Check the design", state: "failed", message: "SpecError: short", error: "SpecError: a longer text Fix: do this first\nfeatures[4] Fix: and that" }] } as any;
    expect(stageErrorText(st)).toMatch(/longer text/);
    expect(engineFix(stageErrorText(st))).toBe("Do this first.");
    expect(stageErrorText({ state: "done", stages: [] } as any)).toBeNull();
  });
  it("drops a fix the engine cut short and prefers the longest copy of the stage text", () => {
    expect(engineFix("SpecError: features[3].at: speaker reaches the wall Fix: move speaker toward th")).toBeNull();
    expect(engineFix("SpecError: features[2].at: button kit overlaps Fix: button touches the led clamp post -: move button up 22 mm or more")).toBe(
      "Button touches the led clamp post -: move button up 22 mm or more.",
    );
    const cut = { state: "error", stages: [{ id: "normalize", state: "failed", message: "SpecError: x Fix: move th" }] } as any;
    const whole = { state: "error", stages: [{ id: "normalize", state: "failed", message: "SpecError: x Fix: move the speaker 13 mm or more" }] } as any;
    expect(bestStageErrorText(cut, null, whole)).toMatch(/13 mm or more$/);
    expect(bestStageErrorText(null, undefined)).toBeNull();
  });
});

describe("share page data", () => {
  const project: any = {
    id: "p_1",
    chat: [
      { id: "1", role: "user", kind: "text", text: "A small desk gadget that glows", payload: [] },
      { id: "2", role: "assistant", kind: "concepts", text: "Choose", payload: {} },
      { id: "3", role: "user", kind: "text", text: "My choices:\nMochi", payload: { action: "select_concept", concept_id: "c_b" } },
      { id: "4", role: "assistant", kind: "result", text: "done", payload: { version: 1, summary: { checks: { firmware_tests: "50/50" } } } },
    ],
    concepts: [
      { id: "c_a", title: "A", photo_url: null, render_url: "/r/a.png" },
      { id: "c_b", title: "Mochi", photo_url: "/p/b.png", render_url: "/r/b.png" },
    ],
    selected_concept: null,
    brief: { version: 1, concept_id: "c_b", title: "Mochi" },
    versions: [
      { n: 1, state: "done", created: "", label: "", started: "2026-11-02T10:10:14Z", ended: "2026-11-02T10:19:57Z", brief_version: 1 },
      { n: 2, state: "error", created: "", label: "" },
    ],
    current_version: 2,
  };
  it("finds the wish, the chosen concept, the shown version and its result", () => {
    expect(originalWish(project)).toBe("A small desk gadget that glows");
    const v = showcaseVersion(project)!;
    expect(v.n).toBe(1);
    expect(chosenConcept(project, v)!.id).toBe("c_b");
    expect(chosenConcept({ ...project, brief: null }, v)!.id).toBe("c_b");
    expect(resultMessage(project, 1)!.payload.summary.checks.firmware_tests).toBe("50/50");
    expect(testCount("50/50")).toEqual({ passed: 50, total: 50 });
    expect(testCount(null)).toBeNull();
    expect(buildTime(v.started, v.ended)).toBe("9 min 43 s");
  });
  it("groups files by folder", () => {
    const g = fileGroups([
      { path: "BUILD_GUIDE.md", bytes: 10 },
      { path: "cad/a.step", bytes: 100 },
      { path: "cad/b.stl", bytes: 50 },
      { path: "pcb/x.kicad_pcb", bytes: 5 },
    ]);
    expect(g.map((x) => [x.folder, x.count, x.bytes])).toEqual([
      ["cad", 2, 150],
      ["", 1, 10],
      ["pcb", 1, 5],
    ]);
  });
  it("carries the decided price line with the rupee sign", () => {
    expect(PRICE_LINE).toBe("First units ₹19k to 28k; under ₹10k at volume (modelled).");
  });
});

describe("feature cards on the Overview tab", () => {
  const Card = () => null;
  it("finds the card by default or by name, with its predicate", () => {
    const a = pickCard({ "/src/x/VoiceWishCard.tsx": { default: Card, hasVoiceWish: () => true } }, "VoiceWishCard", "hasVoiceWish")!;
    expect(a.C).toBe(Card);
    expect(a.has).toBeTypeOf("function");
    const b = pickCard({ "/src/y/SoundCard.tsx": { SoundCard: Card } }, "SoundCard", "hasSound")!;
    expect(b.C).toBe(Card);
    expect(b.has).toBeNull();
    expect(pickCard({}, "SoundCard", "hasSound")).toBeNull();
    expect(pickCard({ "/src/z/SoundCard.tsx": { other: 1 } }, "SoundCard", "hasSound")).toBeNull();
  });
  it("renders only when the predicate says so, never when it throws", () => {
    const p: any = { id: "p" };
    expect(cardWanted(null, p, null)).toBe(false);
    expect(cardWanted({ C: Card, has: null }, p, null)).toBe(true);
    expect(cardWanted({ C: Card, has: () => false }, p, null)).toBe(false);
    expect(
      cardWanted(
        {
          C: Card,
          has: () => {
            throw new Error("x");
          },
        },
        p,
        null,
      ),
    ).toBe(false);
  });
});

describe("sign-in page share link", () => {
  it("accepts https links and same-site paths only", () => {
    expect(safeShareUrl("https://wish.plugbook.si/studio/s/s_abc")).toBe("https://wish.plugbook.si/studio/s/s_abc");
    expect(safeShareUrl(" /studio/s/s_abc ")).toBe("/studio/s/s_abc");
    for (const bad of [null, undefined, "", "javascript:alert(1)", "http://x.example/s", "//evil.example/x", 'https://ok.example/"><b>']) expect(safeShareUrl(bad as any)).toBeNull();
  });
});
