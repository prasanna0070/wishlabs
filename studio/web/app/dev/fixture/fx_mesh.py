"""Meshing (OpenCascade triangulation with surface normals), cable tubes and a plain GLB writer.

GLB convention: glTF standard, metres, +Y up. Gadget (x, y, z) mm with +Z up and the front at -Y maps to
(x/1000, z/1000, -y/1000), exactly like web/server/app/builder/placeholder.py.
"""
from __future__ import annotations

import json
import struct

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepLib import BRepLib_ToolTriangulatedShape
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopAbs import TopAbs_REVERSED
from OCP.TopLoc import TopLoc_Location


def mesh_shape(shape, tol: float = 0.1, ang: float = 0.2) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(verts[N,3] mm, normals[N,3], tris[M,3]) with per-face vertices so real creases stay sharp."""
    BRepMesh_IncrementalMesh(shape.wrapped, tol, False, ang, True)
    Vs, Ns, Fs = [], [], []
    off = 0
    for face in shape.Faces():
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face.wrapped, loc)
        if tri is None or tri.NbTriangles() == 0:
            continue
        if not tri.HasNormals():
            BRepLib_ToolTriangulatedShape.ComputeNormals_s(face.wrapped, tri)
        trsf = loc.Transformation()
        nn = tri.NbNodes()
        P = np.empty((nn, 3))
        N = np.empty((nn, 3))
        for i in range(1, nn + 1):
            p = tri.Node(i).Transformed(trsf)
            P[i - 1] = (p.X(), p.Y(), p.Z())
            d = tri.Normal(i).Transformed(trsf)
            N[i - 1] = (d.X(), d.Y(), d.Z())
        nt = tri.NbTriangles()
        T = np.empty((nt, 3), dtype=np.int64)
        for i in range(1, nt + 1):
            t = tri.Triangle(i)
            T[i - 1] = (t.Value(1) - 1, t.Value(2) - 1, t.Value(3) - 1)
        if face.wrapped.Orientation() == TopAbs_REVERSED:
            T = T[:, [0, 2, 1]]
        # make stored normals agree with the triangle winding (ComputeNormals conventions vary by version)
        g = np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]])
        s = np.einsum("ij,ij->i", g, N[T[:, 0]] + N[T[:, 1]] + N[T[:, 2]])
        if np.sum(s < 0) > np.sum(s > 0):
            N = -N
        Vs.append(P)
        Ns.append(N)
        Fs.append(T + off)
        off += nn
    if not Vs:
        return np.zeros((0, 3)), np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)
    N = np.vstack(Ns)
    N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)
    return np.vstack(Vs), N, np.vstack(Fs)


def write_stl(shape, path, tol: float = 0.1, ang: float = 0.2) -> None:
    """Binary STL from an absolute-tolerance mesh (CadQuery's exportStl default is relative, too coarse on big curves)."""
    from OCP.StlAPI import StlAPI_Writer

    BRepMesh_IncrementalMesh(shape.wrapped, tol, False, ang, True)
    w = StlAPI_Writer()
    w.ASCIIMode = False
    if not w.Write(shape.wrapped, str(path)):
        raise RuntimeError(f"STL write failed: {path}")


def tube_mesh(points, dia: float, sides: int = 14) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Swept circle along a polyline with rounded (hemisphere-ish) caps; smooth normals."""
    P = np.asarray(points, float)
    r = dia / 2
    T = np.gradient(P, axis=0)
    T /= np.maximum(np.linalg.norm(T, axis=1, keepdims=True), 1e-9)
    ref = np.array([0.0, 0.0, 1.0]) if abs(T[0][2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    Nn = np.cross(T[0], ref)
    Nn /= np.linalg.norm(Nn)
    frames = []
    for i in range(len(P)):  # parallel transport
        if i:
            Nn = Nn - np.dot(Nn, T[i]) * T[i]
            Nn /= max(np.linalg.norm(Nn), 1e-9)
        frames.append((Nn.copy(), np.cross(T[i], Nn)))
    ang = np.linspace(0, 2 * np.pi, sides, endpoint=False)
    V, N = [], []
    for i, (n1, n2) in enumerate(frames):
        for a in ang:
            d = np.cos(a) * n1 + np.sin(a) * n2
            V.append(P[i] + r * d)
            N.append(d)
    F = []
    for i in range(len(P) - 1):
        for k in range(sides):
            a, b = i * sides + k, i * sides + (k + 1) % sides
            c, d = a + sides, b + sides
            F += [[a, c, b], [b, c, d]]
    for end, sgn in ((0, -1.0), (len(P) - 1, 1.0)):  # caps
        ci = len(V)
        V.append(P[end] + sgn * T[end] * r * 0.6)
        N.append(sgn * T[end])
        for k in range(sides):
            a, b = end * sides + k, end * sides + (k + 1) % sides
            F.append([a, b, ci] if sgn > 0 else [b, a, ci])
    return np.array(V), np.array(N), np.array(F, dtype=np.int64)


def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def to_gltf(V: np.ndarray, N: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    Vg = np.stack([V[:, 0] / 1000.0, V[:, 2] / 1000.0, -V[:, 1] / 1000.0], axis=1)
    Ng = np.stack([N[:, 0], N[:, 2], -N[:, 1]], axis=1)
    return Vg.astype(np.float32), Ng.astype(np.float32)


def write_glb(path, meshes: list[dict], generator: str, extras: dict | None = None) -> dict:
    """meshes: [{name, V (mm), N, F, color hex, extras}]. One node + one mesh + one material per component."""
    blobs = []
    offset = 0
    gltf = {"asset": {"version": "2.0", "generator": generator}, "scene": 0, "scenes": [{"name": "assembly", "nodes": []}],
            "nodes": [], "meshes": [], "materials": [], "accessors": [], "bufferViews": [], "buffers": []}
    if extras:
        gltf["asset"]["extras"] = extras

    def add_view(data: bytes, target: int) -> int:
        nonlocal offset
        pad = (4 - len(data) % 4) % 4
        blobs.append(data + b"\x00" * pad)
        gltf["bufferViews"].append({"buffer": 0, "byteOffset": offset, "byteLength": len(data), "target": target})
        offset += len(data) + pad
        return len(gltf["bufferViews"]) - 1

    lo_all, hi_all = np.full(3, np.inf), np.full(3, -np.inf)
    for m in meshes:
        Vg, Ng = to_gltf(m["V"], m["N"])
        F = m["F"].astype(np.uint32)
        lo, hi = Vg.min(axis=0), Vg.max(axis=0)
        lo_all, hi_all = np.minimum(lo_all, lo), np.maximum(hi_all, hi)
        vp = add_view(Vg.tobytes(), 34962)
        vn = add_view(Ng.tobytes(), 34962)
        vi = add_view(F.tobytes(), 34963)
        acc0 = len(gltf["accessors"])
        gltf["accessors"] += [
            {"bufferView": vp, "componentType": 5126, "count": len(Vg), "type": "VEC3", "min": lo.tolist(), "max": hi.tolist()},
            {"bufferView": vn, "componentType": 5126, "count": len(Ng), "type": "VEC3"},
            {"bufferView": vi, "componentType": 5125, "count": int(F.size), "type": "SCALAR"}]
        r, g, b = (srgb_to_linear(c) for c in hex_rgb(m["color"]))
        mat = {"name": m["name"], "pbrMetallicRoughness": {"baseColorFactor": [r, g, b, 1.0], "metallicFactor": 0.0,
                                                          "roughnessFactor": m.get("roughness", 0.6)}}
        if m.get("double_sided"):
            mat["doubleSided"] = True
        gltf["materials"].append(mat)
        gltf["meshes"].append({"name": m["name"], "primitives": [{"attributes": {"POSITION": acc0, "NORMAL": acc0 + 1},
                                                                  "indices": acc0 + 2, "material": len(gltf["materials"]) - 1}]})
        node = {"name": m["name"], "mesh": len(gltf["meshes"]) - 1}
        if m.get("extras"):
            node["extras"] = m["extras"]
        gltf["nodes"].append(node)
        gltf["scenes"][0]["nodes"].append(len(gltf["nodes"]) - 1)
    binc = b"".join(blobs)
    gltf["buffers"].append({"byteLength": len(binc)})
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(binc)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<III", 0x46546C67, 2, total))
        fh.write(struct.pack("<II", len(js), 0x4E4F534A))
        fh.write(js)
        fh.write(struct.pack("<II", len(binc), 0x004E4942))
        fh.write(binc)
    return {"bbox_m": [lo_all.tolist(), hi_all.tolist()], "bytes": total}
