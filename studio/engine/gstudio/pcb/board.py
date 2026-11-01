"""Board (.kicad_pcb) construction with the pcbnew 9 Python API.

Board coordinates (mm, Y up, origin lower-left) map to KiCad page coordinates (Y down) with the board's
lower-left corner at (X0, Y0 + H). The drill/place (aux) origin is set to the board's lower-left corner, so
Gerbers, drill files and the CPL use board coordinates.
"""

from __future__ import annotations

import math
import os
import uuid
from pathlib import Path

import pcbnew

from ..errors import StageError
from .design import POWER_NETS, Design, Part, net_class
from .genlib import LIB

FP_DIR = Path(os.environ.get("KICAD9_FOOTPRINT_DIR", "/usr/share/kicad/footprints"))
X0, Y0 = 100.0, 60.0
POWER_SYMBOL_NETS = {"GND", "+5V", "+3V3", "VBUS"}

RULES = {
    "min_clearance": 0.15, "min_track": 0.15, "min_via_diameter": 0.5, "min_via_drill": 0.3,
    "min_annular": 0.1, "hole_clearance": 0.25, "hole_to_hole": 0.25, "copper_edge": 0.3,
    "min_silk_text_height": 0.8, "min_silk_text_thickness": 0.12,
}
CLASSES = {
    "Default": {"clearance": 0.18, "route_clearance": 0.2, "track": 0.25, "via_d": 0.6, "via_drill": 0.3, "nets": [],
                "why": "signals: routed at 0.20 mm clearance, checked at 0.18 mm (room to widen Freerouting neck-downs)"},
    "Power": {"clearance": 0.15, "track": 0.8, "via_d": 0.8, "via_drill": 0.4, "nets": ["+5V", "GND"],
              "why": "backplane-v1 net_classes power_track_mm 0.8"},
    "PowerFine": {"clearance": 0.15, "track": 0.6, "via_d": 0.6, "via_drill": 0.3, "nets": ["VBUS", "+3V3"],
                  "why": "power at 0.6 mm where the USB-C VBUS pads (0.6 wide, 0.2 mm gaps) and 2.54 mm headers allow"},
}


def mm(v: float) -> int:
    return pcbnew.FromMM(float(v))


class Frame:
    def __init__(self, design: Design):
        self.W, self.H = design.width, design.height

    def pt(self, x: float, y: float) -> pcbnew.VECTOR2I:
        return pcbnew.VECTOR2I(mm(X0 + x), mm(Y0 + self.H - y))

    def to_board(self, v: pcbnew.VECTOR2I) -> tuple[float, float]:
        return (round(pcbnew.ToMM(v.x) - X0, 4), round(self.H - (pcbnew.ToMM(v.y) - Y0), 4))


def board_net_name(net: str) -> str:
    """Schematic local labels become '/NAME' nets; power symbols give global names."""
    return net if net in POWER_SYMBOL_NETS else "/" + net


# ----------------------------------------------------------------------------- footprints

def load_footprint(fpid: str, project_pretty: Path) -> pcbnew.FOOTPRINT:
    lib, name = fpid.split(":", 1)
    libdir = project_pretty if lib == LIB else FP_DIR / f"{lib}.pretty"
    fp = pcbnew.FootprintLoad(str(libdir), name)
    if fp is None:
        raise StageError(f"footprint {fpid} not found", {"libdir": str(libdir)})
    fp.SetFPID(pcbnew.LIB_ID(lib, name))
    return fp


def courtyard_bbox(fp: pcbnew.FOOTPRINT, frame: Frame) -> tuple[float, float, float, float]:
    """Courtyard bbox in board coords (xmin, ymin, xmax, ymax). Falls back to the footprint bbox."""
    xs, ys = [], []
    lay = {pcbnew.F_CrtYd, pcbnew.B_CrtYd}
    for g in fp.GraphicalItems():
        if g.GetLayer() in lay:
            bb = g.GetBoundingBox()
            for v in (bb.GetOrigin(), bb.GetEnd()):
                x, y = frame.to_board(v)
                xs.append(x)
                ys.append(y)
    if not xs:
        bb = fp.GetBoundingBox(False)
        for v in (bb.GetOrigin(), bb.GetEnd()):
            x, y = frame.to_board(v)
            xs.append(x)
            ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys))


def _overlap(a, b, gap=0.0) -> bool:
    return not (a[2] + gap <= b[0] or b[2] + gap <= a[0] or a[3] + gap <= b[1] or b[3] + gap <= a[1])


def _inside(a, W, H, m) -> bool:
    return a[0] >= m and a[1] >= m and a[2] <= W - m and a[3] <= H - m


# ----------------------------------------------------------------------------- the builder

class BoardBuilder:
    def __init__(self, design: Design, pcb_path: Path, sym_uuid):
        self.d = design
        self.path = pcb_path
        self.frame = Frame(design)
        self.sym_uuid = sym_uuid
        seed = int(design.design_id[:8], 16)
        pcbnew.KIID.SeedGenerator(seed)
        self.board = pcbnew.NewBoard(str(pcb_path))
        self.nets: dict[str, pcbnew.NETINFO_ITEM] = {}
        self.fps: dict[str, pcbnew.FOOTPRINT] = {}
        self.boxes: dict[str, tuple] = {}
        self.placement_log: list[dict] = []

    # ---------------------------------------------------------------- setup
    def setup(self):
        b, d = self.board, self.d
        b.SetCopperLayerCount(2)
        ds = b.GetDesignSettings()
        ds.SetBoardThickness(mm(d.thickness))
        ds.m_MinClearance = mm(RULES["min_clearance"])
        ds.m_TrackMinWidth = mm(RULES["min_track"])
        ds.m_ViasMinSize = mm(RULES["min_via_diameter"])
        ds.m_MinThroughDrill = mm(RULES["min_via_drill"])
        ds.m_ViasMinAnnularWidth = mm(RULES["min_annular"])
        ds.m_HoleClearance = mm(RULES["hole_clearance"])
        ds.m_HoleToHoleMin = mm(RULES["hole_to_hole"])
        ds.m_CopperEdgeClearance = mm(RULES["copper_edge"])
        ds.m_MinSilkTextHeight = mm(RULES["min_silk_text_height"])
        ds.m_MinSilkTextThickness = mm(RULES["min_silk_text_thickness"])
        ds.SetAuxOrigin(self.frame.pt(0, 0))
        ds.SetGridOrigin(self.frame.pt(0, 0))
        ns = ds.m_NetSettings
        for name, c in CLASSES.items():
            nc = ns.GetDefaultNetclass() if name == "Default" else pcbnew.NETCLASS(name)
            nc.SetClearance(mm(c["clearance"]))
            nc.SetTrackWidth(mm(c["track"]))
            nc.SetViaDiameter(mm(c["via_d"]))
            nc.SetViaDrill(mm(c["via_drill"]))
            nc.SetDescription(c["why"])
            if name != "Default":
                ns.SetNetclass(name, nc)
                for n in c["nets"]:
                    ns.SetNetclassPatternAssignment(board_net_name(n), name)
        tb = b.GetTitleBlock()
        tb.SetTitle(f"BP1 backplane {d.backplane_id} for {d.slug}")  # no project name: reused across versions (MIN-36)
        tb.SetDate(d.date)
        tb.SetRevision(d.revision)
        tb.SetCompany("Gadget Studio")
        tb.SetComment(0, f"Engine {d.engine_version}; design id {d.design_id}")
        tb.SetComment(1, "2-layer FR-4 1.6 mm, 1 oz; JLCPCB/PCBPower-class rules (0.15 mm clearance, 0.3 mm vias)")
        tb.SetComment(2, "Digital checks only; no physical test made.")

    def add_nets(self):
        for net in sorted(self.d.nets()):
            name = board_net_name(net)
            ni = pcbnew.NETINFO_ITEM(self.board, name)
            self.board.Add(ni)
            self.nets[net] = ni
        ns = self.board.GetDesignSettings().m_NetSettings
        for net, ni in self.nets.items():
            ni.SetNetClass(ns.GetEffectiveNetClass(ni.GetNetname()))

    # ---------------------------------------------------------------- outline
    def outline(self):
        W, H, r = self.d.width, self.d.height, self.d.corner_radius
        f = self.frame
        segs = [((r, 0), (W - r, 0)), ((W, r), (W, H - r)), ((W - r, H), (r, H)), ((0, H - r), (0, r))]
        for a, b_ in segs:
            s = pcbnew.PCB_SHAPE(self.board)
            s.SetShape(pcbnew.SHAPE_T_SEGMENT)
            s.SetStart(f.pt(*a))
            s.SetEnd(f.pt(*b_))
            s.SetLayer(pcbnew.Edge_Cuts)
            s.SetWidth(mm(0.1))
            self.board.Add(s)
        k = 1 - math.sqrt(0.5)
        arcs = [((W - r, 0), (W - r * k, r * k), (W, r)), ((W, H - r), (W - r * k, H - r * k), (W - r, H)),
                ((r, H), (r * k, H - r * k), (0, H - r)), ((0, r), (r * k, r * k), (r, 0))]
        for a, m_, b_ in arcs:
            s = pcbnew.PCB_SHAPE(self.board)
            s.SetShape(pcbnew.SHAPE_T_ARC)
            s.SetArcGeometry(f.pt(*a), f.pt(*m_), f.pt(*b_))
            s.SetLayer(pcbnew.Edge_Cuts)
            s.SetWidth(mm(0.1))
            self.board.Add(s)

    # ---------------------------------------------------------------- footprints
    def _place_fp(self, part: Part, at, rot) -> pcbnew.FOOTPRINT:
        fp = load_footprint(part.footprint, self.path.parent / f"{LIB}.pretty")
        fp.SetReference(part.ref)
        fp.SetValue(part.value)
        self.board.Add(fp)
        fp.SetPosition(self.frame.pt(*at))
        fp.SetOrientationDegrees(float(rot))
        if part.side == "bottom":
            fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
        fp.SetExcludedFromBOM(not part.in_bom)
        fp.SetExcludedFromPosFiles(part.exclude_pos)
        if part.board_only:
            fp.SetBoardOnly(True)
        else:
            su = self.sym_uuid(part.ref)
            fp.SetPath(pcbnew.KIID_PATH(f"/{su}"))
            fp.SetSheetname("/")
            fp.SetSheetfile("backplane.kicad_sch")
        for k, v in part.bom.items():
            if k in ("mpn", "lcsc") and v:
                fld = pcbnew.PCB_FIELD(fp, fp.GetNextFieldId() if hasattr(fp, "GetNextFieldId") else 0, k.upper())
                fld.SetText(str(v))
                fld.SetVisible(False)
                fld.SetLayer(pcbnew.F_Fab)
                fp.AddField(fld)
        for pad in fp.Pads():
            net = part.pads.get(pad.GetNumber(), "")
            if net:
                pad.SetNet(self.nets[net])
        return fp

    def place(self):
        d = self.d
        W, H = d.width, d.height
        fixed = [p for p in d.parts if p.anchor is None]
        movable = [p for p in d.parts if p.anchor is not None]
        for p in fixed:
            fp = self._place_fp(p, p.at, p.rot)
            if p.slot == "J_PWR":
                self._align_to_edge(p, fp)
            self.fps[p.ref] = fp
            self.boxes[p.ref] = courtyard_bbox(fp, self.frame)
            self.placement_log.append({"ref": p.ref, "at": list(p.at), "rot": p.rot, "how": "slot"})
        keep = [k["rect"] for k in d.keepouts]
        for p in movable:
            anchor = self.fps.get(p.anchor)
            if p.anchor == "@board":
                ax, ay = 0.0, 0.0
            else:
                ax, ay = self.frame.to_board(anchor.GetPosition()) if anchor else (W / 2, H / 2)
            ox, oy = p.offset or (0.0, 0.0)
            best = None
            fp = self._place_fp(p, (ax + ox, ay + oy), p.rot)
            for r_ in (p.rot, (p.rot + 90) % 360):
                fp.SetOrientationDegrees(float(r_))
                for dx, dy in _spiral(0.635, 16.0):
                    at = (round(ax + ox + dx, 3), round(ay + oy + dy, 3))
                    fp.SetPosition(self.frame.pt(*at))
                    bb = courtyard_bbox(fp, self.frame)
                    if not _inside(bb, W, H, 0.6):
                        continue
                    # fiducials also keep 3.5 mm clear of port connectors: that strip holds the port's pin labels
                    gap = {r: (3.5 if p.ref.startswith("FID") and r.startswith("J_") and not r.startswith("J_U1")
                               else 0.15) for r in self.boxes}
                    if any(_overlap(bb, o, gap[r]) for r, o in self.boxes.items()):
                        continue
                    if any(_overlap(bb, k) for k in keep):
                        continue
                    best = (fp, at, r_, bb, math.hypot(dx, dy))
                    break
                if best:
                    break
            if best is None:
                raise StageError(f"no free place for {p.ref} near {p.anchor}", {"ref": p.ref})
            fp, at, r_, bb, dist = best
            fp.SetOrientationDegrees(float(r_))
            fp.SetPosition(self.frame.pt(*at))
            self.fps[p.ref] = fp
            self.boxes[p.ref] = bb
            p.at, p.rot = at, r_
            self.placement_log.append({"ref": p.ref, "at": list(at), "rot": r_, "how": f"near {p.anchor}",
                                       "moved_mm": round(dist, 2)})

    def _align_to_edge(self, part: Part, fp) -> None:
        """Move an edge connector so its footprint's own 'PCB Edge' line (Dwgs.User) sits on the nearest board edge."""
        lines = []
        for g in fp.GraphicalItems():
            if g.GetLayer() == pcbnew.Dwgs_User and g.GetClass() == "PCB_SHAPE" and g.GetShape() == pcbnew.SHAPE_T_SEGMENT:
                a, b_ = self.frame.to_board(g.GetStart()), self.frame.to_board(g.GetEnd())
                if abs(a[1] - b_[1]) < 0.01 and abs(a[0] - b_[0]) > 5.0:
                    lines.append(a[1])
        if not lines:
            return
        y_edge = min(lines, key=lambda y: min(abs(y - self.d.height), abs(y)))
        target = self.d.height if abs(y_edge - self.d.height) < abs(y_edge) else 0.0
        dy = round(target - y_edge, 4)
        if abs(dy) < 0.005 or abs(dy) > 2.0:
            return
        x, y = self.frame.to_board(fp.GetPosition())
        fp.SetPosition(self.frame.pt(x, y + dy))
        part.at = (x, round(y + dy, 4))
        self.d.deviations.append(f"{part.ref}: moved {dy:+.3f} mm in Y so the footprint's 'PCB Edge' line meets the "
                                 f"board edge (backplane-v1 position was an estimate)")

    def envelope_models(self, pcb_dir: Path, slots: dict) -> list[str]:
        """Parts whose KiCad footprint has no 3D model in the installed library get a plain envelope STEP (labelled as
        such): width/depth from the footprint's own F.Fab outline, height from backplane-v1. Only the USB-C receptacle
        needs it today (KiCad 9.0.9 ships no model for HRO TYPE-C-31-M-12)."""
        made = []
        model_dir = Path(os.environ.get("KICAD9_3DMODEL_DIR", "/usr/share/kicad/3dmodels"))
        for p in self.d.parts:
            fp = self.fps.get(p.ref)
            if fp is None or p.slot != "J_PWR":
                continue
            models = list(fp.Models())
            if models and all((model_dir / m.m_Filename.split("}/", 1)[-1]).exists() for m in models):
                continue
            slot = slots.get(p.slot) or {}
            h = 2.0 * float(slot.get("receptacle_center_above_board_top") or 0.0) or float(
                (slot.get("height_envelope_mm") or {}).get("above_board_top") or 0.0)
            if h <= 0:
                continue
            lib, name = p.footprint.split(":", 1)
            ref_fp = load_footprint(p.footprint, self.path.parent / f"{LIB}.pretty")
            xs, ys = [], []
            for g in ref_fp.GraphicalItems():
                if g.GetLayer() == pcbnew.F_Fab:
                    bb = g.GetBoundingBox()
                    xs += [pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetRight())]
                    ys += [pcbnew.ToMM(bb.GetTop()), pcbnew.ToMM(bb.GetBottom())]
            if not xs:
                continue
            w, dpt = max(xs) - min(xs), max(ys) - min(ys)
            cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
            out = pcb_dir / "3d" / f"{name}_envelope.step"
            if not out.exists():
                try:
                    import cadquery as cq
                except ImportError:  # pragma: no cover - builder without cadquery: keep pads-only look
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                body = cq.Workplane("XZ").slot2D(w, h).extrude(dpt / 2, both=True).translate((0, 0, h / 2))
                cq.exporters.export(body, str(out))
            m = pcbnew.FP_3DMODEL()
            m.m_Filename = "${KIPRJMOD}/3d/" + out.name
            m.m_Offset = pcbnew.VECTOR3D(cx, -cy, 0.0)
            m.m_Scale = pcbnew.VECTOR3D(1.0, 1.0, 1.0)
            m.m_Rotation = pcbnew.VECTOR3D(0.0, 0.0, 0.0)
            fp.Models().clear()
            fp.Models().push_back(m)
            made.append(f"{p.ref}: envelope 3D model {w:.2f} x {dpt:.2f} x {h:.2f} mm (F.Fab outline x backplane-v1 "
                        "height; not the vendor model)")
        return made

    def courtyard_overlaps(self) -> list[list[str]]:
        refs = sorted(self.boxes)
        side = {r: ("bottom" if self.fps[r].GetLayer() == pcbnew.B_Cu else "top") for r in refs}
        tht = {r for r in refs if any(pd.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH)
                                      for pd in self.fps[r].Pads())}
        out = []
        for i, a in enumerate(refs):
            for b_ in refs[i + 1:]:
                if side[a] != side[b_] and not (a in tht and b_ in tht):
                    continue
                if _overlap(self.boxes[a], self.boxes[b_], -0.001):
                    out.append([a, b_])
        return out

    def pad_boxes(self) -> dict[str, list[tuple]]:
        """Mask-opening boxes (board coords) per side: THT/NPTH pads count on both sides."""
        out = {"top": [], "bottom": []}
        f = self.frame
        for fp in self.board.GetFootprints():
            for pad in fp.Pads():
                bb = pad.GetBoundingBox()
                (xa, ya), (xb, yb) = f.to_board(bb.GetOrigin()), f.to_board(bb.GetEnd())
                box = (min(xa, xb) - 0.1, min(ya, yb) - 0.1, max(xa, xb) + 0.1, max(ya, yb) + 0.1)
                if pad.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
                    out["top"].append(box)
                    out["bottom"].append(box)
                elif pad.IsOnLayer(pcbnew.F_Cu):
                    out["top"].append(box)
                else:
                    out["bottom"].append(box)
        return out

    def assign_unconnected(self, sch_nets: dict):
        """Give no-connect pads the 'unconnected-(...)' nets the schematic netlist defines (KiCad's own convention)."""
        for name, nodes in sch_nets.items():
            if not name.startswith("unconnected-"):
                continue
            ni = pcbnew.NETINFO_ITEM(self.board, name)
            self.board.Add(ni)
            for ref, pad in nodes:
                fp = self.fps.get(ref)
                if fp is None:
                    continue
                for pd in fp.Pads():
                    if pd.GetNumber() == pad and not pd.GetNetname():
                        pd.SetNet(ni)

    # ---------------------------------------------------------------- keepouts, zones
    def keepouts(self):
        for p in self.d.parts:
            if p.ref.startswith("FID") and p.ref in self.fps:
                x, y = self.frame.to_board(self.fps[p.ref].GetPosition())
                self.d.keepouts.append({"name": f"{p.ref} clear", "rect": [x - 1.6, y - 1.6, x + 1.6, y + 1.6],
                                        "layers": ["F.Cu"]})
        for k in self.d.keepouts:
            z = pcbnew.ZONE(self.board)
            z.SetIsRuleArea(True)
            z.SetDoNotAllowCopperPour(True)
            z.SetDoNotAllowTracks(True)
            z.SetDoNotAllowVias(True)
            z.SetDoNotAllowPads(False)
            z.SetDoNotAllowFootprints(False)
            ls = pcbnew.LSET()
            for lname in k.get("layers", ["F.Cu", "B.Cu"]):
                ls.AddLayer(pcbnew.F_Cu if lname == "F.Cu" else pcbnew.B_Cu)
            z.SetLayerSet(ls)
            z.SetZoneName(k["name"])
            x0, y0, x1, y1 = k["rect"]
            x0, y0, x1, y1 = max(x0, 0.0), max(y0, 0.0), min(x1, self.d.width), min(y1, self.d.height)
            for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
                z.AppendCorner(self.frame.pt(x, y), -1)
            self.board.Add(z)

    def gnd_zones(self):
        W, H = self.d.width, self.d.height
        gnd = self.nets.get("GND")
        if gnd is None:
            return
        for i, layer in enumerate((pcbnew.F_Cu, pcbnew.B_Cu)):
            z = pcbnew.ZONE(self.board)
            z.SetLayer(layer)
            z.SetNet(gnd)
            z.SetZoneName(f"GND {'top' if layer == pcbnew.F_Cu else 'bottom'}")
            z.SetLocalClearance(mm(0.25))
            z.SetMinThickness(mm(0.2))
            z.SetPadConnection(pcbnew.ZONE_CONNECTION_THT_THERMAL)
            z.SetThermalReliefGap(mm(0.3))
            z.SetThermalReliefSpokeWidth(mm(0.4))
            z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
            z.SetAssignedPriority(0)
            for x, y in ((0, 0), (W, 0), (W, H), (0, H)):
                z.AppendCorner(self.frame.pt(x, y), -1)
            self.board.Add(z)

    def usb_fanout(self, ref: str = "J_PWR", nets=("VBUS", "GND"), width: float = 0.6, via_d: float = 0.6,
                   drill: float = 0.3) -> int:
        """Locked fan-out for the fine-pitch USB-C power pins: a short stub straight back from the mouth to a via
        (GND vias staggered further back). Freerouting then only joins vias; without this it sometimes cannot
        escape a VBUS pad wedged 0.2 mm from a GND pad, depending on routing order."""
        fp = self.fps.get(ref)
        if fp is None:
            return 0
        f = self.frame
        pads = [p for p in fp.Pads() if p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD and p.GetNetname() in nets]
        if not pads:
            return 0
        fx, fy = f.to_board(fp.GetPosition())
        px = sum(f.to_board(p.GetPosition())[0] for p in pads) / len(pads)
        py = sum(f.to_board(p.GetPosition())[1] for p in pads) / len(pads)
        vx, vy = px - fx, py - fy
        n = math.hypot(vx, vy) or 1.0
        ix, iy = round(vx / n), round(vy / n)          # inward: from the mouth through the pad row
        seen, made = set(), 0
        for p in sorted(pads, key=lambda q: (q.GetNetname(), q.GetNumber())):
            x, y = f.to_board(p.GetPosition())
            key = (round(x, 3), round(y, 3))
            if key in seen:
                continue  # stacked pad (A9/B4 share one place)
            seen.add(key)
            half = pcbnew.ToMM(max(p.GetSize(pcbnew.F_Cu).x, p.GetSize(pcbnew.F_Cu).y)) / 2
            L = half + (0.9 if p.GetNetname() == "VBUS" else 1.8)
            ex, ey = x + ix * L, y + iy * L
            t = pcbnew.PCB_TRACK(self.board)
            t.SetStart(f.pt(x, y))
            t.SetEnd(f.pt(ex, ey))
            t.SetWidth(mm(width))
            t.SetLayer(pcbnew.F_Cu)
            t.SetNet(p.GetNet())
            t.SetLocked(True)
            self.board.Add(t)
            v = pcbnew.PCB_VIA(self.board)
            v.SetPosition(f.pt(ex, ey))
            v.SetWidth(mm(via_d))
            v.SetDrill(mm(drill))
            v.SetNet(p.GetNet())
            v.SetViaType(pcbnew.VIATYPE_THROUGH)
            v.SetLocked(True)
            self.board.Add(v)
            made += 1
        return made

    def remove_gnd_zones(self) -> int:
        """Freerouting treats copper zones as planes and skips routing their net; GND is routed as tracks instead
        (connectivity by construction), and the pours are added back after the SES import."""
        zs = [z for z in self.board.Zones() if not z.GetIsRuleArea() and z.GetNetname() == "GND"]
        for z in zs:
            self.board.Remove(z)
        return len(zs)

    def fill(self):
        filler = pcbnew.ZONE_FILLER(self.board)
        filler.Fill(self.board.Zones())

    # ---------------------------------------------------------------- silkscreen
    def text(self, s: str, x: float, y: float, size=1.0, thick=0.15, rot=0.0, layer=None, just="center",
             mirror=False):
        t = pcbnew.PCB_TEXT(self.board)
        t.SetText(s)
        t.SetPosition(self.frame.pt(x, y))
        t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size)))
        t.SetTextThickness(mm(thick))
        t.SetLayer(pcbnew.F_SilkS if layer is None else layer)
        t.SetTextAngleDegrees(rot)
        if just == "left":
            t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_LEFT)
        elif just == "right":
            t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_RIGHT)
        if mirror:
            t.SetMirrored(True)
        self.board.Add(t)
        return t

    def silk_shape(self, kind, pts, width=0.15, filled=False, layer=None):
        s = pcbnew.PCB_SHAPE(self.board)
        s.SetLayer(pcbnew.F_SilkS if layer is None else layer)
        s.SetWidth(mm(width))
        if kind == "circle":
            (cx, cy), r = pts
            s.SetShape(pcbnew.SHAPE_T_CIRCLE)
            s.SetCenter(self.frame.pt(cx, cy))
            s.SetEnd(self.frame.pt(cx + r, cy))
        elif kind == "rect":
            (x0, y0), (x1, y1) = pts
            s.SetShape(pcbnew.SHAPE_T_RECT)
            s.SetStart(self.frame.pt(x0, y1))
            s.SetEnd(self.frame.pt(x1, y0))
        else:
            (x0, y0), (x1, y1) = pts
            s.SetShape(pcbnew.SHAPE_T_SEGMENT)
            s.SetStart(self.frame.pt(x0, y0))
            s.SetEnd(self.frame.pt(x1, y1))
        s.SetFilled(filled)
        self.board.Add(s)
        return s

    def hide_fab_refs(self):
        """Footprint reference text stays on silk only for parts without a port label; values on Fab."""
        for ref, fp in self.fps.items():
            fp.Value().SetLayer(pcbnew.F_Fab if fp.GetLayer() == pcbnew.F_Cu else pcbnew.B_Fab)
            fp.Value().SetVisible(True)

    def bbox(self, ref):
        return self.boxes[ref]

    # ---------------------------------------------------------------- devkit outline (fab + silk corners)
    def devkit_outline(self):
        dk = self.d.devkit or {}
        plan = dk.get("pcb_plan")
        if not plan:
            return
        x0, x1, y0, y1 = (float(v) for v in plan)
        W = self.d.width
        xc0, xc1 = max(x0, 0.0), min(x1, W)
        for a, b_ in (((xc0, y0), (xc1, y0)), ((xc1, y0), (xc1, y1)), ((xc1, y1), (xc0, y1)), ((xc0, y1), (xc0, y0))):
            self.silk_shape("line", (a, b_), 0.1, False, pcbnew.F_Fab)
        self.text("ESP32-S3-DevKitC-1 outline (plugs in on 8.5 mm sockets)", (xc0 + xc1) / 2, y1 - 1.2, 0.8, 0.1,
                  0.0, pcbnew.F_Fab)

    # ---------------------------------------------------------------- GND stitching vias
    def stitch(self, pitch: float = 5.08, via_d: float = 0.6, drill: float = 0.3, margin: float = 0.25) -> int:
        free = self._via_checker(via_d, margin, avoid_courtyards=True)
        if free is None:
            return 0
        W, H = self.d.width, self.d.height
        n = 0
        y = pitch / 2
        while y < H:
            x = pitch / 2
            while x < W:
                if free(x, y):
                    self._add_gnd_via(x, y, via_d, drill)
                    n += 1
                x += pitch
            y += pitch
        return n

    def _add_gnd_via(self, x, y, via_d, drill):
        v = pcbnew.PCB_VIA(self.board)
        v.SetPosition(self.frame.pt(x, y))
        v.SetWidth(mm(via_d))
        v.SetDrill(mm(drill))
        v.SetNet(self.nets["GND"])
        v.SetViaType(pcbnew.VIATYPE_THROUGH)
        self.board.Add(v)

    def stitch_islands(self, via_d: float = 0.6, drill: float = 0.3, step: float = 0.3175) -> int:
        """Every GND pour fragment other than the main one on its layer gets a via into the other layer's pour."""
        free = self._via_checker(via_d, 0.1, avoid_courtyards=False)
        if free is None:
            return 0
        f = self.frame
        zones = {z.GetLayer(): z for z in self.board.Zones() if not z.GetIsRuleArea() and z.GetNetname() == "GND"}
        gnd_vias = [t.GetPosition() for t in self.board.GetTracks() if t.GetClass() == "PCB_VIA" and t.GetNetname() == "GND"]
        n = 0
        for lay, z in zones.items():
            polys = z.GetFilledPolysList(lay)
            cnt = polys.OutlineCount()
            if cnt < 2:
                continue
            areas = [abs(polys.Outline(i).Area()) for i in range(cnt)]
            main = max(range(cnt), key=lambda i: areas[i])
            for i in range(cnt):
                if i == main:
                    continue
                ol = polys.Outline(i)
                if any(ol.PointInside(v) for v in gnd_vias):
                    continue
                bb = ol.BBox()
                (xa, ya), (xb, yb) = f.to_board(bb.GetOrigin()), f.to_board(bb.GetEnd())
                x0, x1, y0, y1 = min(xa, xb), max(xa, xb), min(ya, yb), max(ya, yb)
                done = False
                y = y0
                while y <= y1 and not done:
                    x = x0
                    while x <= x1:
                        if ol.PointInside(f.pt(x, y)) and free(x, y):
                            self._add_gnd_via(x, y, via_d, drill)
                            gnd_vias.append(f.pt(x, y))
                            n += 1
                            done = True
                            break
                        x += step
                    y += step
        return n

    def _via_checker(self, via_d: float, margin: float, avoid_courtyards: bool):
        gnd = self.nets.get("GND")
        if gnd is None:
            return None
        W, H = self.d.width, self.d.height
        f = self.frame
        zones = {z.GetLayer(): z for z in self.board.Zones() if not z.GetIsRuleArea() and z.GetNetname() == "GND"}
        if len(zones) < 2:
            return None
        obstacles = []  # (kind, data, clearance) in board coords
        for t in self.board.GetTracks():
            if t.GetNetname() == "GND":
                continue
            if t.GetClass() == "PCB_VIA":
                x, y = f.to_board(t.GetPosition())
                obstacles.append(("c", (x, y, pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu)) / 2)))
            else:
                a, b_ = f.to_board(t.GetStart()), f.to_board(t.GetEnd())
                obstacles.append(("s", (a, b_, pcbnew.ToMM(t.GetWidth()) / 2)))
        for fp in self.board.GetFootprints():
            for pad in fp.Pads():
                bb = pad.GetBoundingBox()
                (xa, ya), (xb, yb) = f.to_board(bb.GetOrigin()), f.to_board(bb.GetEnd())
                obstacles.append(("r", (min(xa, xb), min(ya, yb), max(xa, xb), max(ya, yb), pad.GetNetname() == "GND"
                                        and pad.GetAttribute() == pcbnew.PAD_ATTRIB_SMD)))
        keep = [k["rect"] for k in self.d.keepouts] + [
            [b[0] - 0.4, b[1] - 0.4, b[2] + 0.4, b[3] + 0.4] for b in getattr(self, "silk_boxes", [])]
        cyd = [b for r, b in self.boxes.items() if not r.startswith("TP")]
        need = via_d / 2 + 0.2 + margin
        for t in self.board.GetTracks():
            if t.GetClass() == "PCB_VIA" and t.GetNetname() == "GND":
                x, y = f.to_board(t.GetPosition())
                obstacles.append(("c", (x, y, pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu)) / 2 + 0.2)))

        def free(x, y) -> bool:
            if x < 1.2 or y < 1.2 or x > W - 1.2 or y > H - 1.2:
                return False
            for k in keep:
                if k[0] - 0.5 <= x <= k[2] + 0.5 and k[1] - 0.5 <= y <= k[3] + 0.5:
                    return False
            if avoid_courtyards:
                for b in cyd:
                    if b[0] <= x <= b[2] and b[1] <= y <= b[3]:
                        return False
            for kind, dta in obstacles:
                if kind == "c":
                    if math.hypot(x - dta[0], y - dta[1]) < dta[2] + need:
                        return False
                elif kind == "s":
                    if _seg_dist((x, y), dta[0], dta[1]) < dta[2] + need:
                        return False
                else:
                    x0, y0, x1, y1, _g = dta
                    dx = max(x0 - x, 0.0, x - x1)
                    dy = max(y0 - y, 0.0, y - y1)
                    if math.hypot(dx, dy) < need + 0.1:
                        return False
            pt = f.pt(x, y)
            for lay, z in zones.items():
                polys = z.GetFilledPolysList(lay)
                for ang in range(0, 360, 45):
                    r = via_d / 2 + 0.05
                    q = f.pt(x + r * math.cos(math.radians(ang)), y + r * math.sin(math.radians(ang)))
                    if not polys.Contains(q):
                        return False
                if not polys.Contains(pt):
                    return False
            return True

        return free

    def stats(self) -> dict:
        length = 0.0
        vias = 0
        by_layer = {"F.Cu": 0.0, "B.Cu": 0.0}
        for t in self.board.GetTracks():
            if t.GetClass() == "PCB_VIA":
                vias += 1
            else:
                L = pcbnew.ToMM(t.GetLength())
                length += L
                by_layer[t.GetLayerName()] = by_layer.get(t.GetLayerName(), 0.0) + L
        self.board.BuildConnectivity()
        conn = self.board.GetConnectivity()
        try:
            conn.RecalculateRatsnest()
        except Exception:  # pragma: no cover
            pass
        unrouted = conn.GetUnconnectedCount(False)
        return {"track_length_mm": round(length, 1), "track_length_by_layer_mm": {k: round(v, 1) for k, v in by_layer.items()},
                "vias": vias, "unrouted": int(unrouted)}

    def save(self):
        if not pcbnew.SaveBoard(str(self.path), self.board):
            raise StageError("pcbnew could not save the board", {"path": str(self.path)})

    def reload(self):
        """Save and load the board from disk. A board loaded from a file gets the DRC engine that the zone filler
        needs for hole and netclass clearances (an in-memory NewBoard() fills over NPTH holes)."""
        self.save()
        self.load_from_disk()

    def load_from_disk(self):
        b = pcbnew.LoadBoard(str(self.path))
        if b is None:
            raise StageError("pcbnew could not reload the board", {"path": str(self.path)})
        self.board = b
        self.fps = {fp.GetReference(): fp for fp in b.GetFootprints()}
        self.nets = {}
        for net in self.d.nets():
            ni = b.FindNet(board_net_name(net))
            if ni is not None:
                self.nets[net] = ni
        pcbnew.KIID.SeedGenerator(int(self.d.design_id[8:12], 16) + 7)


STACKUP = """(stackup
\t\t\t(layer "F.SilkS" (type "Top Silk Screen") (color "White"))
\t\t\t(layer "F.Paste" (type "Top Solder Paste"))
\t\t\t(layer "F.Mask" (type "Top Solder Mask") (color "Blue") (thickness 0.01))
\t\t\t(layer "F.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "dielectric 1" (type "core") (color "FR4 natural") (thickness 1.51) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
\t\t\t(layer "B.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "B.Mask" (type "Bottom Solder Mask") (color "Blue") (thickness 0.01))
\t\t\t(layer "B.Paste" (type "Bottom Solder Paste"))
\t\t\t(layer "B.SilkS" (type "Bottom Silk Screen") (color "White"))
\t\t\t(copper_finish "HAL lead-free")
\t\t\t(dielectric_constraints no)
\t\t)"""


def add_stackup(pcb_path: Path) -> None:
    """Write a physical 2-layer stackup (1.6 mm FR-4, 1 oz, blue mask, white silk, lead-free HASL) into the board.
    The pcbnew Python API does not wrap BOARD_STACKUP, so the saved S-expression is edited once."""
    txt = pcb_path.read_text()
    if "(stackup" in txt:
        return
    txt = txt.replace("\t(setup\n", "\t(setup\n\t\t" + STACKUP + "\n", 1)
    pcb_path.write_text(txt)


def _seg_dist(p, a, b) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _spiral(step: float, rmax: float):
    """Deterministic search offsets ordered by distance, then angle."""
    pts = [(0.0, 0.0)]
    n = int(rmax / step)
    cand = []
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            if i == 0 and j == 0:
                continue
            dx, dy = i * step, j * step
            r = math.hypot(dx, dy)
            if r <= rmax:
                cand.append((round(r, 4), round(math.atan2(dy, dx), 4), dx, dy))
    cand.sort()
    pts += [(c[2], c[3]) for c in cand]
    return pts


def sym_uuid_fn(design_id: str):
    ns = uuid.uuid5(uuid.NAMESPACE_URL, "gadget-studio/" + design_id)
    return lambda ref: str(uuid.uuid5(ns, f"sym/{ref}"))


__all__ = ["BoardBuilder", "board_net_name", "sym_uuid_fn", "RULES", "CLASSES", "Frame", "X0", "Y0", "mm"]
