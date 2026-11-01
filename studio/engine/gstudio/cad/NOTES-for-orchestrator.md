# CAD stage: notes and requests for the orchestrator (2026-11-01)

## What other stages can rely on

- **Live draft.** `cad/draft.glb` is written atomically (tmp file + `os.replace`) as soon as the shell and its decor
  exist, and `ctx.artifacts["cad.draft"]` is set. It is not a deliverable.
- **GLB convention.** Follows REQUESTS.md: metres, +Y up, (x, y, z) mm → (x/1000, z/1000, −y/1000). There is one node
  per component and node name == component name. Each coloured sub-part is a primitive with a PBR material.
  `anchors.json`, `cable-routes.json` and `components.json` bboxes are in gadget mm, Z up.
- **`components.json` fields.** Each entry has `kind`, `label`, `ref`, `module_id`, `feature_id`, `spec_path` (`body`,
  `features[i]`, `electronics.core`, or null for implied parts), `color`, `color_hex`, `bbox`, `volume_mm3`, `valid`,
  `solids`, `subparts`, `mass_g` and `mass_source`. PRINT parts add `print` {file, orientation_note, material}.
  Fastener groups and feet add `qty`, `size` and `items[{size, qty}]`.
- **Decor regions.** Decor and ears are part of `PRINT_Shell`, so its entry also carries
  `decor: [{spec_paths: ["decor[3]"], type, label, bbox}]`. For "make the ears rounder", the UI maps the clicked point to
  a region bbox and edits that `spec_path`. Grouped relief, such as two eyes and a mouth cut together, lists every index
  it covers.
- **Fastener counts.** CAD is the source of truth. Module ids:
  - `PURCHASE_HeatSetInserts` → `INSERT_HEATSET_M2_5X4_STD`
  - `PURCHASE_*Screws` → `SCREW_SET_M2_M2_5_SHCS_A2`
  - `PURCHASE_Feet` → `FEET_BUMPON_SJ5302_CLEAR`
  - `PURCHASE_SpeakerGasket` and `PURCHASE_MicGasket` → `SPK_GASKET_FOAM_25X3_ADH` (rings cut from the strip)
- **Fit coupons.** `TEST_*` coupons are listed in `summary["print"]` and in the internal `cad/cad-summary.json`. Each has
  `tests: [...]`, the interfaces it checks.
- **Anchor names.** These are stable:
  - `BP1.<slot>` (connector top) and `BP1.J_PWR` (receptacle face centre);
  - `BP1.H1..H4` (cartridge screws);
  - `U1.USB`;
  - `<REF>.CONN` (module connector, back side);
  - `<REF>.screw1/2` (clamp screws);
  - `AMP1.SOCKET` and `SD1.SOCKET`.
- **Run summary.** `run()` returns:
  - `checks[]` as `{id, title, status, detail, qb?, artifact}`;
  - `print{name: {file, orientation_note, material}}`;
  - `rebuild: "python cad/gadget_cad.py"`;
  - `say` and `timings`.

  The full check data is in `cad/digital-checks.json`.
- **Renders.** `run()` renders only `cad/renders/hero.png`, in a subprocess with a 150 s timeout
  (`gstudio.cad.render_cli`), so a hung GL context can never block the stage.
  - `options.cad_renders = False` skips it.
  - `options.cad_views = "all"` also writes `rear_open.png` and `xray.png`.
  - `preview()` renders the same way, with a 60 s timeout.

## Requests to foundation (normalize / backplane)

1. **Rear-cover cartridge placement.** `normalize` places the board so the USB-C face sits 0.2 mm inside the inner rear
   wall. With a removable rear cover that puts the rear cartridge bosses (board y 75.1, OD 7.2) across the cover seam.
   CAD therefore moves the board forward until `y_off + 75.1 + 3.6 + 0.8 <= y_seam`.
   - For silhouettes, `y_seam = depth/2 - min(edge_round, 6) - 1.5`.
   - For other families, it is the largest y where the cavity section contains the cartridge's front envelope.

   The USB-C face then sits a few mm behind the rear surface. CAD cuts the 13 × 7 overmold tunnel from backplane-v1 all
   the way to the receptacle face, so the plug's overmold enters it and the plug seats. Please apply the same rule in
   `_place_cartridge` for `split == "rear_cover"` so that `derived.cartridge.board_origin` matches the CAD. Until then,
   `anchors.json` and `components.json` hold the real positions.
2. **Base-plate floor.** The CAD base plate is 3.5 mm thick, so the inner floor (plate top) is z = 3.5. Normalize uses
   `floor_z = wall` (2.4) for every split. Please use 3.5 for `base_plate`. If `derived.cartridge.floor_z` is set, CAD
   still uses its own plate thickness and reports `info.cartridge.floor_z`.
3. **M2.5 screw lengths.** The library stocks M2.5 in 5/8/10/12 mm only, not 6. CAD picks the shortest stocked screw that
   engages at least 3 mm and deepens the insert hole to tip + 0.5 mm:
   - cartridge: M2.5 × 5;
   - module clamps and the speaker ring: M2.5 × 8, insert holes 6.1 mm deep;
   - cover and plate: per geometry.

   `backplane-v1.json` says M2.5 × 6; please align it.
4. **Face convention.** `left` is the robot's left (+X), matching normalize's `_ray`.

## Requests to the library owners

- **Structured display geometry.** Please add `glass_mm {w, h, t, origin}`, `viewing_area_mm {w, h, origin}` and header
  position fields to the display entries. CAD transcribes them from the notes today (`modules.LIB_DETAIL`). The OLED
  1.3" glass size is not in the library, so CAD uses a provisional estimate, marked as such.
- **Back-side component heights.** A `back_clear_mm` field would let clamp straps sit tighter. The INMP441 header
  positions and the KY-004 switch position are estimates (the library says so); CAD uses them as given.

## Known limits (honest list)

- Purchased-part models are simplified envelopes from library dimensions (no pins, latches or cable bends).
- Cable routes are display-only. Each length is compared with the harness length class plus a 20 mm allowance.
- Servo, motor and fan features fall back to a generic clamp mount with a warning; they have no dedicated kit yet.
- Hero renders draw an illustrative idle face on displays. It is not in the CAD or GLB.
- No physical print or fit has been made. The fit coupons are there to be printed first.
