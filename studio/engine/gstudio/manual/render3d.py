"""Offscreen VTK renderer for manual illustrations, driven by the saved CAD geometry.

* Geometry: each `cad/components/<NAME>.brep` is tessellated with OCCT BRepMesh at a fixed absolute
  deflection (fine enough that a 9 mm eye is a circle, not an octagon), cached as .npz keyed by the BREP
  sha256 + mesh parameters, in parallel worker processes. Print-bed views read `print/*.stl`.
* Rendering: VTK with EGL (headless Mesa) or any offscreen window, parallel projection, +Z up, 2x
  supersampling for anti-aliasing. Ghosted parts are translucent (depth peeling) with crisp feature edges.
* Anchors: an ID pass renders every opaque part in a flat unique colour; a label's anchor is the deepest
  visible pixel of its part (distance transform), so a dot never lands on a hidden part. Anchors from
  `anchors.json` and surface rays are projected through the same camera.
"""

from __future__ import annotations

import hashlib
import math
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

if not os.environ.get("DISPLAY"):
    os.environ.setdefault("VTK_DEFAULT_OPENGL_WINDOW", "vtkEGLRenderWindow")

MESH_VERSION = "gs-manual-mesh-v1"
LIN_DEFL = 0.05  # mm, absolute chordal deflection
ANG_DEFL = 0.25  # rad: 25 segments per full circle at most coarse
BG = (0.925, 0.918, 0.894)  # viewport #ECEAE4


# --------------------------------------------------------------------------------------------- tessellation
def tessellate_brep(path: str, lin: float = LIN_DEFL, ang: float = ANG_DEFL) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """BREP file -> (points Nx3, triangles Mx3, solid index per triangle)."""
    from OCP.BRep import BRep_Builder, BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED, TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS, TopoDS_Shape

    shape = TopoDS_Shape()
    if not BRepTools.Read_s(shape, str(path), BRep_Builder()):
        raise ValueError(f"cannot read BREP {path}")
    # scale the deflection for very large or very small parts
    BRepMesh_IncrementalMesh(shape, lin, False, ang, True)
    P, T, S = [], [], []
    off = 0

    def faces_of(sh, solid_idx):
        nonlocal off
        ex = TopExp_Explorer(sh, TopAbs_FACE)
        while ex.More():
            f = TopoDS.Face_s(ex.Current())
            ex.Next()
            loc = TopLoc_Location()
            tri = BRep_Tool.Triangulation_s(f, loc)
            if tri is None:
                continue
            tr = loc.Transformation()
            n = tri.NbNodes()
            pts = np.empty((n, 3))
            for i in range(1, n + 1):
                p = tri.Node(i).Transformed(tr)
                pts[i - 1] = (p.X(), p.Y(), p.Z())
            m = tri.NbTriangles()
            tris = np.empty((m, 3), dtype=np.int64)
            rev = f.Orientation() == TopAbs_REVERSED
            for i in range(1, m + 1):
                a, b, c = tri.Triangle(i).Get()
                tris[i - 1] = (a, c, b) if rev else (a, b, c)
            P.append(pts)
            T.append(tris - 1 + off)
            S.append(np.full(m, solid_idx, dtype=np.int32))
            off += n

    ex = TopExp_Explorer(shape, TopAbs_SOLID)
    k = 0
    while ex.More():
        faces_of(ex.Current(), k)
        k += 1
        ex.Next()
    if k == 0:  # shells / faces only
        faces_of(shape, 0)
    if not P:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), np.zeros(0, dtype=np.int32)
    return np.vstack(P), np.vstack(T), np.concatenate(S)


def _cache_key(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    h.update(f"{MESH_VERSION}-{LIN_DEFL}-{ANG_DEFL}".encode())
    return h.hexdigest()[:32]


def _tess_job(args: tuple[str, str]) -> tuple[str, str, Optional[str]]:
    src, dst = args
    try:
        P, T, S = tessellate_brep(src)
        tmp = dst + ".tmp.npz"
        np.savez_compressed(tmp, P=P, T=T, S=S)
        os.replace(tmp, dst)
        return src, dst, None
    except Exception as exc:  # pragma: no cover - reported to the caller
        return src, dst, f"{type(exc).__name__}: {exc}"


def default_cache_dir() -> Path:
    return Path(os.environ.get("GS_MESH_CACHE", "/tmp/gstudio-mesh-cache"))


# --------------------------------------------------------------------------------------------- GLB source
def _lin_to_srgb(c: float) -> float:
    c = max(0.0, min(1.0, float(c)))
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


class GlbSource:
    """Meshes and per-subpart colours from the CAD stage's assembly.glb (one node per component, glTF metres,
    usually Y-up). The axis/scale mapping back to gadget mm (+Z up) is chosen by matching components.json bboxes."""

    def __init__(self, path: Path, ref_bboxes: dict[str, tuple]):
        import pygltflib

        self.g = pygltflib.GLTF2().load(str(path))
        self.blob = self.g.binary_blob()
        self.nodes = {n.name: n for n in self.g.nodes if n.name and n.mesh is not None}
        self.map = self._pick_mapping(ref_bboxes)

    def _acc(self, i: int) -> np.ndarray:
        a = self.g.accessors[i]
        bv = self.g.bufferViews[a.bufferView]
        dt = {5126: np.float32, 5125: np.uint32, 5123: np.uint16, 5121: np.uint8}[a.componentType]
        n = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a.type]
        off = (bv.byteOffset or 0) + (a.byteOffset or 0)
        stride = bv.byteStride
        item = np.dtype(dt).itemsize * n
        if stride and stride != item:
            raw = np.frombuffer(self.blob, dtype=np.uint8, count=stride * a.count, offset=off).reshape(a.count, stride)
            return raw[:, :item].copy().view(dt).reshape(a.count, n)
        arr = np.frombuffer(self.blob, dtype=dt, count=a.count * n, offset=off)
        return arr.reshape(a.count, n) if n > 1 else arr

    def _raw(self, name: str) -> list[tuple[np.ndarray, np.ndarray, tuple]]:
        node = self.nodes[name]
        out = []
        for prim in self.g.meshes[node.mesh].primitives:
            if prim.mode not in (None, 4):
                continue
            P = self._acc(prim.attributes.POSITION).astype(np.float64)
            T = self._acc(prim.indices).reshape(-1, 3).astype(np.int64) if prim.indices is not None else \
                np.arange(len(P), dtype=np.int64).reshape(-1, 3)
            col = (0.7, 0.7, 0.7)
            if prim.material is not None:
                pbr = self.g.materials[prim.material].pbrMetallicRoughness
                if pbr is not None and pbr.baseColorFactor:
                    col = tuple(_lin_to_srgb(v) for v in pbr.baseColorFactor[:3])
            out.append((P, T, col))
        return out

    @staticmethod
    def _apply(P: np.ndarray, m: str) -> np.ndarray:
        x, y, z = P[:, 0], P[:, 1], P[:, 2]
        if m == "yup":
            Q = np.stack([x, -z, y], axis=1)
        elif m == "yup2":
            Q = np.stack([x, z, -y], axis=1)
        else:
            Q = P.copy()
        return Q

    def _pick_mapping(self, ref: dict[str, tuple]) -> tuple[str, float]:
        cands = [(m, sc) for m in ("yup", "yup2", "zup") for sc in (1000.0, 1.0)]
        names = [n for n in sorted(ref, key=lambda n: -(abs(ref[n][1] - ref[n][0]) * abs(ref[n][5] - ref[n][4]))) if n in self.nodes][:3]
        if not names:
            return ("yup", 1000.0)
        best, err = cands[0], float("inf")
        raws = {n: np.vstack([P for P, _, _ in self._raw(n)]) for n in names}
        for m, sc in cands:
            e = 0.0
            for n in names:
                Q = self._apply(raws[n], m) * sc
                bb = np.array([Q[:, 0].min(), Q[:, 0].max(), Q[:, 1].min(), Q[:, 1].max(), Q[:, 2].min(), Q[:, 2].max()])
                e += float(np.abs(bb - np.array(ref[n])).sum())
            if e < err:
                best, err = (m, sc), e
        return best

    def has(self, name: str) -> bool:
        return name in self.nodes

    def polydata(self, name: str):
        Ps, Ts, Cs = [], [], []
        off = 0
        for P, T, col in self._raw(name):
            Ps.append(self._apply(P, self.map[0]) * self.map[1])
            Ts.append(T + off)
            Cs.append(np.tile(np.array([int(round(v * 255)) for v in col], dtype=np.uint8), (len(T), 1)))
            off += len(P)
        if not Ps:
            return None
        P, T, C = np.vstack(Ps), np.vstack(Ts), np.vstack(Cs)
        return _to_polydata(P, T, np.zeros(len(T), dtype=np.int32), colors=C)


def tube_polydata(points, diameter: float):
    """A smooth cable envelope from a route centreline (cable-routes.json), much lighter than a swept BREP."""
    import vtk

    pts = vtk.vtkPoints()
    for p in points:
        pts.InsertNextPoint(*p)
    line = vtk.vtkPolyLine()
    line.GetPointIds().SetNumberOfIds(len(points))
    for i in range(len(points)):
        line.GetPointIds().SetId(i, i)
    cells = vtk.vtkCellArray()
    cells.InsertNextCell(line)
    pd = vtk.vtkPolyData()
    pd.SetPoints(pts)
    pd.SetLines(cells)
    clean = vtk.vtkCleanPolyData()
    clean.SetInputData(pd)
    clean.Update()
    tf = vtk.vtkTubeFilter()
    tf.SetInputConnection(clean.GetOutputPort())
    tf.SetRadius(max(0.6, float(diameter or 3.0) / 2))
    tf.SetNumberOfSides(20)
    tf.CappingOn()
    tri = vtk.vtkTriangleFilter()
    tri.SetInputConnection(tf.GetOutputPort())
    tri.Update()
    nrm = vtk.vtkPolyDataNormals()
    nrm.SetInputData(tri.GetOutput())
    nrm.SetFeatureAngle(60.0)
    nrm.Update()
    return nrm.GetOutput()


# --------------------------------------------------------------------------------------------- mesh store
class MeshStore:
    def __init__(self, brep_dir: Path, cache_dir: Optional[Path] = None, workers: int = 0, glb: Optional[Path] = None,
                 ref_bboxes: Optional[dict] = None, routes: Optional[dict] = None, solid_boxes: Optional[dict] = None):
        self.brep_dir = Path(brep_dir)
        self.cache_dir = Path(cache_dir or default_cache_dir())
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.workers = workers or max(1, min(12, (os.cpu_count() or 2) - 1))
        self._npz: dict[str, Path] = {}
        self._pd: dict[str, object] = {}
        self._edges: dict[str, object] = {}
        self.errors: dict[str, str] = {}
        self.routes = routes or {}  # comp name -> (points, diameter)
        self.solid_boxes = dict(solid_boxes or {})
        self.glb = None
        if glb and Path(glb).is_file():
            try:
                self.glb = GlbSource(Path(glb), ref_bboxes or {})
            except Exception as exc:  # fall back to BREP tessellation
                self.errors["assembly.glb"] = f"{type(exc).__name__}: {exc}"

    def brep(self, name: str) -> Optional[Path]:
        p = self.brep_dir / f"{name}.brep"
        return p if p.is_file() else None

    def preload(self, names: list[str]) -> dict[str, str]:
        jobs = []
        for n in sorted({x.split("#", 1)[0] for x in names}):
            if n in self.routes or (self.glb is not None and self.glb.has(n)):
                continue  # drawn from cable-routes.json / assembly.glb: no tessellation needed
            src = self.brep(n)
            if not src:
                self.errors[n] = "no BREP file"
                continue
            dst = self.cache_dir / f"{_cache_key(src)}.npz"
            self._npz[n] = dst
            if not dst.is_file():
                jobs.append((str(src), str(dst)))
        if jobs:
            # biggest first so the long shell tessellation starts immediately
            jobs.sort(key=lambda j: -os.path.getsize(j[0]))
            if len(jobs) == 1 or self.workers == 1:
                results = [_tess_job(j) for j in jobs]
            else:
                import multiprocessing as mp

                # spawn, not fork: the CAD stage may have run OCCT (with worker threads) in this process,
                # and forking a process that holds those threads can deadlock the children.
                with ProcessPoolExecutor(max_workers=min(self.workers, len(jobs)), mp_context=mp.get_context("spawn")) as ex:
                    results = list(ex.map(_tess_job, jobs))
            for src, _dst, err in results:
                if err:
                    self.errors[Path(src).stem] = err
        return dict(self.errors)

    def polydata(self, name: str):
        if name in self._pd:
            return self._pd[name]
        if name in self.routes:
            pts, dia = self.routes[name]
            if len(pts) >= 2:
                self._pd[name] = tube_polydata(pts, dia)
                return self._pd[name]
        if self.glb is not None and self.glb.has(name):
            try:
                pd = self.glb.polydata(name)
                if pd is not None and pd.GetNumberOfCells():
                    self._pd[name] = pd
                    return pd
            except Exception as exc:
                self.errors[name] = f"glb: {type(exc).__name__}: {exc}"
        if name not in self._npz:
            self.preload([name])
        p = self._npz.get(name)
        if not p or not p.is_file():
            self._pd[name] = None
            return None
        z = np.load(p)
        pd = _to_polydata(z["P"], z["T"], z["S"])
        self._pd[name] = pd
        return pd

    def solid(self, name: str, k: int = 0):
        """One solid of a multi-solid component (e.g. a single screw out of a group of four)."""
        key = f"{name}#{k}"
        if key not in self._pd:
            import vtk

            pd = self.polydata(name)
            if pd is None:
                return None
            arr = pd.GetCellData().GetArray("solid")
            if arr is None or arr.GetRange()[1] < 1:
                sub = self._solid_by_region(name, pd, k)
                self._pd[key] = sub if sub is not None else pd
                return self._pd[key]
            th = vtk.vtkThreshold()
            th.SetInputData(pd)
            th.SetInputArrayToProcess(0, 0, 0, vtk.vtkDataObject.FIELD_ASSOCIATION_CELLS, "solid")
            th.SetLowerThreshold(k - 0.5)
            th.SetUpperThreshold(k + 0.5)
            th.SetThresholdFunction(vtk.vtkThreshold.THRESHOLD_BETWEEN)
            gf = vtk.vtkGeometryFilter()
            gf.SetInputConnection(th.GetOutputPort())
            gf.Update()
            out = vtk.vtkPolyData()
            out.DeepCopy(gf.GetOutput())
            self._pd[key] = out
        return self._pd[key]

    def solids(self, name: str, ks: list[int]):
        """Several solids of one component merged into one mesh."""
        key = f"{name}#{','.join(str(k) for k in ks)}"
        if key not in self._pd:
            import vtk

            app = vtk.vtkAppendPolyData()
            n = 0
            for k in ks:
                pd = self.solid(name, k)
                if pd is not None and pd.GetNumberOfPoints():
                    app.AddInputData(pd)
                    n += 1
            if not n:
                self._pd[key] = None
            else:
                app.Update()
                out = vtk.vtkPolyData()
                out.DeepCopy(app.GetOutput())
                self._pd[key] = out
        return self._pd[key]

    def boxes(self, name: str) -> list:
        if name not in self.solid_boxes:
            b = self.brep(name)
            try:
                from .data import solid_bboxes

                self.solid_boxes[name] = solid_bboxes(b) if b else []
            except Exception:
                self.solid_boxes[name] = []
        return self.solid_boxes[name]

    def _solid_by_region(self, name: str, pd, k: int):
        """Connected regions of a GLB mesh whose centres fall in BREP solid k's box (GLB keeps no solid ids)."""
        import vtk

        boxes = self.boxes(name)
        if len(boxes) < 2 or k >= len(boxes):
            return None
        from scipy.sparse import coo_matrix
        from scipy.sparse.csgraph import connected_components
        from vtk.util import numpy_support as ns

        P = ns.vtk_to_numpy(pd.GetPoints().GetData())
        tris = ns.vtk_to_numpy(pd.GetPolys().GetConnectivityArray()).reshape(-1, 3)
        e = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]]])
        g = coo_matrix((np.ones(len(e)), (e[:, 0], e[:, 1])), shape=(len(P), len(P)))
        _, lab = connected_components(g, directed=False)
        cell_lab = lab[tris[:, 0]]
        cent = P[tris].mean(axis=1)
        bx = np.array(boxes)
        bc = np.stack([(bx[:, 0] + bx[:, 1]) / 2, (bx[:, 2] + bx[:, 3]) / 2, (bx[:, 4] + bx[:, 5]) / 2], axis=1)
        keep = np.zeros(len(tris), dtype=np.uint8)
        for r in np.unique(cell_lab):
            m = cell_lab == r
            c = cent[m].mean(axis=0)
            if int(np.argmin(np.linalg.norm(bc - c, axis=1))) == k:
                keep[m] = 1
        if not keep.any():
            return None
        tmp = vtk.vtkPolyData()
        tmp.ShallowCopy(pd)
        arr = ns.numpy_to_vtk(keep, deep=True)
        arr.SetName("keep")
        tmp.GetCellData().AddArray(arr)
        th = vtk.vtkThreshold()
        th.SetInputData(tmp)
        th.SetInputArrayToProcess(0, 0, 0, vtk.vtkDataObject.FIELD_ASSOCIATION_CELLS, "keep")
        th.SetLowerThreshold(0.5)
        th.SetUpperThreshold(1.5)
        th.SetThresholdFunction(vtk.vtkThreshold.THRESHOLD_BETWEEN)
        gf = vtk.vtkGeometryFilter()
        gf.SetInputConnection(th.GetOutputPort())
        gf.Update()
        res = vtk.vtkPolyData()
        res.DeepCopy(gf.GetOutput())
        return res

    def n_solids(self, name: str) -> int:
        pd = self.polydata(name)
        arr = pd.GetCellData().GetArray("solid") if pd is not None else None
        if arr is not None and arr.GetNumberOfTuples() and arr.GetRange()[1] >= 1:
            return int(arr.GetRange()[1]) + 1
        return max(1, len(self.boxes(name))) if (self.glb is not None and self.glb.has(name)) else 1

    def stl(self, path: str):
        key = f"stl:{path}"
        if key not in self._pd:
            import vtk

            r = vtk.vtkSTLReader()
            r.SetFileName(str(path))
            r.Update()
            self._pd[key] = _prep(r.GetOutput())
        return self._pd[key]

    def edges(self, key: str, pd):
        if pd is None or pd.GetNumberOfCells() > 400000 or key.split("#", 1)[0] in self.routes:
            return None
        if key not in self._edges:
            import vtk

            fe = vtk.vtkFeatureEdges()
            fe.SetInputData(pd)
            fe.BoundaryEdgesOn()
            fe.FeatureEdgesOn()
            fe.SetFeatureAngle(32.0)
            fe.ManifoldEdgesOff()
            fe.NonManifoldEdgesOff()
            fe.ColoringOff()
            fe.Update()
            self._edges[key] = fe.GetOutput()
        return self._edges[key]


def _to_polydata(P: np.ndarray, T: np.ndarray, S: np.ndarray, colors: Optional[np.ndarray] = None):
    import vtk
    from vtk.util import numpy_support as ns

    pts = vtk.vtkPoints()
    pts.SetData(ns.numpy_to_vtk(np.ascontiguousarray(P, dtype=np.float64), deep=True))
    cells = vtk.vtkCellArray()
    if len(T):
        conn = np.ascontiguousarray(T.reshape(-1), dtype=np.int64)
        offs = np.arange(0, len(conn) + 1, 3, dtype=np.int64)
        cells.SetData(ns.numpy_to_vtkIdTypeArray(offs, deep=True), ns.numpy_to_vtkIdTypeArray(conn, deep=True))
    pd = vtk.vtkPolyData()
    pd.SetPoints(pts)
    pd.SetPolys(cells)
    sid = ns.numpy_to_vtk(np.ascontiguousarray(S, dtype=np.int32), deep=True)
    sid.SetName("solid")
    pd.GetCellData().AddArray(sid)
    if colors is not None and len(colors) == len(T):
        arr = ns.numpy_to_vtk(np.ascontiguousarray(colors, dtype=np.uint8), deep=True)
        arr.SetName("rgb")
        pd.GetCellData().AddArray(arr)
    return _prep(pd)


def _prep(pd):
    import vtk

    clean = vtk.vtkCleanPolyData()
    clean.SetInputData(pd)
    clean.SetTolerance(0.0)
    clean.PointMergingOn()
    clean.Update()
    nrm = vtk.vtkPolyDataNormals()
    nrm.SetInputData(clean.GetOutput())
    nrm.SetFeatureAngle(32.0)
    nrm.SplittingOn()
    nrm.ConsistencyOn()
    nrm.AutoOrientNormalsOff()
    nrm.Update()
    return nrm.GetOutput()


# --------------------------------------------------------------------------------------------- render
@dataclass
class ItemSpec:
    key: str  # name used in ID buffer / labels
    pd: object
    color: tuple[float, float, float]
    mode: str  # solid | focus | ghost | context
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    edges: object = None


@dataclass
class PanelRender:
    rgb: np.ndarray  # (h, w, 3) uint8 at panel size
    ids: np.ndarray  # (h, w) int32, -1 background
    keys: list[str]
    focus_mask: np.ndarray  # (h, w) bool
    model_mask: np.ndarray  # (h, w) bool
    camera: dict
    project: object  # callable world point -> panel px (x, y)
    visible: object = None  # callable world point -> bool (not hidden behind an opaque part)
    anchors: dict = field(default_factory=dict)


def _mix(c, t, k):
    return tuple(c[i] * (1 - k) + t[i] * k for i in range(3))


class Renderer:
    def __init__(self, ss: int = 2):
        import vtk

        self.vtk = vtk
        self.ss = ss
        self.win = vtk.vtkRenderWindow()
        self.win.SetOffScreenRendering(1)
        self.win.SetMultiSamples(0)
        self.win.SetAlphaBitPlanes(1)
        self.ren = vtk.vtkRenderer()
        self.win.AddRenderer(self.ren)
        self.ren.SetUseDepthPeeling(1)
        self.ren.SetMaximumNumberOfPeels(10)
        self.ren.SetOcclusionRatio(0.0)
        vtk.vtkMapper.SetResolveCoincidentTopologyToPolygonOffset()
        vtk.vtkMapper.SetResolveCoincidentTopologyPolygonOffsetParameters(1.2, 1.0)
        self.lk = vtk.vtkLightKit()
        self.lk.SetKeyLightIntensity(0.95)
        self.lk.AddLightsToRenderer(self.ren)
        self.size = (0, 0)

    # ------------------------------------------------------------------ actors
    def _actor(self, pd, color, opacity=1.0, offset=(0, 0, 0), ambient=0.22, diffuse=0.8, specular=0.12, lines=False, lw=1.0):
        vtk = self.vtk
        m = vtk.vtkPolyDataMapper()
        m.SetInputData(pd)
        m.ScalarVisibilityOff()
        a = vtk.vtkActor()
        a.SetMapper(m)
        p = a.GetProperty()
        p.SetColor(*color)
        p.SetOpacity(opacity)
        if lines:
            p.SetLineWidth(lw)
            p.LightingOff()
        else:
            p.SetAmbient(ambient)
            p.SetDiffuse(diffuse)
            p.SetSpecular(specular)
            p.SetSpecularPower(25)
            p.SetInterpolationToPhong()
        a.SetPosition(*offset)
        return a

    def _set_size(self, w, h):
        if self.size != (w, h):
            self.win.SetSize(w, h)
            self.size = (w, h)

    def _zbuffer(self) -> np.ndarray:
        """Depth buffer of the last render, bottom-up rows (display coordinates)."""
        vtk = self.vtk
        from vtk.util import numpy_support as ns

        w, h = self.win.GetSize()
        arr = vtk.vtkFloatArray()
        self.win.GetZbufferData(0, 0, w - 1, h - 1, arr)
        return ns.vtk_to_numpy(arr).reshape(h, w).copy()

    def _grab(self) -> np.ndarray:
        vtk = self.vtk
        from vtk.util import numpy_support as ns

        f = vtk.vtkWindowToImageFilter()
        f.SetInput(self.win)
        f.SetInputBufferTypeToRGB()
        f.ReadFrontBufferOff()
        f.Update()
        img = f.GetOutput()
        w, h, _ = img.GetDimensions()
        arr = ns.vtk_to_numpy(img.GetPointData().GetScalars()).reshape(h, w, -1)[:, :, :3]
        return np.flipud(arr).copy()

    # ------------------------------------------------------------------ camera
    def frame(self, bbox, view_dir, w, h, pad=1.18, label_room=0.3):
        cam = self.ren.GetActiveCamera()
        cam.ParallelProjectionOn()
        d = np.array(view_dir, float)
        d /= np.linalg.norm(d) or 1.0
        up = np.array([0.0, 0.0, 1.0])
        if abs(float(d @ up)) > 0.985:
            up = np.array([0.0, 1.0, 0.0])
        fwd = -d
        right = np.cross(fwd, up)
        right /= np.linalg.norm(right)
        upv = np.cross(right, fwd)
        x0, x1, y0, y1, z0, z1 = bbox
        corners = np.array([[x, y, z] for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)])
        c = corners.mean(0)
        pr, pu = (corners - c) @ right, (corners - c) @ upv
        cx = (pr.max() + pr.min()) / 2
        cy = (pu.max() + pu.min()) / 2
        c = c + right * cx + upv * cy
        ext_x, ext_y = pr.max() - pr.min(), pu.max() - pu.min()
        avail_w = max(0.25, 1.0 - 2 * label_room) * w
        half_h = max(ext_y / 2, (ext_x / 2) * h / avail_w) * pad
        half_h = max(half_h, 5.0)
        diag = float(np.linalg.norm(corners.max(0) - corners.min(0))) or 100.0
        pos = c + d * (diag * 3 + 200)
        cam.SetFocalPoint(*c)
        cam.SetPosition(*pos)
        cam.SetViewUp(*upv)
        cam.SetParallelScale(half_h)
        self.ren.ResetCameraClippingRange()
        rng = cam.GetClippingRange()
        cam.SetClippingRange(max(0.1, rng[0] * 0.5), rng[1] * 2.0)
        return {"position": [round(float(v), 1) for v in pos], "target": [round(float(v), 1) for v in c],
                "up": [round(float(v), 3) for v in upv], "parallel_scale": round(half_h, 2)}

    # ------------------------------------------------------------------ main
    def _arrow(self, a, b, radius):
        vtk = self.vtk
        a, b = np.array(a, float), np.array(b, float)
        d = b - a
        L = float(np.linalg.norm(d))
        if L < 1e-3:
            return None
        src = vtk.vtkArrowSource()
        src.SetTipLength(min(0.45, 4.2 * radius / L))
        src.SetTipRadius(min(0.2, 2.2 * radius / L))
        src.SetShaftRadius(min(0.08, 0.9 * radius / L))
        src.SetTipResolution(32)
        src.SetShaftResolution(24)
        x = d / L
        ref = np.array([0.0, 0.0, 1.0]) if abs(x[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
        y = np.cross(ref, x)
        y /= np.linalg.norm(y)
        z = np.cross(x, y)
        m = vtk.vtkMatrix4x4()
        for i in range(3):
            m.SetElement(i, 0, x[i] * L)
            m.SetElement(i, 1, y[i] * L)
            m.SetElement(i, 2, z[i] * L)
            m.SetElement(i, 3, a[i])
        tf = vtk.vtkTransform()
        tf.SetMatrix(m)
        tpf = vtk.vtkTransformPolyDataFilter()
        tpf.SetInputConnection(src.GetOutputPort())
        tpf.SetTransform(tf)
        tpf.Update()
        return self._actor(tpf.GetOutput(), (0.19, 0.27, 0.85), ambient=0.45, diffuse=0.6, specular=0.2)

    def render(self, items: list[ItemSpec], w: int, h: int, view_dir, bbox, pad=1.06, label_room=0.12,
               floor: Optional[float] = None, arrows=()) -> PanelRender:
        vtk = self.vtk
        ss = self.ss
        self.ren.RemoveAllViewProps()
        self.ren.SetBackground(*BG)
        actors = []
        for it in items:
            if it.pd is None:
                continue
            if it.mode == "ghost":
                col = _mix(it.color, (1, 1, 1), 0.45)
                a = self._actor(it.pd, col, opacity=0.17, offset=it.offset, ambient=0.45, diffuse=0.55, specular=0.0)
                e = self._actor(it.edges, _mix(it.color, (0.1, 0.12, 0.14), 0.55), opacity=0.45, offset=it.offset, lines=True, lw=1.0 * ss) if it.edges is not None else None
            else:
                col = it.color if it.mode in ("focus", "solid") else _mix(it.color, (1, 1, 1), 0.3)
                a = self._actor(it.pd, col, offset=it.offset)
                if it.pd.GetCellData().GetArray("rgb") is not None:
                    m = a.GetMapper()
                    m.ScalarVisibilityOn()
                    m.SetScalarModeToUseCellFieldData()
                    m.SelectColorArray("rgb")
                    m.SetColorModeToDirectScalars()
                e = self._actor(it.edges, _mix(it.color, (0.05, 0.06, 0.08), 0.6), opacity=0.55 if it.mode == "focus" else 0.35, offset=it.offset, lines=True, lw=0.8 * ss) if it.edges is not None else None
            self.ren.AddActor(a)
            if e is not None:
                self.ren.AddActor(e)
            actors.append((it, a, e))
        arrow_actors = []
        diag = float(np.linalg.norm(np.array(bbox[1::2]) - np.array(bbox[0::2]))) or 100.0
        for a, b in arrows:
            act = self._arrow(a, b, radius=max(0.6, diag * 0.006))
            if act is not None:
                self.ren.AddActor(act)
                arrow_actors.append(act)
        fl = None
        if floor is not None:
            x0, x1, y0, y1, z0, z1 = bbox
            span = max(x1 - x0, y1 - y0) * 1.6 + 80
            src = vtk.vtkPlaneSource()
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            src.SetOrigin(cx - span, cy - span, floor - 0.05)
            src.SetPoint1(cx + span, cy - span, floor - 0.05)
            src.SetPoint2(cx - span, cy + span, floor - 0.05)
            src.Update()
            fl = self._actor(src.GetOutput(), (0.88, 0.87, 0.84), ambient=0.6, diffuse=0.4, specular=0.0)
            self.ren.AddActor(fl)
        self._set_size(w * ss, h * ss)
        cam = self.frame(bbox, view_dir, w, h, pad=pad, label_room=label_room)
        self.ren.SetUseDepthPeeling(1)
        self.win.Render()
        big = self._grab()

        # ---- ID pass: opaque parts only, flat colours, no edges
        self.ren.SetUseDepthPeeling(0)
        self.ren.SetBackground(0, 0, 0)
        keys = []
        for i, (it, a, e) in enumerate(actors):
            if e is not None:
                e.VisibilityOff()
            if it.mode == "ghost":
                a.VisibilityOff()
                continue
            keys.append(it.key)
            k = len(keys)
            a.GetMapper().ScalarVisibilityOff()
            p = a.GetProperty()
            p.LightingOff()
            p.SetOpacity(1.0)
            idc = ((k & 255) / 255.0, ((k >> 8) & 255) / 255.0, 77 / 255.0)
            p.SetColor(*idc)
            p.SetAmbientColor(*idc)
            p.SetDiffuseColor(*idc)
            p.SetAmbient(1.0)  # with lighting off VTK still scales by ambient + diffuse
            p.SetDiffuse(0.0)
            p.SetSpecular(0.0)
        if fl is not None:
            fl.VisibilityOff()
        for act in arrow_actors:
            act.VisibilityOff()
        self.win.Render()
        idimg = self._grab()
        zbuf = self._zbuffer()
        enc = idimg[:, :, 0].astype(np.int32) + (idimg[:, :, 1].astype(np.int32) << 8)
        ids_big = np.where(idimg[:, :, 2] == 77, enc - 1, -1)
        ids = ids_big[::ss, ::ss][:h, :w]

        from PIL import Image

        rgb = np.asarray(Image.fromarray(big).resize((w, h), Image.LANCZOS))
        focus_keys = {i for i, k in enumerate(keys) if any(it.key == k and it.mode == "focus" for it in items)}
        model_mask = ids >= 0
        focus_mask = np.isin(ids, list(focus_keys)) if focus_keys else model_mask.copy()

        ren = self.ren

        def project(p):
            ren.SetWorldPoint(float(p[0]), float(p[1]), float(p[2]), 1.0)
            ren.WorldToDisplay()
            x, y, _ = ren.GetDisplayPoint()
            return (x / ss, (h * ss - y) / ss)

        def visible(p, tol=2.5e-3) -> bool:
            """True unless an opaque part lies in front of world point p (ghosted parts never hide it)."""
            ren.SetWorldPoint(float(p[0]), float(p[1]), float(p[2]), 1.0)
            ren.WorldToDisplay()
            x, y, z = ren.GetDisplayPoint()
            xi, yi = int(round(x)), int(round(y))
            if not (0 <= xi < zbuf.shape[1] and 0 <= yi < zbuf.shape[0]):
                return False
            win = zbuf[max(0, yi - 2): yi + 3, max(0, xi - 2): xi + 3]
            return bool(float(win.max()) >= z - tol)

        self.ren.SetBackground(*BG)
        return PanelRender(rgb=rgb, ids=ids, keys=keys, focus_mask=focus_mask, model_mask=model_mask,
                           camera=cam, project=project, visible=visible)


def visible_anchor(ids: np.ndarray, idx: int, prefer: Optional[tuple[float, float]] = None) -> Optional[tuple[float, float]]:
    """Deepest visible pixel of part `idx` (distance transform), biased toward `prefer` if given."""
    m = ids == idx
    if not m.any():
        return None
    from scipy import ndimage

    ys, xs = np.nonzero(m)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    sub = np.pad(m[y0:y1, x0:x1], 1)
    dt = ndimage.distance_transform_edt(sub)[1:-1, 1:-1]
    if prefer is not None:
        yy, xx = np.mgrid[y0:y1, x0:x1]
        dt = dt - 0.02 * np.hypot(xx - prefer[0], yy - prefer[1])
    k = int(np.argmax(dt))
    yy, xx = divmod(k, x1 - x0)
    return (float(xx + x0), float(yy + y0))


def ray_hit(pd, offset, origin, direction) -> Optional[tuple[float, float, float]]:
    """First intersection of a ray with a mesh (mesh translated by offset)."""
    import vtk

    tf = vtk.vtkTransform()
    tf.Translate(*offset)
    tpf = vtk.vtkTransformPolyDataFilter()
    tpf.SetInputData(pd)
    tpf.SetTransform(tf)
    tpf.Update()
    loc = vtk.vtkOBBTree()
    loc.SetDataSet(tpf.GetOutput())
    loc.BuildLocator()
    pts = vtk.vtkPoints()
    o = np.array(origin, float)
    e = o + np.array(direction, float) / (np.linalg.norm(direction) or 1.0) * 2000.0
    loc.IntersectWithLine(o.tolist(), e.tolist(), pts, None)
    if pts.GetNumberOfPoints() == 0:
        return None
    return tuple(pts.GetPoint(0))  # type: ignore[return-value]


def angle_deg(a, b) -> float:
    a, b = np.array(a, float), np.array(b, float)
    return math.degrees(math.acos(max(-1.0, min(1.0, float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) or 1))))))
