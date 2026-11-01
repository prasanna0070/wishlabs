"""Offscreen renders of the assembly (pyvista/VTK, EGL) and the wiring diagram (matplotlib).

Step images: the step's highlight parts in their own colour, everything else ghosted.
"""
from __future__ import annotations

import math

import numpy as np

GHOST = "#C9C4B8"
BG_TOP, BG_BOTTOM = "#FFFFFF", "#ECEAE4"


class Scene:
    def __init__(self, meshes: dict, colors: dict, size=(1200, 900)):
        import pyvista as pv

        self.pv = pv
        p = pv.Plotter(off_screen=True, window_size=list(size), lighting="none")
        p.set_background(BG_BOTTOM, top=BG_TOP)
        for pos, inten in (((-420, -620, 640), 0.70), ((520, -260, 260), 0.30), ((260, 620, 520), 0.42), ((0, -200, -300), 0.10)):
            p.add_light(pv.Light(position=pos, focal_point=(0, 0, 80), intensity=inten, light_type="scene light"))
        p.add_light(pv.Light(light_type="headlight", intensity=0.28))
        self.actors = {}
        self.colors = colors
        for name, (V, N, F) in meshes.items():
            faces = np.hstack([np.full((len(F), 1), 3, dtype=np.int64), F.astype(np.int64)]).ravel()
            pd = pv.PolyData(V, faces)
            pd.point_data.active_normals = N
            a = p.add_mesh(pd, color=colors.get(name, "#CCCCCC"), smooth_shading=True, ambient=0.28, diffuse=0.72,
                           specular=0.18, specular_power=24, name=name)
            self.actors[name] = a
        p.enable_depth_peeling(number_of_peels=8, occlusion_ratio=0.0)
        p.enable_anti_aliasing("ssaa")
        self.p = p
        p.show(auto_close=False, interactive=False)

    def shot(self, path, camera: dict, highlight=None, ghost_opacity=0.11, offsets=None, hide=(), up=(0, 0, 1),
             view_angle=28.0, ghost_small=0.22):
        offsets = offsets or {}
        for name, a in self.actors.items():
            prop = a.GetProperty()
            a.SetVisibility(name not in hide)
            a.SetPosition(*offsets.get(name, (0.0, 0.0, 0.0)))
            if highlight is None or name in highlight:
                prop.SetColor(self.pv.Color(self.colors.get(name, "#CCCCCC")).float_rgb)
                prop.SetOpacity(1.0)
            else:
                prop.SetColor(self.pv.Color(GHOST).float_rgb)
                big = name.startswith(("PRINT_Shell", "PRINT_RearCover"))
                prop.SetOpacity(ghost_opacity if big else ghost_small)
        cam = self.p.camera
        cam.position = tuple(camera["position"])
        cam.focal_point = tuple(camera["target"])
        cam.up = up
        cam.view_angle = view_angle
        self.p.render()
        self.p.screenshot(str(path))

    def close(self):
        self.p.close()


# ------------------------------------------------------------------ wiring diagram
WIRE = {"black": "#1B1F24", "red": "#D1495B", "white": "#B9B3A6", "green": "#2A9D8F", "yellow": "#D9A21B",
        "blue": "#3046D9", "orange": "#F08A24", "grey": "#8A8F98", "gray": "#8A8F98", "brown": "#8B5A2B", "purple": "#7B4FB5"}


def wiring_diagram(png_path, svg_path, electrical: dict, title: str):
    """Backplane slots on the left, modules on the right, one coloured line per wire, labelled with its net."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch

    insts = {i["ref"]: i for i in electrical["instances"]}
    cabled = [i for i in electrical["instances"] if i.get("harness")]
    socketed = [i for i in electrical["instances"] if i.get("slot") in ("A1", "S1", "U1")]
    H = 2.3 + sum(0.36 * len(i["harness"]["wires"]) + 0.95 for i in cabled) + 1.0 * len(socketed) + 1.2
    fig, ax = plt.subplots(figsize=(13, H))
    ax.set_xlim(0, 13)
    ax.set_ylim(H, 0)
    ax.axis("off")
    fig.patch.set_facecolor("#FFFFFF")
    ink, line, panel = "#1B1F24", "#D8D4C8", "#F6F5F1"
    ax.text(0.3, 0.55, title, fontsize=16, fontweight="bold", color=ink, va="center")
    ax.text(0.3, 1.05, "Backplane ports (left) to modules (right). Wire colours as in electrical.json. DEV FIXTURE.",
            fontsize=9.5, color="#5A5F66", va="center")
    ax.text(1.75, 1.7, "BACKPLANE (BP1)", fontsize=10, fontweight="bold", color="#1F6B45", ha="center")
    ax.text(10.9, 1.7, "MODULES", fontsize=10, fontweight="bold", color="#3046D9", ha="center")
    y = 2.3
    for inst in cabled:
        h = inst["harness"]
        wires = h["wires"]
        n = len(wires)
        box_h = 0.36 * n + 0.5
        ax.add_patch(FancyBboxPatch((0.4, y), 2.7, box_h, boxstyle="round,pad=0.02,rounding_size=0.12", fc="#E8F2EC", ec="#1F6B45", lw=1.2))
        ax.add_patch(FancyBboxPatch((9.6, y), 2.7, box_h, boxstyle="round,pad=0.02,rounding_size=0.12", fc="#E9ECFB", ec="#3046D9", lw=1.2))
        ax.text(0.55, y + 0.25, f"{inst['slot']}", fontsize=10, fontweight="bold", color=ink, va="center")
        ax.text(3.0, y + 0.25, h.get("cable", ""), fontsize=7.5, color="#5A5F66", va="center", ha="right")
        nm = inst.get("name", inst["ref"])
        ax.text(9.75, y + 0.25, f"{inst['ref']}", fontsize=10, fontweight="bold", color=ink, va="center")
        ax.text(12.2, y + 0.25, (nm[:30] + "...") if len(nm) > 33 else nm, fontsize=7, color="#5A5F66", va="center", ha="right")
        for k, w in enumerate(wires):
            yy = y + 0.62 + 0.36 * k
            c = WIRE.get(str(w.get("color", "grey")).lower(), "#8A8F98")
            ax.text(0.6, yy, f"{w['slot_pin']}  {w.get('slot_pin_name', '')}", fontsize=8.5, color=ink, va="center", family="monospace")
            ax.text(9.8, yy, f"{w['module_pin']}", fontsize=8.5, color=ink, va="center", family="monospace")
            ax.plot([3.1, 9.6], [yy, yy], color=c, lw=3.2, solid_capstyle="round", zorder=2)
            if str(w.get("color")).lower() == "white":
                ax.plot([3.1, 9.6], [yy, yy], color="#8F887A", lw=0.6, zorder=3)
            for xx in (3.1, 9.6):
                ax.plot([xx], [yy], marker="o", ms=5, color=c, mec=ink, mew=0.6, zorder=4)
            ax.text(6.35, yy - 0.12, f"{w.get('net', '')}  ({w.get('color', '')})", fontsize=7.5, color="#3A3F46", ha="center", va="bottom",
                    bbox=dict(fc="white", ec="none", pad=0.6, alpha=0.85), zorder=5)
        y += box_h + 0.45
    for inst in socketed:
        ax.add_patch(FancyBboxPatch((0.4, y), 2.7, 0.62, boxstyle="round,pad=0.02,rounding_size=0.12", fc="#E8F2EC", ec="#1F6B45", lw=1.2))
        ax.add_patch(FancyBboxPatch((9.6, y), 2.7, 0.62, boxstyle="round,pad=0.02,rounding_size=0.12", fc="#F1EFEA", ec="#7A7F86", lw=1.2))
        ax.text(0.55, y + 0.31, inst["slot"], fontsize=10, fontweight="bold", color=ink, va="center")
        ax.text(9.75, y + 0.31, inst["ref"], fontsize=10, fontweight="bold", color=ink, va="center")
        nm = inst.get("name", "")
        ax.text(12.2, y + 0.31, (nm[:30] + "...") if len(nm) > 33 else nm, fontsize=7, color="#5A5F66", va="center", ha="right")
        ax.plot([3.1, 9.6], [y + 0.31, y + 0.31], color="#9AA0A6", lw=6, alpha=0.35, solid_capstyle="round")
        ax.text(6.35, y + 0.31, "plugs straight into its pin socket (no cable)", fontsize=8, color="#3A3F46", ha="center", va="center",
                bbox=dict(fc="white", ec="none", pad=0.8))
        y += 1.0
    ax.text(0.4, y + 0.3, "Power: USB-C 5 V into BP1.J_PWR (rear). Physical checks still needed.", fontsize=9, color="#5A5F66")
    fig.tight_layout()
    fig.savefig(png_path, dpi=110, facecolor=fig.get_facecolor())
    fig.savefig(svg_path, facecolor=fig.get_facecolor())
    plt.close(fig)


# ------------------------------------------------------------------ fallback board drawing (when the PCB stage is unavailable)
def board_drawing(path, bp: dict, used: list[str], side: str = "top"):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, FancyBboxPatch, Rectangle

    W, H = bp["board"]["size"]
    fig, ax = plt.subplots(figsize=(9, 9 * H / W * 0.9 + 0.8))
    ax.set_aspect("equal")
    ax.set_xlim(-8, W + 8)
    ax.set_ylim(-8, H + 8)
    ax.axis("off")
    fig.patch.set_facecolor("#ECEAE4")
    mirror = (lambda x: W - x) if side == "bottom" else (lambda x: x)
    ax.add_patch(FancyBboxPatch((0, 0), W, H, boxstyle="round,pad=0,rounding_size=1.0", fc="#1F6B45", ec="#0F3D27", lw=2))
    for h in bp["board"]["holes"]:
        ax.add_patch(Circle((mirror(h["at"][0]), h["at"][1]), h["d"] / 2 + 0.8, fc="#C9A227", ec="none"))
        ax.add_patch(Circle((mirror(h["at"][0]), h["at"][1]), h["d"] / 2, fc="#ECEAE4", ec="none"))
    for p in bp["parts"]:
        cy = p.get("courtyard")
        if not cy or p["ref"].startswith(("H",)):
            continue
        on_side = p.get("side", "top") == side
        if not on_side:
            continue
        if p.get("slot") and p["slot"] not in used:
            continue
        x0, x1 = sorted((mirror(cy[0]), mirror(cy[1])))
        fc = "#F2EFE6" if "JST" in p.get("footprint", "") else ("#202326" if "PinSocket" in p.get("footprint", "") else "#B8BCC2")
        ax.add_patch(Rectangle((x0 + 0.3, cy[2] + 0.3), x1 - x0 - 0.6, cy[3] - cy[2] - 0.6, fc=fc, ec="#0F3D27", lw=0.6))
        ax.text((x0 + x1) / 2, cy[3] + 0.6, p["ref"], fontsize=6.5, color="white", ha="center", va="bottom")
    ax.text(W / 2, -4.5, f"backplane-v1  {W:g} x {H:g} mm  ({side})  DEV FIXTURE drawing, not a KiCad render",
            fontsize=8, color="#3A3F46", ha="center")
    fig.savefig(path, dpi=140, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
