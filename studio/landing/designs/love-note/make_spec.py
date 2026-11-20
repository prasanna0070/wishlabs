#!/usr/bin/env python3
"""Writes spec.json for the Love Note heart: a puffy heart standing on a soft plinth.
Pure python (no deps). Heart = two lobe circles + tangent flanks; plinth = tapered rounded block.
Usage: python3 make_spec.py [key=value ...]   (overrides the P dict below)
"""
import json, math, sys
from pathlib import Path

P = dict(R=46.0, c=37.0, drop=92.0, plinth_h=46.0, plinth_wb=62.0, plinth_wt=56.0, neck=24.0,
         depth=104.0, edge=18.0, lobe_step=14.0)
for a in sys.argv[1:]:
    k, v = a.split("=")
    P[k] = float(v)

R, c, drop = P["R"], P["c"], P["drop"]
ph, wb, wt, J = P["plinth_h"], P["plinth_wb"], P["plinth_wt"], P["neck"]

# flank angle from vertical
D = math.hypot(c, drop)
ang = math.atan2(c, drop) + math.asin(R / D)
tanA = math.tan(ang)
zj = ph + 2.0                      # junction height on the heart flank
zt = zj - J / tanA                 # virtual heart tip
zc = zt + drop                     # lobe circle centre height
# tangent point on the right circle: direction from T along the flank
dx, dz = math.sin(ang), math.cos(ang)
# point on flank closest to C is the tangent point
t = (c - 0) * dx + (zc - zt) * dz
Px, Pz = dx * t, zt + dz * t
phiP = math.atan2(Pz - zc, Px - c)           # angle of tangent point on circle (rad)
Qz = zc + math.sqrt(R * R - c * c)            # cleft (circles meet at x=0)
phiQ = math.atan2(Qz - zc, 0 - c)

right = []
# plinth right side, bottom to top
def arc(cx, cz, r, a0, a1, n):
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * k / n)), cz + r * math.sin(math.radians(a0 + (a1 - a0) * k / n)))
            for k in range(n + 1)]
rr = ph / 2.0                                        # stadium base: semicircular ends
right += arc(wb - rr, rr, rr, -90, 90, 8)            # first point (wb-rr, 0) is on the table
if wb - rr > J + 12:
    right += [((wb - rr + J + 5) / 2, ph)]
right += [(J + 5.0, ph + 0.4)]
# flank from junction to tangent point
nf = 4
for i in range(nf + 1):
    z = zj + (Pz - zj) * i / nf
    right.append((J + (z - zj) * tanA if i else J, z))
# arc over the lobe from P (phiP) counter-clockwise to near the cleft
step = math.radians(P["lobe_step"])
a = phiP + step
while True:
    x = c + R * math.cos(a)
    if x < P.get("cleft_x", 9.0):
        break
    right.append((x, zc + R * math.sin(a)))
    a += step
# soft cleft: the spline rounds the V through this point
cleft = (0.0, Qz + P.get("cleft_lift", 1.0))

# full outline CCW: bottom-left -> bottom-right -> up right side -> cleft -> down left side
pts = [(-right[0][0], 0.0)] + right + [cleft] + [(-x, z) for (x, z) in reversed(right[1:])]
pts = [(round(x, 1), round(z, 1)) for x, z in pts]
# drop accidental near-duplicates
clean = []
for p in pts:
    if clean and math.dist(p, clean[-1]) < 1.5:
        continue
    clean.append(p)
pts = clean

info = dict(zt=round(zt, 1), zc=round(zc, 1), top=round(zc + R, 1), cleft=round(Qz, 1), widest=round(c + R, 1),
            flank_deg=round(math.degrees(ang), 1), tangent=(round(Px, 1), round(Pz, 1)), n=len(pts))
print(json.dumps(info))

spec = json.loads(Path(__file__).with_name("spec.template.json").read_text())
spec["body"]["outline"] = [list(p) for p in pts]
spec["body"]["depth"] = P["depth"]
spec["body"]["edge_round"] = P["edge"]
Path(__file__).with_name("spec.json").write_text(json.dumps(spec, indent=1) + "\n")
