"""Mesh-based queries on B-rep solids: ray hits, plane sections, inside tests. Used for layout, not for output."""

from __future__ import annotations

import numpy as np
import trimesh
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import polygonize, unary_union

from .geom import to_trimesh


class Probe:
    def __init__(self, shape, tol: float = 0.12):
        self.shape = shape
        self.mesh: trimesh.Trimesh = to_trimesh(shape, tol, 0.25)
        self._ray = trimesh.ray.ray_triangle.RayMeshIntersector(self.mesh)

    # ---------------------------------------------------------------------------------------- rays
    def hits(self, origins, dirs, first: bool = True):
        """For each ray return (point[N,3] with NaN for misses, normal[N,3], distance[N])."""
        o = np.atleast_2d(np.asarray(origins, float))
        d = np.atleast_2d(np.asarray(dirs, float))
        if len(d) == 1 and len(o) > 1:
            d = np.repeat(d, len(o), 0)
        d = d / np.linalg.norm(d, axis=1, keepdims=True)
        n = len(o)
        pts = np.full((n, 3), np.nan)
        nrm = np.full((n, 3), np.nan)
        dist = np.full(n, np.inf)
        locs, ri, ti = self._ray.intersects_location(o, d, multiple_hits=True)
        if len(locs):
            dd = np.einsum("ij,ij->i", locs - o[ri], d[ri])
            for k in np.argsort(dd)[::-1] if not first else np.argsort(dd):
                r = ri[k]
                if dd[k] < -1e-6:
                    continue
                if first and np.isfinite(dist[r]):
                    continue
                if not first and np.isfinite(dist[r]) and dd[k] < dist[r]:
                    continue
                dist[r] = dd[k]
                pts[r] = locs[k]
                nrm[r] = self.mesh.face_normals[ti[k]]
        return pts, nrm, dist

    def all_hits(self, origin, direction):
        o = np.asarray(origin, float)[None]
        d = np.asarray(direction, float)[None]
        d = d / np.linalg.norm(d)
        locs, ri, ti = self._ray.intersects_location(o, d, multiple_hits=True)
        if not len(locs):
            return np.zeros(0), np.zeros((0, 3))
        dd = (locs - o) @ d[0]
        order = np.argsort(dd)
        return dd[order], self.mesh.face_normals[ti[order]]

    def contains(self, pts) -> np.ndarray:
        pts = np.atleast_2d(np.asarray(pts, float))
        return self.mesh.contains(pts)

    # ---------------------------------------------------------------------------------------- sections
    def section(self, axis: str, value: float):
        """Plane section -> shapely (Multi)Polygon in the plane's 2D coords.
        axis 'y': coords (x, z); axis 'z': coords (x, y); axis 'x': coords (y, z)."""
        normal = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[axis]
        origin = np.zeros(3)
        origin["xyz".index(axis)] = value
        try:
            path = self.mesh.section(plane_origin=origin, plane_normal=normal)
        except Exception:
            path = None
        if path is None:
            return Polygon()
        idx = {"x": [1, 2], "y": [0, 2], "z": [0, 1]}[axis]
        polys = []
        try:
            for ent in path.discrete:
                pts = np.asarray(ent)[:, idx]
                if len(pts) >= 4 and np.allclose(pts[0], pts[-1], atol=1e-3):
                    pg = Polygon(pts).buffer(0)
                    if not pg.is_empty and pg.area > 1e-3:
                        polys.append(pg)
        except Exception:
            return Polygon()
        if not polys:
            return Polygon()
        # even-odd nesting: a ring inside an odd number of others is a hole
        fills = polys
        keep = []
        for i, p in enumerate(fills):
            rp = p.representative_point()
            depth = sum(1 for j, f in enumerate(fills) if j != i and f.contains(rp))
            keep.append((p, depth))
        res = Polygon()
        for p, dpt in sorted(keep, key=lambda t: t[1]):
            res = res.union(p) if dpt % 2 == 0 else res.difference(p)
        return res.buffer(0)


def largest(poly):
    if isinstance(poly, MultiPolygon):
        return max(poly.geoms, key=lambda p: p.area)
    return poly
