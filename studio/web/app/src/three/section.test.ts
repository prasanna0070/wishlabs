// Section view: the cut plane maths (section.ts) and the Preview tab toggle + controls (PreviewTab.tsx). Node only:
// the plane is checked with three.js maths, the controls as server-rendered markup (no DOM, no WebGL).
import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import * as THREE from "three";
import { SCENE_COMPONENT, SECTION_DEFAULT, sectionPlane, sectionWords, type SectionAxis } from "./section";
import { g2s } from "./Viewer";
import { PreviewTab, SectionControls } from "../components/panel/PreviewTab";
import { useProject } from "../store/project";

// a gadget 100 W x 60 D x 80 H mm standing on the floor, centred: gadget x -50..50, y -30..30 (front at -30), z 0..80
const GADGET_BOX = new THREE.Box3().setFromPoints([g2s([-50, -30, 0]), g2s([50, 30, 80])]);
const corners = (b: THREE.Box3) =>
  [0, 1, 2, 3, 4, 5, 6, 7].map((k) => new THREE.Vector3(k & 1 ? b.max.x : b.min.x, k & 2 ? b.max.y : b.min.y, k & 4 ? b.max.z : b.min.z));

describe("sectionPlane", () => {
  it("maps the gadget axes to the scene (x -> x, y (depth) -> z, z (height) -> y), as g2s does", () => {
    expect(SCENE_COMPONENT).toEqual({ x: 0, y: 2, z: 1 });
    expect(GADGET_BOX.min.toArray()).toEqual([-50, 0, -30]);
    expect(GADGET_BOX.max.toArray()).toEqual([50, 80, 30]);
  });

  const cases: [SectionAxis, number, number][] = [
    // axis, scene component, expected cut coordinate at t = 0.25 (the range is padded by 0.1 % + 0.01 mm a side)
    ["x", 0, -25],
    ["y", 2, -15],
    ["z", 1, 20],
  ];
  for (const [axis, i, at] of cases) {
    it(`axis ${axis}: unit normal along scene component ${i}, constant at the cut, flip reverses both`, () => {
      const p = sectionPlane(axis, 0.25, false, GADGET_BOX);
      const n = [0, 0, 0];
      n[i] = -1;
      expect(p.normal.toArray()).toEqual(n);
      expect(p.constant).toBeCloseTo(at, 0);
      const q = sectionPlane(axis, 0.25, true, GADGET_BOX);
      n[i] = 1;
      expect(q.normal.toArray()).toEqual(n);
      expect(q.constant).toBeCloseTo(-at, 0);
      // both planes pass through the same cut
      const onCut = new THREE.Vector3();
      onCut.setComponent(i, -p.constant / p.normal.getComponent(i));
      expect(Math.abs(q.distanceToPoint(onCut))).toBeLessThan(1e-9);
    });
  }

  it("unflipped, keeps the side away from the default camera (right, front, top are cut off)", () => {
    const x = sectionPlane("x", 0.5, false, GADGET_BOX);
    expect(x.distanceToPoint(g2s([30, 0, 40]))).toBeLessThan(0); // right half clipped
    expect(x.distanceToPoint(g2s([-30, 0, 40]))).toBeGreaterThan(0);
    const y = sectionPlane("y", 0.5, false, GADGET_BOX);
    expect(y.distanceToPoint(g2s([0, -20, 40]))).toBeLessThan(0); // front half clipped
    expect(y.distanceToPoint(g2s([0, 20, 40]))).toBeGreaterThan(0);
    const z = sectionPlane("z", 0.5, false, GADGET_BOX);
    expect(z.distanceToPoint(g2s([0, 0, 70]))).toBeLessThan(0); // top half clipped
    expect(z.distanceToPoint(g2s([0, 0, 10]))).toBeGreaterThan(0);
    const yf = sectionPlane("y", 0.5, true, GADGET_BOX);
    expect(yf.distanceToPoint(g2s([0, -20, 40]))).toBeGreaterThan(0); // flipped: front kept
    expect(yf.distanceToPoint(g2s([0, 20, 40]))).toBeLessThan(0);
  });

  it("t = 1 keeps the whole box and t = 0 clips all of it (reversed when flipped)", () => {
    for (const axis of ["x", "y", "z"] as const) {
      const all = sectionPlane(axis, 1, false, GADGET_BOX);
      const none = sectionPlane(axis, 0, false, GADGET_BOX);
      const allF = sectionPlane(axis, 0, true, GADGET_BOX);
      const noneF = sectionPlane(axis, 1, true, GADGET_BOX);
      for (const c of corners(GADGET_BOX)) {
        expect(all.distanceToPoint(c)).toBeGreaterThan(0);
        expect(none.distanceToPoint(c)).toBeLessThan(0);
        expect(allF.distanceToPoint(c)).toBeGreaterThan(0);
        expect(noneF.distanceToPoint(c)).toBeLessThan(0);
      }
    }
  });

  it("clamps t to 0..1, treats a non-number as the middle and cuts an empty box at 0", () => {
    const at = (t: number) => sectionPlane("z", t, false, GADGET_BOX).constant;
    expect(at(-3)).toBe(at(0));
    expect(at(7)).toBe(at(1));
    expect(at(Number.NaN)).toBe(at(0.5));
    const e = sectionPlane("x", 0.3, false, new THREE.Box3());
    expect(Number.isFinite(e.constant)).toBe(true);
    expect(Math.abs(e.constant)).toBeLessThan(0.1);
  });

  it("writes into the plane it is given (the viewer shares one plane with every material)", () => {
    const shared = new THREE.Plane();
    const out = sectionPlane("y", 0.7, true, GADGET_BOX, shared);
    expect(out).toBe(shared);
    expect(shared.normal.z).toBe(1);
  });

  it("describes the cut for screen readers", () => {
    expect(sectionWords(SECTION_DEFAULT)).toBe("Cut at 50% of the depth");
    expect(sectionWords({ axis: "z", t: 0.234, flip: true })).toBe("Cut at 23% of the height, flipped");
  });
});

describe("Section view toggle (Preview tab)", () => {
  it("sits between Size reference and Model parts, and shows no cut controls until it is switched on", () => {
    const v1 = { n: 1, created: "2026-11-20T08:00:00Z", label: "v1", state: "done" };
    // zustand hands server rendering the store's initial state object: fill it for this one render, then restore it
    const init = useProject.getInitialState() as any;
    const keys = ["project", "ctx", "statuses", "readOnly", "turn"];
    const saved = Object.fromEntries(keys.map((k) => [k, init[k]]));
    Object.assign(init, {
      project: { id: "p_test", name: "Test", created: "", updated: "", stage: "built", chat: [], concepts: [], versions: [v1], current_version: 1 },
      ctx: { pid: "p_test", share: "tok_test" },
      statuses: {},
      readOnly: true,
      turn: null,
    });
    let html = "";
    try {
      html = renderToStaticMarkup(createElement(PreviewTab, { version: v1 as any, onTab: () => undefined }));
    } finally {
      Object.assign(init, saved);
    }
    const labels = [...html.matchAll(/<label class="check[^"]*"><input[^>]*\/?>([^<]+)<\/label>/g)].map((m) => m[1]);
    expect(labels).toEqual(["Exploded view", "Separate parts", "Transparent", "Dimensions", "Size reference", "Section view", "Model parts"]);
    expect(html).not.toContain("section-ctl");
  });

  it("renders the axis buttons, the position slider and Flip with their pressed states", () => {
    const html = renderToStaticMarkup(createElement(SectionControls, { value: { axis: "z", t: 0.4, flip: true }, onChange: () => undefined }));
    const buttons = [...html.matchAll(/<button[^>]*>([^<]*)<\/button>/g)].map((m) => ({ tag: m[0], text: m[1] }));
    expect(buttons.map((b) => b.text)).toEqual(["X", "Y", "Z", "Flip"]);
    expect(buttons.map((b) => /aria-pressed="true"/.test(b.tag))).toEqual([false, false, true, true]);
    expect(buttons[2].tag).toContain('aria-label="Cut across the height (Z)"');
    expect(html).toMatch(/<input type="range" min="0" max="1" step="0.01"[^>]*value="0.4"/);
    expect(html).toContain('aria-label="Section position"');
    expect(html).toContain('aria-valuetext="Cut at 40% of the height, flipped"');
    expect(html).toContain(">40%<");
    const off = renderToStaticMarkup(createElement(SectionControls, { value: SECTION_DEFAULT, onChange: () => undefined, disabled: true }));
    expect(off.match(/disabled=""/g)?.length).toBe(5);
  });
});
