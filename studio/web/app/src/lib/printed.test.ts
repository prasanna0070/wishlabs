import { describe, expect, it } from "vitest";
import { noElectronics } from "./showcase";
import type { Build } from "./types";

// catalog pack "printed": a printed-only object never shows a circuit board, firmware or wiring (Overview, Build tab)
const printedBuild = { pcb: null, firmware: null, wiring: { runtime: "", board: "", code: "", parts: [], connections: [], steps: [] }, steps: [] } as unknown as Build;
const gadgetBuild = { pcb: { layers: 2, routed: true }, firmware: { compiled: true }, wiring: { board: "esp32-s3", connections: [{}] } } as unknown as Build;

describe("noElectronics", () => {
  it("is true when the result summary says kind printed, even before build.json loads", () => {
    expect(noElectronics({ kind: "printed" }, null)).toBe(true);
  });
  it("is true for a build with no PCB, no firmware and no wiring", () => {
    expect(noElectronics(null, printedBuild)).toBe(true);
  });
  it("is false for a gadget, a gadget summary and an unknown build", () => {
    expect(noElectronics({ kind: "gadget" }, gadgetBuild)).toBe(false);
    expect(noElectronics(null, gadgetBuild)).toBe(false);
    expect(noElectronics(null, null)).toBe(false);
    expect(noElectronics({ kind: "gadget" }, { ...gadgetBuild, pcb: null, firmware: null } as unknown as Build)).toBe(false);
  });
});
