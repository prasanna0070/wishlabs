// The GLB viewer used by Preview, Build (assembly + wiring), PCB 3D and concept previews.
// Scene units are millimetres, +Y up (glTF). Gadget coordinates (mm, +Z up, front -Y) map to the scene as
// (x, y, z) -> (x, z, -y). GLBs in metres (glTF standard) are detected by size and scaled by 1000.
import { Component, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Canvas, useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { ContactShadows, Environment, Grid, Html, Lightformer, OrbitControls, useGLTF } from "@react-three/drei";
import * as THREE from "three";
import type { CableRoute, Camera } from "../lib/types";

export interface ViewerHandle {
  zoom: (factor: number) => void;
  reset: () => void;
}

export interface PartInfo {
  name: string;
  kind: string;
  size: [number, number, number]; // W x D x H in mm (gadget axes)
}

export interface ModelInfo {
  parts: PartInfo[];
  size: [number, number, number] | null; // assembly W x D x H mm
}

export interface Label {
  id: string;
  at: number[]; // gadget mm
  title: string;
  sub?: string;
}

export type SizeRef = "none" | "auto" | "apple" | "person" | "toy";

export interface ViewerProps {
  url: string | null;
  bodyColor?: string | null;
  highlight?: string[] | null;
  isolate?: boolean;
  hidden?: Set<string>;
  hideKinds?: string[];
  xray?: boolean;
  explode?: number;
  separate?: boolean;
  showBox?: boolean;
  sizeRef?: SizeRef;
  camera?: Camera | null;
  labels?: Label[] | null;
  partLabels?: boolean;
  partLabel?: (name: string) => { title: string; sub?: string };
  cables?: (CableRoute & { highlight?: boolean })[] | null;
  selectable?: boolean;
  selected?: string | null;
  onSelect?: (name: string | null) => void;
  onInfo?: (info: ModelInfo) => void;
  handle?: React.MutableRefObject<ViewerHandle | null>;
  autoFlat?: boolean;
  floor?: "grid" | "none";
  shadow?: boolean;
  className?: string;
  children?: ReactNode;
  ariaLabel?: string;
}

const KIND_RE = /^(PRINT|PURCHASE|REFERENCE|TEST)_/;
const PRIMARY = new THREE.Color("#3046D9");
const GHOST = new THREE.Color("#C9C6BE");
const WHITE = new THREE.Color("#FFFFFF");

interface Comp {
  name: string;
  kind: string;
  node: THREE.Object3D;
  meshes: THREE.Mesh[];
  baseLocal: THREE.Vector3;
  box: THREE.Box3; // world box, assembled
  target: THREE.Vector3; // local target position
}

interface Prepared {
  root: THREE.Group;
  comps: Comp[];
  offset: THREE.Vector3;
  box: THREE.Box3; // assembled, non-reference
}

export const g2s = (p: number[] | undefined | null, offset?: THREE.Vector3) =>
  new THREE.Vector3(p?.[0] ?? 0, p?.[2] ?? 0, -(p?.[1] ?? 0)).add(offset || new THREE.Vector3());

function kindOf(name: string): string {
  const m = KIND_RE.exec(name);
  return m ? m[1] : "OTHER";
}

function prepare(scene: THREE.Object3D, autoFlat: boolean): Prepared {
  const inner = scene.clone(true);
  inner.traverse((o) => {
    const m = o as THREE.Mesh;
    if (m.isMesh) {
      const clone = (mat: THREE.Material) => {
        const c = mat.clone() as THREE.MeshStandardMaterial;
        if ("roughness" in c && c.roughness > 0.9) c.roughness = 0.62;
        if ("metalness" in c && c.metalness > 0.6 && !/PURCHASE|REFERENCE/.test(o.name)) c.metalness = 0.1;
        c.userData.base = {
          color: (c.color ? c.color.clone() : new THREE.Color("#cccccc")) as THREE.Color,
          opacity: c.opacity,
          transparent: c.transparent,
          emissive: c.emissive ? c.emissive.clone() : null,
        };
        return c;
      };
      m.material = Array.isArray(m.material) ? m.material.map(clone) : clone(m.material);
      if (m.geometry && !m.geometry.attributes.normal) m.geometry.computeVertexNormals();
    }
  });
  const root = new THREE.Group();
  root.add(inner);
  root.updateMatrixWorld(true);
  let box = new THREE.Box3().setFromObject(inner);
  const size = box.getSize(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z) || 1;
  if (maxDim < 5) inner.scale.multiplyScalar(1000); // metres -> mm
  if (autoFlat) {
    root.updateMatrixWorld(true);
    const s = new THREE.Box3().setFromObject(inner).getSize(new THREE.Vector3());
    const thin = Math.min(s.x, s.y, s.z);
    if (thin === s.z && s.z < s.y) inner.rotateX(-Math.PI / 2);
    else if (thin === s.x && s.x < s.y) inner.rotateZ(Math.PI / 2);
  }
  root.updateMatrixWorld(true);
  box = new THREE.Box3().setFromObject(inner);
  const c = box.getCenter(new THREE.Vector3());
  const offset = new THREE.Vector3(autoFlat ? -c.x : 0, Math.abs(box.min.y) > 0.5 || autoFlat ? -box.min.y : 0, autoFlat ? -c.z : 0);
  inner.position.add(offset);
  root.updateMatrixWorld(true);

  // component nodes: named PRINT_/PURCHASE_/REFERENCE_/TEST_, outermost wins
  let comps: Comp[] = [];
  const isComp = (o: THREE.Object3D) => KIND_RE.test(o.name);
  inner.traverse((o) => {
    if (!isComp(o)) return;
    let p = o.parent;
    while (p) {
      if (isComp(p)) return;
      p = p.parent;
    }
    comps.push(makeComp(o, o.name));
  });
  if (!comps.length) {
    // no component names: every mesh is a part
    let i = 0;
    inner.traverse((o) => {
      if ((o as THREE.Mesh).isMesh) comps.push(makeComp(o, o.name || `Part ${++i}`));
    });
  }
  // merge duplicates of the same name (exporters sometimes split a component into siblings)
  const byName = new Map<string, Comp>();
  for (const cp of comps) {
    const ex = byName.get(cp.name);
    if (ex && ex.node !== cp.node) {
      cp.name = `${cp.name}#${cp.node.id}`;
    }
    byName.set(cp.name, cp);
  }
  comps = [...byName.values()];
  const all = new THREE.Box3();
  for (const cp of comps) if (cp.kind !== "REFERENCE" && !cp.box.isEmpty()) all.union(cp.box);
  return { root, comps, offset, box: all.isEmpty() ? new THREE.Box3().setFromObject(inner) : all };
}

function makeComp(node: THREE.Object3D, name: string): Comp {
  const meshes: THREE.Mesh[] = [];
  node.traverse((o) => {
    if ((o as THREE.Mesh).isMesh) meshes.push(o as THREE.Mesh);
  });
  return {
    name,
    kind: kindOf(name),
    node,
    meshes,
    baseLocal: node.position.clone(),
    box: new THREE.Box3().setFromObject(node),
    target: node.position.clone(),
  };
}

function sizeOf(b: THREE.Box3): [number, number, number] {
  const s = b.getSize(new THREE.Vector3());
  return [round1(s.x), round1(s.z), round1(s.y)]; // W (x), D (scene z = gadget y), H (scene y = gadget z)
}
const round1 = (x: number) => Math.round(x * 10) / 10;

function forEachMat(m: THREE.Mesh, fn: (mat: THREE.MeshStandardMaterial) => void) {
  const mats = Array.isArray(m.material) ? m.material : [m.material];
  for (const x of mats) fn(x as THREE.MeshStandardMaterial);
}

// ---------------------------------------------------------------- model

function Model(props: ViewerProps & { onPrepared: (p: Prepared) => void; onExtent?: (b: THREE.Box3 | null) => void }) {
  const { url, autoFlat = false } = props;
  const gltf = useGLTF(url!, false) as any;
  const prep = useMemo(() => prepare(gltf.scene, autoFlat), [gltf.scene, autoFlat]);
  const [hover, setHover] = useState<string | null>(null);
  const selBox = useRef<THREE.Box3Helper | null>(null);
  const invalidate = useThree((s) => s.invalidate);

  useEffect(() => {
    props.onPrepared(prep);
    props.onInfo?.({
      parts: prep.comps.map((c) => ({ name: c.name, kind: c.kind, size: sizeOf(c.box) })),
      size: prep.box.isEmpty() ? null : sizeOf(prep.box),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prep]);

  // look: colours, ghosting, x-ray, visibility
  const hl = useMemo(() => (props.highlight && props.highlight.length ? new Set(props.highlight) : null), [props.highlight]);
  useEffect(() => {
    const body = props.bodyColor ? new THREE.Color(props.bodyColor) : null;
    for (const c of prep.comps) {
      const hiddenKind = props.hideKinds?.includes(c.kind);
      c.node.visible = !props.hidden?.has(c.name) && !hiddenKind && !(props.separate && c.kind === "REFERENCE");
      const state = hl ? (hl.has(c.name) ? "hl" : "ghost") : "normal";
      const selected = props.selected === c.name || (props.selectable && hover === c.name);
      for (const m of c.meshes) {
        forEachMat(m, (mat) => {
          const b = mat.userData.base;
          if (!b) return;
          let col: THREE.Color = b.color;
          if (body && c.kind === "PRINT") col = body;
          let opacity = b.opacity;
          let transparent = b.transparent;
          let depthWrite = true;
          if (state === "ghost") {
            col = col.clone().lerp(GHOST, 0.65);
            opacity = props.isolate ? 0.07 : 0.16;
            transparent = true;
            depthWrite = false;
          } else if (props.xray && c.kind === "PRINT" && state !== "hl") {
            col = col.clone().lerp(WHITE, 0.55);
            opacity = 0.2;
            transparent = true;
            depthWrite = false;
          }
          if (mat.transparent !== transparent) mat.needsUpdate = true;
          mat.color.copy(col);
          mat.opacity = opacity;
          mat.transparent = transparent;
          mat.depthWrite = depthWrite;
          if (mat.emissive) {
            if (selected) mat.emissive.copy(PRIMARY).multiplyScalar(props.selected === c.name ? 0.07 : 0.04);
            else if (state === "hl" && hl && hl.size < prep.comps.length) mat.emissive.copy(PRIMARY).multiplyScalar(0.06);
            else if (b.emissive) mat.emissive.copy(b.emissive);
          }
          m.renderOrder = transparent ? 2 : 0;
          m.castShadow = state !== "ghost";
        });
      }
    }
    invalidate();
  }, [prep, hl, props.bodyColor, props.hidden, props.hideKinds, props.xray, props.isolate, props.separate, props.selected, props.selectable, hover, invalidate]);

  // positions: explode / separate
  useEffect(() => {
    const center = prep.box.getCenter(new THREE.Vector3());
    const size = prep.box.getSize(new THREE.Vector3());
    const maxDim = Math.max(size.x, size.y, size.z) || 100;
    const biggest = prep.comps
      .filter((c) => c.kind === "PRINT")
      .reduce<Comp | null>((a, c) => (!a || c.box.getSize(new THREE.Vector3()).lengthSq() > a.box.getSize(new THREE.Vector3()).lengthSq() ? c : a), null);
    const slots = props.separate ? layout(prep.comps.filter((c) => c.kind !== "REFERENCE"), maxDim) : null;
    const ext = new THREE.Box3();
    for (const c of prep.comps) {
      const delta = new THREE.Vector3();
      if (slots) {
        const s = slots.get(c.name);
        if (s) delta.copy(s);
      } else if ((props.explode ?? 0) > 0 && c !== biggest) {
        const cc = c.box.getCenter(new THREE.Vector3());
        const dir = cc.clone().sub(center);
        if (dir.length() < maxDim * 0.05) dir.set(0, 1, 0);
        // push mostly along the dominant axis so parts slide out like an exploded drawing
        const ax = Math.abs(dir.x) / size.x, ay = Math.abs(dir.y) / size.y, az = Math.abs(dir.z) / size.z;
        const dom = new THREE.Vector3(ax >= ay && ax >= az ? Math.sign(dir.x) : 0, ay > ax && ay >= az ? Math.sign(dir.y) : 0, az > ax && az > ay ? Math.sign(dir.z) : 0);
        dom.multiplyScalar(0.75).add(dir.normalize().multiplyScalar(0.25)).normalize();
        const k = c.kind === "PRINT" ? 0.9 : 0.55;
        delta.copy(dom.multiplyScalar(props.explode! * maxDim * k));
      }
      if (delta.lengthSq() === 0) {
        c.target.copy(c.baseLocal);
        if (c.kind !== "REFERENCE" || !slots) ext.union(c.box);
        continue;
      }
      if (c.kind !== "REFERENCE" || !slots) ext.union(c.box.clone().translate(delta));
      // world delta -> parent-local target
      const parent = c.node.parent!;
      const worldBase = parent.localToWorld(c.baseLocal.clone());
      c.target.copy(parent.worldToLocal(worldBase.add(delta)));
    }
    props.onExtent?.(slots || (props.explode ?? 0) > 0 ? ext : null);
    invalidate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prep, props.explode, props.separate]);

  useFrame((_, dt) => {
    // frameloop is "demand": keep asking for frames only while parts are still moving
    const k = 1 - Math.exp(-Math.min(dt, 0.1) * 9);
    let moving = false;
    for (const c of prep.comps) if (!c.node.position.equals(c.target)) {
      c.node.position.lerp(c.target, k);
      if (c.node.position.distanceToSquared(c.target) < 0.0004) c.node.position.copy(c.target);
      else moving = true;
    }
    if (moving) invalidate();
    if (selBox.current) {
      const name = props.selected;
      const comp = name ? prep.comps.find((c) => c.name === name) : null;
      if (comp) {
        selBox.current.visible = true;
        selBox.current.box.setFromObject(comp.node).expandByScalar(1.5);
      } else selBox.current.visible = false;
    }
  });

  const compOf = (o: THREE.Object3D | null): Comp | null => {
    while (o) {
      const c = prep.comps.find((x) => x.node === o);
      if (c) return c;
      o = o.parent;
    }
    return null;
  };

  return (
    <>
      <primitive
        object={prep.root}
        onClick={
          props.selectable
            ? (e: ThreeEvent<MouseEvent>) => {
                e.stopPropagation();
                const c = compOf(e.object);
                if (c && c.node.visible) props.onSelect?.(c.name === props.selected ? null : c.name);
              }
            : undefined
        }
        onPointerMove={
          props.selectable
            ? (e: ThreeEvent<PointerEvent>) => {
                e.stopPropagation();
                const c = compOf(e.object);
                setHover(c?.name || null);
                document.body.style.cursor = c ? "pointer" : "";
              }
            : undefined
        }
        onPointerOut={
          props.selectable
            ? () => {
                setHover(null);
                document.body.style.cursor = "";
              }
            : undefined
        }
      />
      <box3Helper ref={selBox as any} args={[new THREE.Box3(), new THREE.Color("#3046D9")]} visible={false} />
      {props.partLabels && hl ? <PartCallouts prep={prep} names={[...hl]} partLabel={props.partLabel} /> : null}
      {props.labels?.length ? <Callouts labels={props.labels} offset={prep.offset} /> : null}
      {props.cables?.length ? <Cables routes={props.cables} offset={prep.offset} dim={!!hl} /> : null}
      {props.showBox ? <DimBox prep={prep} hidden={props.hidden} /> : null}
      {props.sizeRef && props.sizeRef !== "none" ? <SizeReference box={prep.box} kind={props.sizeRef} /> : null}
    </>
  );
}

function layout(comps: Comp[], maxDim: number): Map<string, THREE.Vector3> {
  // lay parts flat on the floor in rows, biggest first, keeping their orientation
  const items = comps
    .map((c) => ({ c, s: c.box.getSize(new THREE.Vector3()), ctr: c.box.getCenter(new THREE.Vector3()) }))
    .sort((a, b) => b.s.x * b.s.z - a.s.x * a.s.z);
  const gap = Math.max(10, maxDim * 0.08);
  const totalW = items.reduce((a, i) => a + i.s.x + gap, 0);
  const rowW = Math.max(maxDim * 1.6, Math.sqrt(items.reduce((a, i) => a + (i.s.x + gap) * (i.s.z + gap), 0)) * 1.5, Math.min(totalW, maxDim * 2.2));
  const out = new Map<string, THREE.Vector3>();
  let x = 0, z = 0, rowD = 0;
  const placed: { name: string; x: number; z: number; it: (typeof items)[number] }[] = [];
  for (const it of items) {
    if (x > 0 && x + it.s.x > rowW) {
      x = 0;
      z += rowD + gap;
      rowD = 0;
    }
    placed.push({ name: it.c.name, x: x + it.s.x / 2, z: z + it.s.z / 2, it });
    x += it.s.x + gap;
    rowD = Math.max(rowD, it.s.z);
  }
  const W = Math.max(...placed.map((p) => p.x + p.it.s.x / 2));
  const D = z + rowD;
  for (const p of placed) {
    // centre in the slot, bottom on the floor (y = 0)
    out.set(p.name, new THREE.Vector3(p.x - W / 2 - p.it.ctr.x, -p.it.c.box.min.y, p.z - D / 2 - p.it.ctr.z));
  }
  return out;
}

// ---------------------------------------------------------------- overlays

function Callouts({ labels, offset }: { labels: Label[]; offset: THREE.Vector3 }) {
  return (
    <>
      {labels.map((l, i) => (
        <Html key={l.id} position={g2s(l.at, offset)} zIndexRange={[20, 0]} style={{ pointerEvents: "none" }}>
          <Callout title={l.title} sub={l.sub} side={i % 2 ? "left" : "right"} rise={28 + (i % 3) * 22} />
        </Html>
      ))}
    </>
  );
}

function PartCallouts({ prep, names, partLabel }: { prep: Prepared; names: string[]; partLabel?: (n: string) => { title: string; sub?: string } }) {
  const comps = prep.comps.filter((c) => names.includes(c.name)).slice(0, 8);
  const [, force] = useState(0);
  const moving = useRef(false);
  useFrame(() => {
    // keep anchors following parts while they animate (and once more when they stop)
    const m = comps.some((c) => !c.node.position.equals(c.target));
    if (m || moving.current) force((v) => v + 1);
    moving.current = m;
  });
  return (
    <>
      {comps.map((c, i) => {
        const b = new THREE.Box3().setFromObject(c.node);
        const at = b.getCenter(new THREE.Vector3());
        at.y = b.max.y;
        const lab = partLabel ? partLabel(c.name) : { title: c.name };
        return (
          <Html key={c.name} position={at} zIndexRange={[20, 0]} style={{ pointerEvents: "none" }}>
            <Callout title={lab.title} sub={lab.sub} side={i % 2 ? "left" : "right"} rise={30 + (i % 3) * 26} />
          </Html>
        );
      })}
    </>
  );
}

export function Callout({ title, sub, side, rise }: { title: string; sub?: string; side: "left" | "right"; rise: number }) {
  return (
    <div className={`callout3d ${side}`} style={{ ["--rise" as any]: `${rise}px` }}>
      <span className="dot" />
      <span className="leader" />
      <div className="card3d">
        <div className="t">{title}</div>
        {sub ? <div className="s">{sub}</div> : null}
      </div>
    </div>
  );
}

function Cables({ routes, offset, dim }: { routes: (CableRoute & { highlight?: boolean })[]; offset: THREE.Vector3; dim: boolean }) {
  const tubes = useMemo(
    () =>
      routes
        .filter((r) => r.points && r.points.length >= 2)
        .map((r, i) => {
          const pts = r.points.map((p) => g2s(p, offset));
          const curve = new THREE.CatmullRomCurve3(pts, false, "centripetal");
          const radius = Math.max(0.8, (r.diameter || r.diameterEnvelopeMm || 3) / 2);
          const geo = new THREE.TubeGeometry(curve, Math.min(400, pts.length * 12), radius, 10, false);
          return { key: r.id || `cable-${i}`, geo, color: r.color || "#3046D9", hl: r.highlight !== false };
        }),
    [routes, offset],
  );
  useEffect(() => () => tubes.forEach((t) => t.geo.dispose()), [tubes]);
  return (
    <>
      {tubes.map((t) => (
        <mesh key={t.key} geometry={t.geo} renderOrder={3}>
          <meshStandardMaterial color={t.color} roughness={0.45} transparent={dim && !t.hl} opacity={dim && !t.hl ? 0.2 : 1} emissive={t.color} emissiveIntensity={t.hl ? 0.15 : 0} />
        </mesh>
      ))}
    </>
  );
}

function DimBox({ prep, hidden }: { prep: Prepared; hidden?: Set<string> }) {
  const box = useMemo(() => {
    const b = new THREE.Box3();
    for (const c of prep.comps) if (c.kind !== "REFERENCE" && !hidden?.has(c.name)) b.union(c.box);
    return b.isEmpty() ? prep.box : b;
  }, [prep, hidden]);
  const s = box.getSize(new THREE.Vector3());
  const min = box.min, max = box.max;
  const fmt = (v: number) => `${Math.round(v * 10) / 10} mm`;
  return (
    <>
      <box3Helper args={[box, new THREE.Color("#3046D9")]} />
      <Html position={[(min.x + max.x) / 2, min.y, max.z]} center zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
        <span className="dimtag">W {fmt(s.x)}</span>
      </Html>
      <Html position={[max.x, min.y, (min.z + max.z) / 2]} center zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
        <span className="dimtag">D {fmt(s.z)}</span>
      </Html>
      <Html position={[max.x, (min.y + max.y) / 2, max.z]} center zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
        <span className="dimtag">H {fmt(s.y)}</span>
      </Html>
    </>
  );
}

function SizeReference({ box, kind }: { box: THREE.Box3; kind: SizeRef }) {
  const s = box.getSize(new THREE.Vector3());
  const k = kind === "auto" ? (s.y > 450 ? "person" : "apple") : kind;
  const x = box.max.x + (k === "person" ? 260 : 70);
  const z = (box.min.z + box.max.z) / 2;
  const apple = useMemo(() => {
    const pts: THREE.Vector2[] = [];
    for (let i = 0; i <= 24; i++) {
      const t = (i / 24) * Math.PI;
      const r = 40 * Math.sin(t) * (1 - 0.12 * Math.cos(2 * t)) + (i === 0 || i === 24 ? 0 : 0.0);
      const y = 37 - 37 * Math.cos(t) - 6 * Math.sin(t) * Math.sin(t) * (i < 12 ? 0.2 : 0);
      pts.push(new THREE.Vector2(Math.max(0.01, r), y));
    }
    return new THREE.LatheGeometry(pts, 48);
  }, []);
  if (k === "apple") {
    return (
      <group position={[x, 0, z]}>
        <mesh geometry={apple} castShadow>
          <meshStandardMaterial color="#C8372D" roughness={0.38} />
        </mesh>
        <mesh position={[0, 78, 0]} rotation={[0, 0, 0.25]}>
          <cylinderGeometry args={[1.4, 1.8, 14, 8]} />
          <meshStandardMaterial color="#5B3A1E" roughness={0.8} />
        </mesh>
        <mesh position={[7, 80, 0]} rotation={[0, 0, -0.9]} scale={[1, 0.35, 0.6]}>
          <sphereGeometry args={[8, 16, 12]} />
          <meshStandardMaterial color="#4E8A3A" roughness={0.6} />
        </mesh>
        <Html position={[0, 0, 60]} center style={{ pointerEvents: "none" }}>
          <span className="dimtag">Apple, about 80 mm</span>
        </Html>
      </group>
    );
  }
  if (k === "toy") {
    return (
      <group position={[x, 0, z]} scale={0.1}>
        <Person />
      </group>
    );
  }
  return (
    <group position={[x, 0, z]}>
      <Person />
      <Html position={[0, 0, 220]} center style={{ pointerEvents: "none" }}>
        <span className="dimtag">Person, 1.75 m</span>
      </Html>
    </group>
  );
}

function Person() {
  const mat = <meshStandardMaterial color="#B9B5AB" roughness={0.75} />;
  return (
    <group>
      <mesh position={[0, 1640, 0]} castShadow>
        <sphereGeometry args={[105, 32, 24]} />
        {mat}
      </mesh>
      <mesh position={[0, 1180, 0]} castShadow>
        <capsuleGeometry args={[175, 420, 8, 24]} />
        {mat}
      </mesh>
      <mesh position={[-95, 440, 0]} castShadow>
        <capsuleGeometry args={[70, 760, 8, 16]} />
        {mat}
      </mesh>
      <mesh position={[95, 440, 0]} castShadow>
        <capsuleGeometry args={[70, 760, 8, 16]} />
        {mat}
      </mesh>
      <mesh position={[-250, 1120, 0]} rotation={[0, 0, 0.12]} castShadow>
        <capsuleGeometry args={[52, 620, 8, 16]} />
        {mat}
      </mesh>
      <mesh position={[250, 1120, 0]} rotation={[0, 0, -0.12]} castShadow>
        <capsuleGeometry args={[52, 620, 8, 16]} />
        {mat}
      </mesh>
    </group>
  );
}

// ---------------------------------------------------------------- camera

function CameraRig({ prep, camera, handle, sizeRef, extent }: { prep: Prepared | null; camera?: Camera | null; handle?: React.MutableRefObject<ViewerHandle | null>; sizeRef?: SizeRef; extent?: THREE.Box3 | null }) {
  const { camera: cam, controls, invalidate } = useThree() as any;
  const goal = useRef<{ pos: THREE.Vector3; target: THREE.Vector3; t: number } | null>(null);
  const home = useRef<{ pos: THREE.Vector3; target: THREE.Vector3 } | null>(null);

  const fit = (box: THREE.Box3) => {
    const c = box.getCenter(new THREE.Vector3());
    const s = box.getSize(new THREE.Vector3());
    const r = Math.max(s.x, s.y, s.z, 20);
    const fov = ((cam as THREE.PerspectiveCamera).fov * Math.PI) / 180;
    const dist = (r / 2 / Math.tan(fov / 2)) * 1.75;
    const dir = new THREE.Vector3(0.48, 0.36, 1).normalize();
    return { pos: c.clone().add(dir.multiplyScalar(dist)), target: c.clone().setY(c.y * 0.92) };
  };

  useEffect(() => {
    if (!prep) return;
    let view: { pos: THREE.Vector3; target: THREE.Vector3 };
    if (camera?.position && camera?.target) {
      view = { pos: g2s(camera.position, prep.offset), target: g2s(camera.target, prep.offset) };
    } else {
      const b = (extent && !extent.isEmpty() ? extent : prep.box).clone();
      if (sizeRef && sizeRef !== "none") {
        const s = b.getSize(new THREE.Vector3());
        const k = sizeRef === "auto" ? (s.y > 450 ? "person" : "apple") : sizeRef;
        if (k === "person") b.union(new THREE.Box3(new THREE.Vector3(b.max.x, 0, b.min.z), new THREE.Vector3(b.max.x + 520, 1760, b.max.z)));
        else b.union(new THREE.Box3(new THREE.Vector3(b.max.x, 0, b.min.z), new THREE.Vector3(b.max.x + 120, 90, b.max.z)));
      }
      view = fit(b);
    }
    const near = Math.max(0.5, prep.box.getSize(new THREE.Vector3()).length() / 400);
    (cam as THREE.PerspectiveCamera).near = near;
    (cam as THREE.PerspectiveCamera).far = 60000;
    cam.updateProjectionMatrix();
    home.current = view;
    if (!goal.current && !(cam as any).userData.placed) {
      cam.position.copy(view.pos);
      controls?.target.copy(view.target);
      (cam as any).userData.placed = true;
      controls?.update();
    } else goal.current = { ...view, t: 0 };
    invalidate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prep, camera?.position?.join(","), camera?.target?.join(","), sizeRef, controls, extent ? extent.min.toArray().concat(extent.max.toArray()).map((x) => Math.round(x)).join(",") : ""]);

  useEffect(() => {
    if (!handle) return;
    handle.current = {
      zoom: (f) => {
        if (!controls) return;
        const t = controls.target as THREE.Vector3;
        const d = cam.position.clone().sub(t).multiplyScalar(f);
        goal.current = { pos: t.clone().add(d), target: t.clone(), t: 0 };
        invalidate();
      },
      reset: () => {
        if (home.current) goal.current = { pos: home.current.pos.clone(), target: home.current.target.clone(), t: 0 };
        invalidate();
      },
    };
  }, [handle, controls, cam, invalidate]);

  useFrame((_, dt) => {
    const g = goal.current;
    if (!g || !controls) return;
    g.t = Math.min(1, g.t + dt * 2.2);
    const k = 1 - Math.pow(1 - g.t, 3);
    cam.position.lerp(g.pos, k * 0.35 + 0.05);
    (controls.target as THREE.Vector3).lerp(g.target, k * 0.35 + 0.05);
    controls.update();
    if (g.t >= 1 && cam.position.distanceTo(g.pos) < 0.5) goal.current = null;
    else invalidate();
  });
  return null;
}

// ---------------------------------------------------------------- shell

class Boundary extends Component<{ children: ReactNode; onError: (e: any) => void; resetKey: any }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(e: any) {
    this.props.onError(e);
  }
  componentDidUpdate(prev: any) {
    if (prev.resetKey !== this.props.resetKey && this.state.failed) this.setState({ failed: false });
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

function Studio({ floorY = 0, extent = 400, shadow = true, floor = "grid" }: { floorY?: number; extent?: number; shadow?: boolean; floor?: string }) {
  return (
    <>
      <hemisphereLight args={["#ffffff", "#d8d3c6", 1.05]} />
      <directionalLight position={[extent * 0.9, extent * 1.6, extent * 1.2]} intensity={1.55} />
      <directionalLight position={[-extent * 1.2, extent * 0.8, extent * 0.6]} intensity={0.55} />
      <directionalLight position={[0, extent * 0.6, -extent * 1.4]} intensity={0.45} />
      <Environment resolution={128} frames={1}>
        <Lightformer form="rect" intensity={1.6} position={[0, 5, -9]} scale={[10, 5, 1]} color="#ffffff" />
        <Lightformer form="rect" intensity={1.1} position={[-5, 1, -1]} rotation-y={Math.PI / 2} scale={[20, 2, 1]} />
        <Lightformer form="rect" intensity={1.1} position={[5, 1, -1]} rotation-y={-Math.PI / 2} scale={[20, 2, 1]} />
        <Lightformer form="ring" intensity={0.8} position={[0, 6, 4]} scale={4} />
      </Environment>
      {floor === "grid" ? (
        <Grid
          position={[0, floorY + 0.02, 0]}
          infiniteGrid
          cellSize={10}
          sectionSize={50}
          cellThickness={0.55}
          sectionThickness={0.9}
          cellColor="#D3CFC5"
          sectionColor="#C6C1B5"
          fadeDistance={Math.max(1600, extent * 7)}
          fadeStrength={1.6}
          followCamera={false}
        />
      ) : null}
      {shadow ? <ContactShadows position={[0, floorY + 0.05, 0]} scale={extent * 3.2} resolution={1024} blur={2.6} far={extent * 1.2} opacity={0.42} color="#33302A" /> : null}
    </>
  );
}

export function Viewer(props: ViewerProps) {
  const [prep, setPrep] = useState<Prepared | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [extent, setExtent] = useState<THREE.Box3 | null>(null);
  useEffect(() => {
    setPrep(null);
    setExtent(null);
    setError(null);
    setReady(false);
    const u = props.url;
    // drafts and tweak previews change URL on every revision: drop them from drei's GLB cache when superseded
    return () => {
      if (u && /[?&](draft|rev)=|\/previews\//.test(u)) useGLTF.clear(u);
    };
  }, [props.url]);
  const size = prep ? Math.max(...prep.box.getSize(new THREE.Vector3()).toArray(), 60) : 200;
  return (
    <div className={`viewer ${props.className || ""}`} data-ready={ready ? "1" : "0"}>
      {props.url ? (
        <Canvas
          frameloop="demand"
          dpr={[1, 2]}
          gl={{ antialias: true, alpha: true, toneMapping: THREE.NeutralToneMapping, preserveDrawingBuffer: true }}
          camera={{ fov: 30, position: [300, 260, 600], near: 1, far: 60000 }}
          onPointerMissed={() => props.selectable && props.onSelect?.(null)}
          aria-label={props.ariaLabel || "Interactive model preview"}
          role="img"
        >
          <Studio extent={size} shadow={props.shadow !== false} floor={props.floor || "grid"} />
          <Boundary onError={(e) => setError(String(e?.message || e))} resetKey={props.url}>
            <Suspense fallback={null}>
              <Model
                {...props}
                onExtent={setExtent}
                onPrepared={(p) => {
                  setPrep(p);
                  setReady(true);
                }}
              />
            </Suspense>
          </Boundary>
          <OrbitControls makeDefault enableDamping dampingFactor={0.12} minDistance={5} maxDistance={40000} maxPolarAngle={Math.PI * 0.495} />
          <CameraRig prep={prep} camera={props.camera} handle={props.handle} sizeRef={props.sizeRef} extent={extent} />
        </Canvas>
      ) : null}
      {props.url && !ready && !error ? (
        <div className="viewer-status">
          <span className="spinner" /> Loading model…
        </div>
      ) : null}
      {error ? (
        <div className="viewer-status error">
          {/404|not found/i.test(error) ? "This version has no 3D model file yet." : `The 3D model could not be loaded.${error.length < 120 ? ` ${error}` : ""}`}
        </div>
      ) : null}
      {props.children}
    </div>
  );
}

export function ViewerControls({ handle, children }: { handle: React.MutableRefObject<ViewerHandle | null>; children?: ReactNode }) {
  return (
    <div className="viewer-ctl" role="toolbar" aria-label="3D view controls">
      {children}
      <button className="icon-btn sm" aria-label="Zoom in" title="Zoom in" onClick={() => handle.current?.zoom(0.78)}>
        <svg width="16" height="16" viewBox="0 0 16 16"><path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
      </button>
      <button className="icon-btn sm" aria-label="Zoom out" title="Zoom out" onClick={() => handle.current?.zoom(1.28)}>
        <svg width="16" height="16" viewBox="0 0 16 16"><path d="M3 8h10" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
      </button>
      <button className="icon-btn sm" aria-label="Reset 3D view" title="Reset 3D view" onClick={() => handle.current?.reset()}>
        <svg width="16" height="16" viewBox="0 0 16 16"><path d="M3.2 8a4.8 4.8 0 1 0 1.5-3.5M3 2.5v2.8h2.8" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </button>
    </div>
  );
}
