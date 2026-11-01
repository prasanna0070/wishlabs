"""Product-shot renders (VTK offscreen via pyvista, EGL; no X server needed).

hero       three-quarter front, soft studio light, contact shadow, body colour and decor fills
rear_open  three-quarter rear with the rear cover removed (rear_cover) or the back half cut away (base_plate)
xray       three-quarter front with the shell at low opacity so the internals show
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

import numpy as np

BG_TOP = (0.965, 0.960, 0.948)
BG_BOTTOM = (0.905, 0.896, 0.878)


def _pv():
    import pyvista as pv

    pv.OFF_SCREEN = True
    return pv


def _mesh(v, n, t):
    pv = _pv()
    faces = np.hstack([np.full((len(t), 1), 3), t]).ravel()
    m = pv.PolyData(np.asarray(v, float), faces)
    if n is not None and len(n) == len(v):
        m.point_data["Normals"] = np.asarray(n, float)
        m.point_data.active_normals_name = "Normals"
    return m


def _shadow(pl, meshes, bb, offset=(4.0, 9.0), strength=0.38, blur_mm=9.0):
    """Blurred footprint texture on a plane just above z=0."""
    from PIL import Image, ImageDraw, ImageFilter

    pv = _pv()
    res = 0.5
    pad = 40.0
    x0, x1, y0, y1 = bb[0] - pad, bb[1] + pad, bb[2] - pad, bb[3] + pad
    W, H = int((x1 - x0) / res), int((y1 - y0) / res)
    img = Image.new("L", (W, H), 0)
    dr = ImageDraw.Draw(img)
    for m in meshes:
        pts = np.asarray(m.points)
        if not len(pts):
            continue
        tri = m.faces.reshape(-1, 4)[:, 1:]
        low = pts[:, 2] < bb[4] + 25.0
        keep = low[tri].any(1)
        for a, b, c in tri[keep][:: max(1, int(keep.sum() / 60000))]:
            P = [((pts[i, 0] - x0 + offset[0]) / res, (y1 - pts[i, 1] - offset[1]) / res) for i in (a, b, c)]
            dr.polygon(P, fill=255)
    img = img.filter(ImageFilter.GaussianBlur(blur_mm / res))
    a = (np.asarray(img, float) / 255.0 * strength * 255).astype(np.uint8)
    rgba = np.zeros((H, W, 4), np.uint8)
    rgba[..., 0:3] = (np.array([0.32, 0.30, 0.28]) * 255).astype(np.uint8)
    rgba[..., 3] = a
    tex = pv.Texture(rgba[::-1].copy())
    plane = pv.Plane(center=((x0 + x1) / 2, (y0 + y1) / 2, bb[4] + 0.05), direction=(0, 0, 1), i_size=x1 - x0, j_size=y1 - y0)
    plane.texture_map_to_plane(inplace=True)
    pl.add_mesh(plane, texture=tex, opacity=1.0, lighting=False, show_scalar_bar=False)


def _lights(pl, c, R):
    pv = _pv()
    pl.remove_all_lights()
    key = pv.Light(position=(c[0] - 1.2 * R, c[1] - 1.6 * R, c[2] + 2.0 * R), focal_point=c, color=(1.0, 0.97, 0.92), intensity=0.85)
    fill = pv.Light(position=(c[0] + 2.0 * R, c[1] - 1.0 * R, c[2] + 0.6 * R), focal_point=c, color=(0.92, 0.95, 1.0), intensity=0.42)
    rim = pv.Light(position=(c[0] + 0.5 * R, c[1] + 2.2 * R, c[2] + 1.8 * R), focal_point=c, color=(1.0, 1.0, 1.0), intensity=0.45)
    top = pv.Light(position=(c[0], c[1], c[2] + 3 * R), focal_point=c, color=(1.0, 1.0, 1.0), intensity=0.18)
    for L in (key, fill, rim, top):
        L.positional = False
        pl.add_light(L)


def _camera(pl, bb, direction, zoom=1.0, view_angle=24.0, aspect=1.0, lift=0.0):
    c = np.array([(bb[0] + bb[1]) / 2, (bb[2] + bb[3]) / 2, (bb[4] + bb[5]) / 2 + lift])
    ext = np.array([bb[1] - bb[0], bb[3] - bb[2], bb[5] - bb[4]])
    R = 0.5 * float(np.linalg.norm(ext))
    d = np.asarray(direction, float)
    d /= np.linalg.norm(d)
    dist = R / math.tan(math.radians(view_angle / 2)) / zoom * 1.08
    pl.camera.position = tuple(c + d * dist)
    pl.camera.focal_point = tuple(c)
    pl.camera.up = (0, 0, 1)
    pl.camera.view_angle = view_angle
    pl.camera.clipping_range = (dist * 0.05, dist * 4)
    return c, R


def _finish(pl, path: Path, size, ss: int):
    from PIL import Image

    img = pl.screenshot(return_img=True)
    pl.close()
    im = Image.fromarray(img).convert("RGB")
    if ss > 1:
        im = im.resize(size, Image.LANCZOS)
    im.save(path, optimize=True)
    return path


def _plotter(size, ss):
    pv = _pv()
    pl = pv.Plotter(off_screen=True, window_size=(size[0] * ss, size[1] * ss), lighting="none")
    pl.set_background(BG_BOTTOM, top=BG_TOP)
    return pl


def _add(pl, mesh, color, opacity=1.0, spec=0.22, power=24.0, smooth=True):
    pl.add_mesh(mesh, color=tuple(color[:3]), opacity=opacity, smooth_shading=smooth, specular=spec, specular_power=power,
                diffuse=0.88, ambient=0.16, show_scalar_bar=False)


def items_from_model(model) -> list:
    from .export import model_primitives

    out = []
    for name, prims, alpha in model_primitives(model):
        for sub, v, n, t, col in prims:
            out.append((name, sub, v, n, t, col, alpha))
    return out


def items_from_glb(path) -> list:
    from .export import glb_items

    return glb_items(path)


def _sel(items, names=None, skip=()):
    out = []
    for name, sub, v, n, t, col, alpha in items:
        if names is not None and name not in names:
            continue
        if any(name.startswith(s) for s in skip):
            continue
        out.append((name, sub, _mesh(v, n, t), col, alpha))
    return out


def _bbox_items(items, names):
    pts = [v for name, sub, v, n, t, col, a in items if name in names] or [v for name, sub, v, n, t, col, a in items]
    P = np.vstack(pts)
    return [P[:, 0].min(), P[:, 0].max(), P[:, 1].min(), P[:, 1].max(), P[:, 2].min(), P[:, 2].max()]


def _enable_fx(pl, R, ssao=True):
    if not ssao:
        return
    try:
        pl.enable_ssao(radius=max(R * 0.05, 2.0), bias=0.6, kernel_size=48, blur=True)
    except Exception:
        pass


SHELLS = ("PRINT_Shell", "PRINT_RearCover", "PRINT_BasePlate")


def _decals(pl, screens):
    """Illustrative idle face on displays (render only; not part of the CAD or GLB)."""
    from shapely import affinity
    from shapely.geometry import LineString, Point

    pv = _pv()
    for sc in screens or []:
        c, u, v, n = (np.asarray(sc[k], float) for k in ("c", "u", "v", "n"))
        w, h = float(sc["w"]), float(sc["h"])
        shapes = []
        if sc.get("kind") == "lcd":
            for sx in (-1, 1):
                eye = affinity.scale(Point(sx * 0.2 * w, 0.06 * h).buffer(1, 32), 0.11 * w, 0.2 * h)
                pup = affinity.scale(Point(sx * 0.2 * w + 0.015 * w, 0.0).buffer(1, 32), 0.055 * w, 0.11 * h)
                hi = Point(sx * 0.2 * w + 0.04 * w, 0.1 * h).buffer(0.022 * w, 16)
                shapes += [(eye, (0.98, 0.98, 0.97), 0.02), (pup, (0.16, 0.12, 0.12), 0.04), (hi, (1, 1, 1), 0.06)]
            sm = LineString([(-0.07 * w, -0.24 * h), (0, -0.30 * h), (0.07 * w, -0.24 * h)]).buffer(0.012 * w, cap_style="round")
            shapes.append((sm, (0.98, 0.98, 0.97), 0.02))
            for sx in (-1, 1):
                shapes.append((affinity.scale(Point(sx * 0.36 * w, -0.16 * h).buffer(1, 24), 0.06 * w, 0.04 * h), (0.98, 0.62, 0.66), 0.02))
        else:
            for sx in (-1, 1):
                pill = LineString([(sx * 0.21 * w, -0.16 * h), (sx * 0.21 * w, 0.16 * h)]).buffer(0.075 * w, cap_style="round")
                shapes.append((pill, (0.80, 0.94, 1.0), 0.02))
        for poly, col, dz in shapes:
            xy = np.asarray(poly.exterior.coords)[:-1]
            P = c[None] + xy[:, :1] * u[None] + xy[:, 1:2] * v[None] + n[None] * dz
            m = pv.PolyData(P, np.r_[len(P), np.arange(len(P))]).triangulate()
            pl.add_mesh(m, color=col, lighting=False, show_scalar_bar=False)


def hero(items, path: Path, size=(1400, 1400), ss=2, direction=(0.52, -1.0, 0.40), ssao=True, screens=None, **_) -> Path:
    pl = _plotter(size, ss)
    sel = _sel(items, skip=("REFERENCE_",))
    bb = _bbox_items(items, SHELLS)
    c, R = _camera(pl, bb, direction, zoom=1.0)
    _lights(pl, c, R)
    for name, sub, m, col, alpha in sel:
        glossy = sub in ("Glass", "Screen")
        if sub == "Diffuser":
            pl.add_mesh(m, color=(1.0, 0.975, 0.90), smooth_shading=True, ambient=0.62, diffuse=0.42, specular=0.05, show_scalar_bar=False)
            continue
        _add(pl, m, col, opacity=alpha, spec=0.6 if glossy else (0.16 if name.startswith("PRINT_") else 0.3), power=60 if glossy else 20)
    _decals(pl, screens)
    _shadow(pl, [m for _, _, m, _, _ in sel], bb)
    _enable_fx(pl, R, ssao)
    return _finish(pl, path, size, ss)


def rear_open(items, path: Path, size=(1400, 1400), ss=2, split="rear_cover", ssao=True, **_) -> Path:
    pv = _pv()
    pl = _plotter(size, ss)
    skip = ("PRINT_RearCover", "PURCHASE_CoverScrews") if split == "rear_cover" else ()
    sel = _sel(items, skip=skip)
    bb = _bbox_items(items, ("PRINT_Shell",))
    yc = (bb[2] + bb[3]) / 2 + (bb[3] - bb[2]) * 0.08
    c, R = _camera(pl, bb, (-0.62, 1.0, 0.62), zoom=1.0)
    _lights(pl, c, R)
    pl.add_light(pv.Light(position=(c[0] - R, c[1] + 3 * R, c[2] + R), focal_point=c, intensity=0.35))
    for name, sub, m, col, alpha in sel:
        mm = m
        if split != "rear_cover" and name == "PRINT_Shell":
            mm = m.clip(normal=(0, 1, 0), origin=(0, yc, 0), invert=True)
        if mm.n_points == 0:
            continue
        _add(pl, mm, col, opacity=alpha, spec=0.2)
    _shadow(pl, [m for _, _, m, _, _ in sel], bb, offset=(-4, -6))
    _enable_fx(pl, R, ssao)
    return _finish(pl, path, size, ss)


def xray(items, path: Path, size=(1400, 1400), ss=2, **_) -> Path:
    pl = _plotter(size, ss)
    try:
        pl.enable_depth_peeling(number_of_peels=8)
    except Exception:
        pass
    sel = _sel(items)
    bb = _bbox_items(items, SHELLS)
    c, R = _camera(pl, bb, (0.62, -1.0, 0.55), zoom=1.0)
    _lights(pl, c, R)
    for name, sub, m, col, alpha in sel:
        if name in SHELLS:
            _add(pl, m, tuple(0.8 * x for x in col[:3]), opacity=0.22, spec=0.05)
            try:
                pl.add_silhouette(m, color=tuple(0.45 * x for x in col[:3]), line_width=2.5)
            except Exception:
                pass
        else:
            _add(pl, m, col, opacity=1.0, spec=0.25)
    return _finish(pl, path, size, ss)


def render_all(model_or_items, out_dir: Path, size=(1400, 1400), ss=2, split: str = None, ssao=True, screens=None) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if isinstance(model_or_items, list):
        items = model_or_items
    else:
        items = items_from_model(model_or_items)
        split = split or model_or_items.info.get("split")
        screens = screens if screens is not None else model_or_items.info.get("screens")
    res = {}
    for name, fn in (("hero", hero), ("rear_open", rear_open), ("xray", xray)):
        try:
            res[name] = str(fn(items, out_dir / f"{name}.png", size, ss, split=split or "rear_cover", ssao=ssao, screens=screens))
        except Exception as e:  # pragma: no cover
            res[name] = f"failed: {e}"
    return res


def render_preview(parts: list, face_colors: dict, path: Path, size=(1200, 1200), ss=2, screens=None) -> Path:
    """parts: [Part]; same lighting as hero."""
    from .export import part_primitives

    items = []
    for p in parts:
        for sub, v, n, t, col in part_primitives(p, face_colors.get(p.name), tol=0.1, ang=0.3):
            items.append((p.name, sub, v, n, t, col, p.alpha))
    return hero(items, path, size, ss, screens=screens)
