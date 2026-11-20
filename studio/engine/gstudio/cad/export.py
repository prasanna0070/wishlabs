"""File outputs: STEP, GLB, BREP, print STLs, components/anchors/cable-routes JSON, params.json.

GLB convention (docs/REQUESTS.md): glTF metres, +Y up; gadget (x, y, z) mm -> glTF (x/1000, z/1000, -y/1000). One node
per component, node name == component name, one primitive per coloured sub-part, PBR materials. Anchors, cable routes,
components.json bboxes stay in gadget millimetres (Z up).
"""

from __future__ import annotations

import json
import math
import os
import shutil
import struct
import tempfile
from pathlib import Path
from typing import Optional

import cadquery as cq
import numpy as np

from .geom import Frame, V, accurate_volume, bbox, tessellate

METALLIC_NAMES = {"Shield", "Usb1", "Usb2", "Magnet", "CardSocket", "UsbA", "UsbB", "Frame", "Basket", "J_PWR", "HeatSetInserts"}
GLOSSY_NAMES = {"Glass", "Screen", "Diffuser"}


def _srgb_to_linear(c):
    c = float(c)
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _material(name: str, sub: str, color, alpha: float = 1.0) -> dict:
    metallic = 0.0
    rough = 0.62
    if sub in METALLIC_NAMES or name == "PURCHASE_HeatSetInserts":
        metallic, rough = 0.85, 0.35
    elif sub in GLOSSY_NAMES:
        rough = 0.12 if sub != "Diffuser" else 0.55
    elif name.startswith("PURCHASE_") and sub in ("Pcb",):
        rough = 0.45
    elif name.startswith("PRINT_"):
        rough = 0.7
    lin = [_srgb_to_linear(c) for c in color[:3]]
    m = {"name": f"{name}/{sub}", "pbrMetallicRoughness": {"baseColorFactor": lin + [float(alpha)], "metallicFactor": metallic,
                                                           "roughnessFactor": rough}, "doubleSided": False}
    if alpha < 0.999:
        m["alphaMode"] = "BLEND"
    return m


def display_tol(part, shape) -> tuple[float, float]:
    """Display tessellation: relative to the part size, finer for printed shells (curvature reads in renders)."""
    b = shape.BoundingBox()
    diag = math.sqrt(b.xlen ** 2 + b.ylen ** 2 + b.zlen ** 2)
    if part.name.startswith("PRINT_"):
        return float(np.clip(diag / 1800.0, 0.05, 0.14)), 0.26
    if part.name.startswith("REFERENCE_"):
        return 0.2, 0.5
    return float(np.clip(diag / 900.0, 0.04, 0.2)), 0.45


def part_primitives(part, face_colors: Optional[dict] = None, tol: Optional[float] = None, ang: Optional[float] = None) -> list:
    """[(subname, verts, normals, tris, color)] with decor-coloured faces split into their own primitive."""
    prims = []
    for sub, shape, col in part.subparts:
        dt, da = display_tol(part, shape)
        v, n, t, ranges = tessellate(shape, tol or dt, ang or da, fresh=True)
        if not len(t):
            continue
        if face_colors and len(part.subparts) == 1:
            tri_col = np.zeros(len(t), dtype=np.int64)
            keys = {}
            for fi, c in face_colors.items():
                if fi < len(ranges):
                    a, b = ranges[fi]
                    k = keys.setdefault(tuple(round(x, 4) for x in c), len(keys) + 1)
                    tri_col[a:b] = k
            inv = {v_: k_ for k_, v_ in keys.items()}
            for k in sorted(set(tri_col.tolist())):
                sel = t[tri_col == k]
                c = col if k == 0 else inv[k]
                prims.append((sub if k == 0 else f"{sub}Detail{k}", v, n, sel, c))
        else:
            prims.append((sub, v, n, t, col))
    return prims


def idle_face_png(kind: str = "oled", px=(128, 64), scale: int = 4, color=(255, 255, 255)) -> bytes:
    """The firmware's idle ("Neutral") face as a PNG: the same geometry as gsdisp::draw_face in
    firmware/templates/common/src/gs_display.cpp (eye w = W/5, h = H*11/20, gap = W/6, corner = ew/3, on black).
    Pixel colour is illustrative: the library does not say whether the OLED glows white or blue."""
    from io import BytesIO

    from PIL import Image, ImageDraw

    w, h = px
    ew, eh, gap = w // 5, h * 11 // 20, w // 6
    cx, cy = w // 2, h // 2
    lx, rx, top = cx - gap // 2 - ew, cx + gap // 2, cy - eh // 2
    img = Image.new("RGB", (w * scale, h * scale), (0, 0, 0))
    d = ImageDraw.Draw(img)
    r = (ew // 3) * scale
    for x0 in (lx, rx):
        d.rounded_rectangle([x0 * scale, top * scale, (x0 + ew) * scale - 1, (top + eh) * scale - 1], radius=r, fill=tuple(color))
    b = BytesIO()
    img.save(b, format="PNG", optimize=True)
    return b.getvalue()


def screen_quad(sc: dict) -> dict:
    """Textured quad prim for one info.screens entry (gadget mm), slightly in front of the glass."""
    c, u, v, n = (np.asarray(sc[k], float) for k in ("c", "u", "v", "n"))
    w, h = float(sc.get("aa_w") or sc["w"]), float(sc.get("aa_h") or sc["h"])
    cc = c + n * 0.05
    P = np.array([cc - u * w / 2 - v * h / 2, cc + u * w / 2 - v * h / 2, cc + u * w / 2 + v * h / 2, cc - u * w / 2 + v * h / 2])
    N = np.tile(n, (4, 1))
    UV = np.array([[0, 1], [1, 1], [1, 0], [0, 0]], float)
    T = np.array([[0, 1, 2], [0, 2, 3]], np.int64)
    px = (128, 64) if sc.get("kind") != "lcd" else (320, 240)
    return {"sub": "Screen", "verts": P, "normals": N, "tris": T, "uv": UV, "png": _screen_png(sc, px),
            "color": (0.0, 0.0, 0.0), "emissive": True}


def _screen_png(sc: dict, px) -> bytes:
    """The idle face, or a plain readout for a screen marked {"face": False} (pack freeform, cad/screens.py)."""
    if sc.get("face") is False:
        from .screens import readout_png

        return readout_png(sc.get("kind", "oled"), px)
    return idle_face_png(sc.get("kind", "oled"), px)


def write_glb(path: Path, nodes: list, generator: str = "Wish Labs CAD") -> dict:
    """nodes: [(node_name, [(subname, verts, normals, tris, color) | textured dict], alpha[, children])] -> binary glTF 2.0.
    children: [(child_name, prims, alpha)] become child nodes (e.g. PRINT_Shell#decor[1] pick meshes, a display's
    emissive idle-face screen PURCHASE_Display#screen); the parent keeps its full mesh."""
    bin_chunks = []
    offset = 0
    accessors, views, meshes, materials, gnodes = [], [], [], [], []
    images, textures = [], []
    mat_index = {}

    def add_view(data: bytes, target) -> int:
        nonlocal offset
        pad = (-len(data)) % 4
        bin_chunks.append(data + b"\x00" * pad)
        v = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target:
            v["target"] = target
        views.append(v)
        offset += len(data) + pad
        return len(views) - 1

    tri_total = 0

    def mesh_of(name, prims, alpha):
        nonlocal tri_total
        gprims = []
        for pr in prims:
            if isinstance(pr, dict):  # textured emissive quad
                vv = np.asarray(pr["verts"], float)
                nn = np.asarray(pr["normals"], float)
                P = np.stack([vv[:, 0], vv[:, 2], -vv[:, 1]], 1).astype(np.float32) / 1000.0
                N = np.stack([nn[:, 0], nn[:, 2], -nn[:, 1]], 1).astype(np.float32)
                UV = np.asarray(pr["uv"], np.float32)
                I = np.asarray(pr["tris"], np.uint32).ravel()
                pa = len(accessors)
                accessors.append({"bufferView": add_view(P.tobytes(), 34962), "componentType": 5126, "count": len(P), "type": "VEC3",
                                  "min": P.min(0).tolist(), "max": P.max(0).tolist()})
                accessors.append({"bufferView": add_view(N.tobytes(), 34962), "componentType": 5126, "count": len(N), "type": "VEC3"})
                accessors.append({"bufferView": add_view(UV.tobytes(), 34962), "componentType": 5126, "count": len(UV), "type": "VEC2"})
                accessors.append({"bufferView": add_view(I.tobytes(), 34963), "componentType": 5125, "count": len(I), "type": "SCALAR"})
                img_view = add_view(pr["png"], None)
                images.append({"bufferView": img_view, "mimeType": "image/png", "name": f"{name}/{pr['sub']}"})
                textures.append({"sampler": 0, "source": len(images) - 1})
                ti = len(textures) - 1
                materials.append({"name": f"{name}/{pr['sub']}", "pbrMetallicRoughness": {"baseColorFactor": [0.0, 0.0, 0.0, 1.0],
                                  "baseColorTexture": {"index": ti}, "metallicFactor": 0.0, "roughnessFactor": 0.25},
                                  "emissiveTexture": {"index": ti}, "emissiveFactor": [1.0, 1.0, 1.0], "doubleSided": False})
                gprims.append({"attributes": {"POSITION": pa, "NORMAL": pa + 1, "TEXCOORD_0": pa + 2}, "indices": pa + 3,
                               "material": len(materials) - 1, "mode": 4})
                tri_total += len(I) // 3
                continue
            sub, v, n, t, col = pr
            used = np.unique(t)
            remap = -np.ones(len(v), dtype=np.int64)
            remap[used] = np.arange(len(used))
            vv = v[used]
            nn = n[used]
            tt = remap[t]
            P = np.stack([vv[:, 0], vv[:, 2], -vv[:, 1]], 1).astype(np.float32) / 1000.0
            N = np.stack([nn[:, 0], nn[:, 2], -nn[:, 1]], 1).astype(np.float32)
            N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-9)
            I = tt.astype(np.uint32).ravel()
            pv = add_view(P.tobytes(), 34962)
            accessors.append({"bufferView": pv, "componentType": 5126, "count": len(P), "type": "VEC3",
                              "min": P.min(0).tolist(), "max": P.max(0).tolist()})
            pa = len(accessors) - 1
            nv = add_view(N.tobytes(), 34962)
            accessors.append({"bufferView": nv, "componentType": 5126, "count": len(N), "type": "VEC3"})
            na = len(accessors) - 1
            iv = add_view(I.tobytes(), 34963)
            accessors.append({"bufferView": iv, "componentType": 5125, "count": len(I), "type": "SCALAR"})
            ia = len(accessors) - 1
            key = (name.split("_")[0], sub, tuple(round(c, 3) for c in col), round(alpha, 2))
            if key not in mat_index:
                materials.append(_material(name, sub, col, alpha))
                mat_index[key] = len(materials) - 1
            gprims.append({"attributes": {"POSITION": pa, "NORMAL": na}, "indices": ia, "material": mat_index[key], "mode": 4})
            tri_total += len(tt)
        if not gprims:
            return None
        meshes.append({"name": name, "primitives": gprims})
        return len(meshes) - 1

    roots = []
    for item in nodes:
        name, prims, alpha = item[0], item[1], item[2]
        children = item[3] if len(item) > 3 else []
        mi = mesh_of(name, prims, alpha)
        kids = []
        for cname, cprims, calpha in children or []:
            ci = mesh_of(cname, cprims, calpha)
            if ci is None:
                continue
            gnodes.append({"name": cname, "mesh": ci})
            kids.append(len(gnodes) - 1)
        if mi is None and not kids:
            continue
        nd = {"name": name}
        if mi is not None:
            nd["mesh"] = mi
        if kids:
            nd["children"] = kids
        gnodes.append(nd)
        roots.append(len(gnodes) - 1)
    blob = b"".join(bin_chunks)
    doc = {"asset": {"version": "2.0", "generator": generator, "extras": {"units": "metres; gadget mm x/1000, z up -> +Y"}},
           "scene": 0, "scenes": [{"name": "gadget", "nodes": roots}], "nodes": gnodes, "meshes": meshes,
           "materials": materials, "accessors": accessors, "bufferViews": views, "buffers": [{"byteLength": len(blob)}]}
    if images:
        doc["images"] = images
        doc["textures"] = textures
        doc["samplers"] = [{"magFilter": 9729, "minFilter": 9729, "wrapS": 33071, "wrapT": 33071}]
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * ((-len(js)) % 4)
    out = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(blob))
    out += struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(blob), 0x004E4942) + blob
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(out)
    os.replace(tmp, path)
    return {"nodes": len(gnodes), "triangles": tri_total, "bytes": len(out)}


def write_draft_glb(path: Path, outer: cq.Shape, records, colors: dict) -> dict:
    from .build import classify_faces
    from .parts import Part

    p = Part("PRINT_Shell", "PRINT", "draft shell", colors["body"])
    p.add("Shell", outer)
    fc = classify_faces(outer, records, colors)
    return write_glb(path, [("PRINT_Shell", part_primitives(p, fc, tol=0.15, ang=0.35), 1.0)], generator="Wish Labs CAD draft")


def glb_items(path: Path) -> list:
    """Read a GLB written by write_glb back into [(node, sub, verts_mm, normals, tris, rgb, alpha)] (gadget mm, Z up)."""
    import pygltflib

    g = pygltflib.GLTF2().load(str(path))
    blob = g.binary_blob()

    def acc(i, comps):
        a = g.accessors[i]
        bv = g.bufferViews[a.bufferView]
        dt = {5126: np.float32, 5125: np.uint32}[a.componentType]
        arr = np.frombuffer(blob, dtype=dt, count=a.count * comps, offset=(bv.byteOffset or 0) + (a.byteOffset or 0))
        return arr.reshape(a.count, comps) if comps > 1 else arr

    out = []
    for nd in g.nodes:
        if nd.mesh is None or "#" in (nd.name or ""):  # child pick meshes and screen quads: not separate components
            continue
        for k, pr in enumerate(g.meshes[nd.mesh].primitives):
            P = acc(pr.attributes.POSITION, 3).astype(float) * 1000.0
            N = acc(pr.attributes.NORMAL, 3).astype(float)
            I = acc(pr.indices, 1).astype(np.int64).reshape(-1, 3)
            v = np.stack([P[:, 0], -P[:, 2], P[:, 1]], 1)
            n = np.stack([N[:, 0], -N[:, 2], N[:, 1]], 1)
            m = g.materials[pr.material]
            lin = m.pbrMetallicRoughness.baseColorFactor
            srgb = [((c * 12.92) if c <= 0.0031308 else (1.055 * c ** (1 / 2.4) - 0.055)) for c in lin[:3]]
            sub = (m.name or "").split("/")[-1]
            out.append((nd.name, sub, v, n, I, tuple(srgb), float(lin[3])))
    return out


def model_primitives(model) -> list:
    """[(name, prims, alpha)] for every component; cached on the model for the GLB and the renders."""
    cache = getattr(model, "_prims", None)
    if cache is None:
        cache = [(p.name, part_primitives(p, model.face_colors.get(p.name)), p.alpha) for p in model.parts]
        model._prims = cache
    return cache


def decor_pick_prims(model) -> list:
    """Child meshes of PRINT_Shell, one per decor region (PRINT_Shell#decor[i]): the shell's own faces in that region,
    so a viewer raycast can tell the ears or the face plate from the rest of the shell (REVIEW-1 MAJ-35)."""
    regions = getattr(model, "face_regions", None) or {}
    sh = model.part("PRINT_Shell")
    if not regions or sh is None or len(sh.subparts) != 1:
        return []
    _, shape, col = sh.subparts[0]
    dt, da = display_tol(sh, shape)
    v, n, t, ranges = tessellate(shape, dt, da, fresh=False)  # reuse the GLB triangulation just made
    by: dict = {}
    for fi, name in regions.items():
        if fi < len(ranges):
            a, b = ranges[fi]
            by.setdefault(name, []).append(t[a:b])
    out = []
    for name, tl in sorted(by.items()):
        tt = np.vstack(tl) if tl else np.zeros((0, 3), np.int64)
        if len(tt):
            out.append((name, [("Decor", v, n, tt, col)], 1.0))
    return out


def screen_children(model) -> dict:
    """{component name: [(PURCHASE_Display#screen, [textured quad], 1.0)]} from info.screens (REVIEW-1 MAJ-32)."""
    out: dict = {}
    for sc in model.info.get("screens") or []:
        comp = sc.get("component") or "PURCHASE_Display"
        out.setdefault(comp, []).append((f"{comp}#screen", [screen_quad(sc)], 1.0))
    return out


def model_glb(path: Path, model) -> dict:
    kids = screen_children(model)
    picks = decor_pick_prims(model)
    nodes = []
    for name, prims, alpha in model_primitives(model):
        ch = list(kids.get(name, []))
        if name == "PRINT_Shell":
            ch += picks
        nodes.append((name, prims, alpha, ch))
    return write_glb(path, nodes)


def write_screen_pngs(cad_dir: Path, model) -> list:
    """cad/screens/<REF>-idle.png: the idle face image used on the GLB screen quads (for the manual renders too)."""
    out = []
    d = Path(cad_dir) / "screens"
    for sc in model.info.get("screens") or []:
        ref = sc.get("ref") or "DISP1"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{ref}-idle.png"
        px = (128, 64) if sc.get("kind") != "lcd" else (320, 240)
        f.write_bytes(_screen_png(sc, px))
        sc["texture"] = f"cad/screens/{f.name}"
        out.append(str(f))
    return out


# ------------------------------------------------------------------------------------------------ STEP / BREP
def write_step(path: Path, model) -> None:
    assy = cq.Assembly(name=model.params.get("slug", "gadget").replace("-", "_"))
    for p in model.parts:
        if len(p.subparts) == 1:
            sub, s, c = p.subparts[0]
            assy.add(s, name=p.name, color=cq.Color(*c[:3], p.alpha))
        else:
            a = cq.Assembly(name=p.name)
            for sub, s, c in p.subparts:
                a.add(s, name=f"{p.name}__{sub}", color=cq.Color(*c[:3], p.alpha))
            assy.add(a, name=p.name)
    try:
        assy.export(str(path))
    except Exception:
        assy.save(str(path))


def write_breps(dirp: Path, model) -> dict:
    dirp.mkdir(parents=True, exist_ok=True)
    out = {}
    for p in model.parts:
        f = dirp / f"{p.name}.brep"
        p.shape.exportBrep(str(f))
        out[p.name] = str(f)
    return out


# ------------------------------------------------------------------------------------------------ print STLs
def print_orient(part) -> tuple[cq.Shape, str]:
    info = part.print_info or {}
    s = part.shape
    up = info.get("up", "z")
    F: Optional[Frame] = info.get("frame")
    if F is not None:
        s = s.moved(F.loc().inverse)
        if up == "-z":
            s = s.rotate(V(0, 0, 0), V(1, 0, 0), 180)
    elif up == "-y":
        s = s.rotate(V(0, 0, 0), V(1, 0, 0), -90)
    elif up == "y":
        s = s.rotate(V(0, 0, 0), V(1, 0, 0), 90)
    b = s.BoundingBox()
    s = s.translate(V(-(b.xmin + b.xmax) / 2, -(b.ymin + b.ymax) / 2, -b.zmin))
    b = s.BoundingBox()
    if (b.xlen > 250 or b.ylen > 210) and b.ylen <= 250 and b.xlen <= 210:
        s = s.rotate(V(0, 0, 0), V(0, 0, 1), 90)
    return s, info.get("reason", "default")


def _stl(shape, f: Path, tol: float = 0.02, ang: float = 0.1) -> None:
    import trimesh
    from OCP.BRepTools import BRepTools

    BRepTools.Clean_s(shape.wrapped)
    # absolute deflection: CadQuery's default for exportStl is relative, which is coarse on large faces
    shape.exportStl(str(f), tolerance=tol, angularTolerance=ang, ascii=False, relative=False, parallel=True)
    m = trimesh.load_mesh(f)
    changed = False
    nd = m.nondegenerate_faces(height=1e-9)
    if not nd.all():  # OCC's writer can leave zero-area slivers at seams and poles
        m.update_faces(nd)
        m.remove_unreferenced_vertices()
        changed = True
    if not m.is_watertight:
        parts = m.split(only_watertight=False)
        keep = [p for p in parts if p.is_watertight and abs(p.volume) > 1e-3]
        if keep and len(keep) < len(parts) and sum(len(p.faces) for p in keep) > 0.999 * len(m.faces):
            m = trimesh.util.concatenate(keep)
            changed = True
    dz = float(m.bounds[0][2])
    if abs(dz) > 1e-6:
        m.apply_translation([0, 0, -dz])
        changed = True
    if changed:
        m.export(f)


def write_stls(dirp: Path, model) -> dict:
    dirp.mkdir(parents=True, exist_ok=True)
    out = {}
    for p in model.parts:
        if p.kind != "PRINT":
            continue
        s, reason = print_orient(p)
        f = dirp / f"{p.name}.stl"
        _stl(s, f)
        vol = accurate_volume(s)
        p.extra["_volume"] = vol
        out[p.name] = {"file": f"print/{f.name}", "orientation_note": reason, "brep_volume_mm3": vol,
                       "material": (p.print_info or {}).get("material") or ((model.params or {}).get("print_material") or {}).get("name", "PLA")}
    for name, c in model.coupons.items():
        s = c["shape"]
        b = s.BoundingBox()
        s = s.translate(V(-(b.xmin + b.xmax) / 2, -(b.ymin + b.ymax) / 2, -b.zmin))
        f = dirp / f"{name}.stl"
        _stl(s, f)
        out[name] = {"file": f"print/{f.name}", "orientation_note": "flat on the bed as generated", "brep_volume_mm3": accurate_volume(s),
                     "test": True, "features": c.get("features"), "tests": c.get("tests", []), "note": c.get("note")}
    return out


# ------------------------------------------------------------------------------------------------ JSON
def _hex(c):
    return "#" + "".join(f"{int(round(max(0, min(1, x)) * 255)):02X}" for x in c[:3])


def _mesh_bbox(model, name):
    for n, prims, _ in getattr(model, "_prims", None) or []:
        if n == name and prims:
            P = np.vstack([v[np.unique(t)] for _, v, _, t, _ in prims])
            return [float(P[:, 0].min()), float(P[:, 0].max()), float(P[:, 1].min()), float(P[:, 1].max()),
                    float(P[:, 2].min()), float(P[:, 2].max())]
    return None


def components_json(model, prints: dict) -> dict:
    out = {}
    for p in model.parts:
        s = p.shape
        try:
            vol_occ = float(s.Volume())
        except Exception:
            vol_occ = 0.0
        vol = float(p.extra.get("_volume", vol_occ))
        entry = {
            "kind": p.kind, "label": p.label, "ref": p.ref, "host_ref": p.host_ref, "role": p.role, "module_id": p.module_id,
            "feature_id": p.feature_id,
            "spec_path": p.spec_path, "color": [round(float(x), 4) for x in p.color[:3]], "color_hex": _hex(p.color),
            "bbox": [round(float(x), 3) for x in (_mesh_bbox(model, p.name) or bbox(s))], "volume_mm3": round(vol, 2), "valid": bool(s.isValid()),
            "solids": len(s.Solids()), "step": "assembly", "subparts": [n for n, _, _ in p.subparts],
            "mass_g": None if p.mass_g is None else round(float(p.mass_g), 2), "mass_source": p.mass_source,
        }
        if p.alpha < 1:
            entry["opacity"] = p.alpha
        if p.kind == "PRINT" and p.name in prints:
            entry["print"] = {k: prints[p.name][k] for k in ("file", "orientation_note", "material")}
        if p.notes:
            entry["notes"] = p.notes
        entry.update({k: v for k, v in (p.extra or {}).items() if not k.startswith("_")})
        if p.name == "PRINT_Shell" and model.info.get("decor_regions"):
            # click a region -> its spec_path (e.g. decor[3]); GLB child node of the same name (REVIEW-1 MAJ-35)
            entry["decor"] = model.info["decor_regions"]
        if p.kind == "PRINT":
            entry["volume_method"] = "fine tessellation (0.01 mm)"
            entry["volume_occ_gprop_mm3"] = round(vol_occ, 2)
        out[p.name] = entry
    return out


def write_json(path: Path, obj) -> None:
    def conv(o):
        if isinstance(o, np.ndarray):
            return o.tolist()
        if isinstance(o, (np.floating, np.integer)):
            return o.item()
        if isinstance(o, Frame):
            return o.to_json()
        if isinstance(o, tuple):
            return list(o)
        return str(o)

    Path(path).write_text(json.dumps(obj, indent=2, default=conv))


# ------------------------------------------------------------------------------------------------ standalone source
VENDOR = ["geom.py", "shapes2d.py", "probe.py", "body.py", "decor.py", "modules.py", "refs_provisional.py", "parts.py",
          "footprint.py", "features.py", "internals.py", "enclosure.py", "build.py", "cables.py", "export.py", "checks.py"]

SCRIPT = '''#!/usr/bin/env python3
"""{name}: editable parametric CAD source (generated by Wish Labs, engine {engine}).

Everything about this design lives in params.json next to this file: body outline/profile/primitives, decor,
feature positions, resolved module dimensions, backplane layout and fastener sizes. Edit it (or this file) and run

    python cad/gadget_cad.py              # rebuild cad/ (STEP, GLB, BREP, JSON) and print/ (STL) here
    python cad/gadget_cad.py --out DIR    # write into DIR/cad and DIR/print instead
    python cad/gadget_cad.py --no-checks  # skip the digital checks

The geometry code is vendored in cad/gadget_cad/ (no Wish Labs install needed).
Requirements: cad/requirements.txt pins every package it imports, with hashes (Python 3.12, Linux x86_64 wheels):
    python3.12 -m venv .venv && .venv/bin/pip install --require-hashes -r cad/requirements.txt
Units: mm. +Z up, front faces -Y, +X is the viewer's right; the gadget stands on its feet at z = 0.
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from gadget_cad import build as B  # noqa: E402
from gadget_cad import export as E  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--params", default=str(HERE / "params.json"))
    ap.add_argument("--out", default=str(HERE.parent))
    ap.add_argument("--no-checks", action="store_true")
    ap.add_argument("--no-cables", action="store_true")
    a = ap.parse_args()
    params = json.loads(Path(a.params).read_text())
    model = B.build(params, progress=lambda m, p=None: print(f"[cad] {{m}}", flush=True), route_cables=not a.no_cables)
    out = Path(a.out)
    files = E.write_all(model, out / "cad", out / "print", vendor=False)
    if not a.no_checks:
        from gadget_cad import checks as C
        res = C.run_checks(model, files)
        E.write_json(out / "cad" / "digital-checks.json", res)
        bad = [c for c in res["checks"] if c["status"] == "failed"]
        print(f"[cad] checks: {{len(res['checks'])}} run, {{len(bad)}} failed")
    print(f"[cad] wrote {{len(files['components'])}} components to {{out}}")


if __name__ == "__main__":
    main()
'''


def vendor_source(cad_dir: Path, name: str, engine: str, extra: tuple = ()) -> None:
    src = Path(__file__).resolve().parent
    dst = cad_dir / "gadget_cad"
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    for f in VENDOR:
        if (src / f).exists():
            shutil.copy2(src / f, dst / f)
    for f in extra:  # pack `freeform`: a csg body also needs its builder and the light tree compiler (gstudio/csg.py)
        p = (src / f) if (src / f).exists() else (src.parent / f)
        if p.exists():
            shutil.copy2(p, dst / Path(f).name)
    (dst / "__init__.py").write_text('"""Vendored Wish Labs CAD modules (see ../gadget_cad.py)."""\n')
    (cad_dir / "gadget_cad.py").write_text(SCRIPT.format(name=name, engine=engine))
    lock = src / "requirements.lock"
    if lock.exists():  # every import of cad/gadget_cad/ and its dependencies, == pinned with sha256 hashes (REVIEW-1 MAJ-26)
        shutil.copy2(lock, cad_dir / "requirements.txt")
    else:
        from .regen import DIRECT

        (cad_dir / "requirements.txt").write_text("# direct pins only (requirements.lock missing)\n" + "\n".join(DIRECT) + "\n")


def write_all(model, cad_dir: Path, print_dir: Path, vendor: bool = True, engine: str = "0.1.0",
              glb: bool = True, step: bool = True) -> dict:
    import time as _t

    cad_dir, print_dir = Path(cad_dir), Path(print_dir)
    cad_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    tm = {}
    t = _t.time()
    try:
        files["screens"] = write_screen_pngs(cad_dir, model)
    except Exception as e:  # pragma: no cover - PIL missing
        model.warnings.append(f"screen images skipped: {e}")
    if glb:  # first: the display mesh is coarse and must not inherit the fine STL triangulation
        files["glb"] = model_glb(cad_dir / "assembly.glb", model)
    tm["glb"] = round(_t.time() - t, 1)
    t = _t.time()
    prints = write_stls(print_dir, model)
    files["print"] = prints
    tm["stl"] = round(_t.time() - t, 1)
    t = _t.time()
    files["breps"] = write_breps(cad_dir / "components", model)
    tm["brep"] = round(_t.time() - t, 1)
    t = _t.time()
    comps = components_json(model, prints)
    write_json(cad_dir / "components.json", comps)
    files["components"] = comps
    anchors = dict(model.anchors)
    seen_d: dict = {}
    for r in model.info.get("decor_regions") or []:  # PRINT_Shell#decor[i] -> centre of its region (MAJ-35)
        if r.get("name"):
            b = r["bbox"]
            seen_d.setdefault(r["name"], []).append([(b[0] + b[1]) / 2, b[2], (b[4] + b[5]) / 2])
    for nm, pts in seen_d.items():
        anchors[nm] = pts[0] if len(pts) == 1 else [float(np.mean([p[0] for p in pts])), float(min(p[1] for p in pts)), float(np.mean([p[2] for p in pts]))]
        for k_, p_ in enumerate(pts if len(pts) > 1 else []):
            anchors[f"{nm}.{k_ + 1}"] = p_
    write_json(cad_dir / "anchors.json", {k: [round(float(x), 3) for x in v] for k, v in sorted(anchors.items())})
    write_json(cad_dir / "cable-routes.json", model.cables)
    tm["json"] = round(_t.time() - t, 1)
    t = _t.time()
    if step:
        write_step(cad_dir / "assembly.step", model)
        files["step"] = str(cad_dir / "assembly.step")
    tm["step"] = round(_t.time() - t, 1)
    files["timings"] = tm
    if vendor:
        csg_files = ("csg_body.py", "csg.py") if (model.params.get("body") or {}).get("family") == "csg" else ()
        vendor_source(cad_dir, model.params.get("name", "Gadget"), engine, extra=csg_files)
    return files
