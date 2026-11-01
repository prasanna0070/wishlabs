// MIN-29 component test: rendered markup (react-dom/server, no DOM needed) for the result chip and the build card's
// stage rows. A run with failed checks shows the warning icon on both, never the green tick, and no raw enum string.
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ResultChips, StageList } from "./chat/Messages";

const WARN = "lucide-triangle-alert";

describe("MIN-29 checks chip and stage row", () => {
  it("shows the warning icon on the chip when any digital check failed", () => {
    const html = renderToStaticMarkup(
      <ResultChips
        summary={{
          checks: { digital_passed: 85, digital_total: 87, digital_failed: ["Full pair matrix of rigid components", "Feature modules clear each other"], firmware_tests: "58/58", pcb_drc_errors: 0 },
          price: { inr: 18999, label: "₹18,999 delivered (estimate, not a binding price)" },
        }}
      />,
    );
    expect(html).toContain(WARN);
    expect(html).not.toContain("lucide-check ");
    expect(html).toContain("2 failed");
  });
  it("shows the tick only when every check passed", () => {
    const html = renderToStaticMarkup(<ResultChips summary={{ checks: { digital_passed: 88, digital_total: 88, digital_failed: [] } }} />);
    expect(html).not.toContain(WARN);
    expect(html).toContain("lucide-check");
  });
  it("gives the checks stage row a warning icon and keeps raw enums out of the build card", () => {
    const stages = [
      { id: "order", label: "Print slicing and price", state: "done", message: "22 purchased rows, one-off ₹18,999 (ESTIMATE_REVIEW_REQUIRED)" },
      { id: "checks", label: "Digital checks", state: "done", message: "85 passed, 2 failed, 3 warnings, 0 not run" },
      { id: "docs", label: "Project documents", state: "done", message: "PROJECT.md 10,183 B" },
    ];
    const html = renderToStaticMarkup(<StageList stages={stages} now={Date.now()} />);
    const rows = html.split("<li").slice(1);
    expect(rows[1]).toContain(WARN);
    expect(rows[1]).toContain("has-failures");
    expect(rows[0]).not.toContain(WARN);
    expect(rows[2]).toContain("lucide-circle-check");
    expect(html).not.toContain("ESTIMATE_REVIEW_REQUIRED");
    expect(html).toContain("estimate, review needed");
  });
});

// REVIEW-1 MAJ-37: the brief trace renders one row per requirement with its status words and evidence path.
import { BriefTrace } from "./chat/Messages";
import { decorName, baseName } from "../three/Viewer";
import { decorInfo, partLabeler } from "./panel/PreviewTab";

describe("MAJ-37 brief trace", () => {
  it("renders Not met for the size row with its evidence and an Implemented row", () => {
    const html = renderToStaticMarkup(
      <BriefTrace
        rows={[
          { item: "Size: About 131 W x 110 D x 168 H mm", status: "not_met", evidence: "H brief 168 vs CAD 188 mm", paths: ["cad/components.json"] },
          { item: "Voice input", status: "implemented", evidence: "MIC1 MIC_INMP441_I2S on J_MIC", paths: ["electrical.json"] },
        ]}
      />,
    );
    expect(html).toContain("Brief trace · 1 of 2 implemented");
    expect(html).toContain("Not met");
    expect(html).toContain("168 vs CAD 188");
    expect(html).toContain("cad/components.json");
    expect(html).toContain("Implemented");
  });
  it("renders nothing without rows", () => {
    expect(renderToStaticMarkup(<BriefTrace rows={[]} />)).toBe("");
  });
});

// REVIEW-1 MAJ-35: a raycast hit on a decor pick mesh selects that decor item and the popover says "Edit ears".
describe("MAJ-35 decor picking", () => {
  it("maps GLTFLoader's sanitised node names back to the components.json decor name", () => {
    expect(decorName("PRINT_Shell#decor1")).toBe("PRINT_Shell#decor[1]");
    expect(decorName("PRINT_Shell#decor[3]")).toBe("PRINT_Shell#decor[3]");
    expect(decorName("PRINT_Shell#decor1_2")).toBe("PRINT_Shell#decor[1]");
    expect(decorName("PRINT_Shell")).toBeNull();
    expect(baseName("PRINT_Shell#decor[1]")).toBe("PRINT_Shell");
  });
  it("labels the popover from components.json decor[]", () => {
    const comps = { PRINT_Shell: { kind: "PRINT", label: "main shell", decor: [{ name: "PRINT_Shell#decor[1]", spec_path: "decor[1]", label: "ears" }] } };
    expect(decorInfo(comps, "PRINT_Shell#decor[1]")?.spec_path).toBe("decor[1]");
    expect(partLabeler(comps)("PRINT_Shell#decor[1]").title).toBe("EARS");
    expect(partLabeler(comps)("PRINT_Shell").title).toBe("MAIN SHELL");
  });
});
