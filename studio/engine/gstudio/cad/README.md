# gstudio.cad

Parametric CAD stage: spec → buildable, printable enclosure with named, coloured components, exports, checks and
renders. CadQuery 2.8 / OCP 7.9. It runs on the VM only (`tools/vm.sh`).

## API

```python
from gstudio.cad import run, preview
summary = run(ctx)                    # ARCHITECTURE §2 stage entry; writes ctx.out/cad and ctx.out/print
res = preview(spec, lib, out_dir)     # {"png", "glb", "warnings", "seconds"}; shell + apertures + visible parts
                                      # (1024 px hero, about 13-24 s on 8 vCPU)
```

`run(ctx)` writes:
- **Early:** `cad/draft.glb` (the shell with decor, written atomically as soon as it exists).
- **CAD outputs:** `cad/params.json`, `cad/assembly.step`, `cad/assembly.glb`, `cad/components.json`, `cad/anchors.json`,
  `cad/cable-routes.json` and `cad/components/<NAME>.brep`.
- **Editable source:** `cad/gadget_cad.py` with its vendored package `cad/gadget_cad/`.
- **Checks and views:** `cad/digital-checks.json`, `cad/renders/{hero,rear_open,xray}.png` and `cad/cad-summary.json`.
- **Prints:** `print/PRINT_*.stl`, plus `print/TEST_FitCoupon.stl`, `TEST_SeamShell.stl` and `TEST_SeamMate.stl`.

The summary returns:
- `checks[]` as `{id, title, status, detail, qb}`;
- `print{}` (orientation notes);
- `rebuild` (`python cad/gadget_cad.py`);
- `say`, `timings` and `warnings`.

`ctx.options`:
- `fast` skips the STEP re-import check;
- `cad_renders: False` skips the hero PNG;
- `cad_views: "all"` also renders `rear_open` and `xray`;
- `render_timeout` (default 150 s).

Renders never run inside the CAD process. `gstudio.cad.render_cli` renders from the GLB in a fresh interpreter with a
hard timeout (VTK EGL offscreen), so a stuck GL context cannot hang the stage. To render views by hand:
`PYTHONPATH=engine python -m gstudio.cad.render_cli --glb cad/assembly.glb --out cad/renders --views hero,rear_open,xray`.

Standalone rebuild, with no engine needed: `python cad/gadget_cad.py [--out DIR] [--no-checks] [--no-cables]`. It reads
`cad/params.json`, which holds every resolved dimension.

## Pipeline (`build.py`)

The stages run in this order:
1. body;
2. ears;
3. decor relief;
4. draft;
5. feature kits;
6. cartridge placement;
7. split;
8. feet, tie anchors and the USB-C tunnel;
9. shell booleans;
10. components;
11. cable routes;
12. fit coupons;
13. lift onto the feet, so that z = 0 is the table.

## Body families (`body.py`)

| family | construction | `edge_round` |
|---|---|---|
| `silhouette` | Spline outline with a straight bottom, extruded along Y. The inner cavity is an offset outline rebuilt as a clean B-spline. | Front perimeter round, reduced on fillet failure; the rear round is `min(edge_round, 6)`. The actual radii are in `info.body`. |
| `revolve` | Spline half profile revolved. `squash_y` is built as a smooth loft of elliptical sections, about 100× faster in booleans than a non-uniform transform. | Bottom edge fillet. |
| `blob` | Rounded boxes, spheres, ellipsoids (exact spheroids when two axes match), cylinders and capsules. Seams are blended with fillets on the fuse section edges, then the shape is cut flat at z=0. | Bottom edge fillet. |

## Spec fields used

- `colors.*`
- `body.*`: `family`, `outline`, `depth`, `profile`, `squash_y`, `primitives` (with `axis`), `wall`, `edge_round`, `split`
  and `feet`
- `decor[]`:
  - `face_plate`: circle, ellipse or rounded_rect, engrave or emboss
  - `eye`: 6 styles
  - `mouth`: 4 styles
  - `blush`: `side: both`
  - `ear`: cat, bear, bunny, owl_tuft or antenna, with `side`, `thickness` and `tilt`
  - `text`: engraved or embossed, DejaVu Sans Bold
  - `color` token. Defaults: eyes, mouth and text use `detail`; blush is pink.
- `features[]`: `module`, `face` (front, rear, top, left = robot's left = +X, or right), `at`, `rotate` and `grille`
- `electronics.core`, `extra_modules`
- `derived` (from normalize): `instances` (refs and slots), `harness` (cables and lengths), `cartridge` (hint) and
  `hardware`
- `library/modules.json` and `library/backplane-v1.json`: every dimension. Values that the library keeps only in notes
  are transcribed in `modules.LIB_DETAIL` with their source. Anything still missing comes from `refs_provisional.py` and
  is listed in `params.provenance.provisional_modules`.

## Decor (`decor.py`)

- Face plates are applied first; relief inside a plate is measured from the plate floor.
- Other items are grouped by (relief, depth, plate depth, colour), so each group costs one layer computation and one
  boolean.
- Flat patches use straight prisms; curved surfaces use the layer technique.
- Engraved decor carries its colour token as a separate GLB primitive (`detail`, or pink blush). This is a paint-fill
  or multi-material suggestion; the STEP shell is single-colour.

## Features (`features.py`)

Each kit is built in a local frame on the outer surface, with +z along the smoothed local normal. On curved fronts a flat
local pad is carved into the cavity so modules always seat on a flat face. Posts use M2.5 heat-set inserts (hole 4.0,
boss 7.2, from the library), and printed clamps are separate `PRINT_` parts.

| kind | aperture | mount kit |
|---|---|---|
| display | Window at the viewing area, 0.6 mm bezel recess, flat spot face on curves | Glass pocket that leaves the glass unloaded, PCB pocket, strap clamp on 2 insert posts |
| speaker | Grille clipped to the baffle opening (`hex_holes`, `slots`, `concentric`, `dots_ellipse`) | Gasket seat, foam gasket, cup wall, ring clamp with a presser tube on 3 insert posts |
| snap button | Panel hole, spot face, counterbore to a 3.0 mm panel (library range 2 to 3.9) | Snap-in latches |
| tactile | Hole and printed plunger cap with flange | Standoffs and strap clamp |
| led / led_ring | Window with a printed diffuser plug that follows the surface, plus flange | Light tube that sets the LED-to-diffuser gap; strap clamp |
| mic | 1.5 mm port, gasket seat, foam gasket ring | Standoffs and strap clamp |
| sensor_window / touch | Small window, or a pocket thinned to 1.5 mm skin with an engraved touch ring | Clamp, or bonded board |

## Enclosure (`enclosure.py`)

**Rear cover.** The seam sits where the rear round starts, so the cover reads as a cap. The cover has a 1.6 mm locating
lip with a 0.25 mm gap, relieved at the floor and the bosses. Bosses with webs to the wall carry inserts, screws sit in
counterbores, and there are vents. The cartridge is moved forward so its rear bosses stay in front of the seam.

**Base plate.** The opening is the bottom footprint offset by the wall. The plate carries the cartridge, feet seats and
screw counterbores; screws go up into insert bosses webbed to the shell wall.

The cartridge follows `backplane-v1.json`: board, sockets, ports, devkit, amp, microSD, support posts and 4 floor bosses.
Every design also gets a 13 × 7 USB-C overmold tunnel to the receptacle face, feet seats (library bumpers) and tie
anchors with a 3.6 × 1.8 slot.

## Checks (`checks.py`)

| check | gate |
|---|---|
| OCC validity; one solid per print part | QB-07 |
| Role prefixes and purchase links | QB-06 |
| Watertight STLs with one component; BREP vs STL volume within 1 % | QB-07, QB-16 |
| Bed fit in the stated orientation | QB-10 |
| zmin = 0 | QB-05 |
| STEP re-import | QB-04 |
| Full pair matrix: AABB prefilter, mesh boolean, then exact OCC on hits, with an allow-list | QB-08 |
| Clearance per purchased/printed pair | QB-09 |
| Cartridge and feature-module clearance | |
| Wall thickness sampling | QB-11 |
| Screw engagement and bottoming | QB-14 |
| Driver access | |
| Cable length vs harness and wall clearance | QB-15 |
| Fit coupon coverage | QB-13 |
| Static tip-over angle from the CAD centre of gravity | §9.2 item 7 |

## Known limits

- Purchased parts are simplified envelopes. Pins, clips, the arcade button's latches and cable bends are not modelled.
- Cables are display-only, routed through a voxel free-space map and swept.
- Feature module kits do not yet support servos or motors; they fall back to a generic clamp with a warning.
- Decor is front-projected only (the spec defines it in front-view coordinates).
- Rear-cover designs place the cartridge with its rear bosses in front of the seam. That can differ from
  `derived.cartridge`; see NOTES-for-orchestrator.md. `anchors.json` and `components.json` hold the real positions.
- The display renders draw an illustrative idle face that exists only in the PNG. The GLB and STEP show the glass.
