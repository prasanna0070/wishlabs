# Wish Labs benchmarks, 21 Nov 2026

Run on the live production studio at [wish.plugbook.si](https://wish.plugbook.si) in standard mode (Gemini 3.5
Flash; concept images by Nano Banana Pro), with no human help: the agent answered its own questions with its
defaults. Every build below has a public, read-only share link.

## 1. Text2CAD-Bench prompts, end to end

[Text2CAD-Bench](https://huggingface.co/datasets/AICAD/Text2CAD-Bench) (AICAD, v1.0, Feb 2026) measures text to a
valid CAD model. We sent 10 of its public prompts (L1: 4, L2: 4, L3: 2, matching the leaderboard's 40/40/20
weighting), each as written (the non-expert "geo" style) after one line: *"Make this as a 3D-printed part, no
electronics:"*. The studio's agent wrote the shape; the engine built it, checked it (single solid, no collisions,
printable overhangs and walls), sliced it and priced it.

**Score: 9 of 10 buildable** (L1 3 of 4, L2 4 of 4, L3 2 of 2). Weighted invalid rate **10%**. Median 5.9 minutes
a part; model cost $1.00 median, $9.66 for the run.

| Published invalid rate | |
|---|---|
| DeepSeek-V3.2 | 29.7% |
| GPT-5.2 | 30.6% |
| Claude-4.5-Sonnet | 41.3% |
| Best domain model (Text2CAD) | 7.0% |
| **Wish Labs (this run)** | **10%** |

| Case | Prompt (start) | Result | Failed checks | Price | Min | Model $ | Build |
|---|---|---|---|---|---|---|---|
| L1_1 | The model is a prismatic solid based on a regular hexagon... | not buildable | bought rubber feet, placed none | ₹6,999 | 4.9 | 1.06 | [open](https://wish.plugbook.si/studio/s/s_bmnbbeecwi37858a2ic6jewc) |
| L1_16 | This is a rectangular plate with two symmetrical circular holes... | **buildable** | none | ₹7,299 | 3.2 | 0.98 | [open](https://wish.plugbook.si/studio/s/s_3chd5c4f6r637ukeg44x8r3a) |
| L1_31 | This is a sphere with a square through-hole in the center... | **buildable** | none | ₹6,699 | 4.7 | 1.01 | [open](https://wish.plugbook.si/studio/s/s_wma3kajqmbj3dj9j9wnbiv6j) |
| L1_46 | The Z-axis is the main axis/thickness direction... | **buildable** | none | ₹6,599 | 4.9 | 0.82 | [open](https://wish.plugbook.si/studio/s/s_nstz7mhh2rd6rz6qmrp6va28) |
| L2_1 | The shape is enclosed by concave surfaces... an apple core... | **buildable** | none | ₹7,299 | 6.2 | 1.27 | [open](https://wish.plugbook.si/studio/s/s_wt7tgxxnuys75upqerc87y75) |
| L2_16 | This is a pipe fitting with a central bulge... | **buildable** | unsupported_ceiling | ₹7,799 | 5.6 | 0.92 | [open](https://wish.plugbook.si/studio/s/s_6mckvz93ptcxaa86ue77wvuj) |
| L2_31 | The geometric challenge of creating a square hole on a spherical surface... | **buildable** | min_wall, unsupported_ceiling (+1) | ₹7,199 | 7.4 | 0.54 | [open](https://wish.plugbook.si/studio/s/s_khzz482puhpsgb2hzm6jvpv7) |
| L2_45 | This is a flexible hose with a wavy wall... | **buildable** | none | ₹7,099 | 7.5 | 1.18 | [open](https://wish.plugbook.si/studio/s/s_w7da7z3cjs6ahb65nep5deqv) |
| L3_1 | The model is an optical surface for a car headlight reflector... | **buildable** | print_watertight, min_wall (+1) | ₹7,499 | 6.2 | 0.61 | [open](https://wish.plugbook.si/studio/s/s_q53j3yu5qi5uyrpeaysammw2) |
| L3_16 | This is an ergonomic streamlined mouse top cover... | **buildable** | none | ₹7,299 | 7.1 | 1.27 | [open](https://wish.plugbook.si/studio/s/s_s8gy4espf2sb8dzkshbe29k4) |

"Buildable" means printable, gated and priced. Warnings such as `unsupported_ceiling` are shown to the user but
do not block the build.

### Shape spot check

For the L1 prompts, which give every dimension, we compared the printed STL's size and volume with the prompt.
This stands in for the benchmark's Chamfer distance and IoU until its ground-truth STEP files are available.

| Case | Asked (mm) | Built (mm) | Volume asked | Volume built | Largest size error |
|---|---|---|---|---|---|
| L1_1 | 57.7 x 50.0 x 20.0 | 57.7 x 50.0 x 20.0 | 36.67 cm3 | 35.62 cm3 (-3%) | 0.0 mm |
| L1_16 | 120.0 x 40.0 x 10.0 | 120.0 x 40.0 x 20.0 | 46.43 cm3 | 92.31 cm3 (+99%) | 10.0 mm |
| L1_31 | 50.0 x 50.0 x 50.0 | 50.0 x 50.0 x 50.0 | 46.56 cm3 | 52.3 cm3 (+12%) | 0.0 mm |
| L1_46 | 60.0 x 40.0 x 30.0 | 60.0 x 37.0 x 28.5 | 36.0 cm3 | 35.56 cm3 (-1%) | 3.0 mm |

**1 of 4 match the prompt within 1 mm and 5% volume.** Shape fidelity is the weakest part of this run.

## 2. Real gadget wishes, end to end

Six wishes from the showcase run on the same day (labels as recorded by the bench runner). A build passes only when every stage ran (design check,
electronics, CAD, PCB with Freerouting, firmware compile, slicing and price, manual) and the collision gate held.

| Wish | Result | Checks passed | Price | Min | Model $ |
|---|---|---|---|---|---|
| tank | **buildable** | 114 of 119 | ₹21,999 | 8.4 | 0.73 |
| plant | **buildable** | 108 of 112 | ₹23,499 | 8.9 | 1.09 |
| medicine | **buildable** | 113 of 120 | ₹25,299 | 9.2 | 0.76 |
| story | **buildable** | 126 of 131 | ₹27,799 | 9.4 | 0.84 |
| focus | not buildable (build error) | 18 of 22 | held | 24.2 | 0.89 |
| shopqr | **buildable** | 45 of 45 | ₹9,299 | 3.1 | 1.00 |

## What this does and does not show

- **Stricter than the benchmark on validity.** Text2CAD-Bench counts a model invalid when its code fails or its
  solid is broken. We count anything short of a printable, gated, priced part as a failure.
- **Not measured: shape fidelity.** A valid part can still differ from the prompt (see the spot check). Chamfer
  distance and IoU need the benchmark's ground-truth STEP files. That is the next step.
- **Not measured: efficiency.** The gates check correctness, not how close the price or the board is to what an
  expert would design. Next: expert reference bills of materials for 10 wishes.
- **Fixes from this run:** the agent must keep every dimension the user states (it made the L1_16 plate 20 mm
  thick, not 10), and the feet rule must match where the CAD can really place feet (L1_1 bought four feet and
  placed none).
- **Small samples:** 10 prompts and 6 wishes, one run each (pass@1).
- **Where Wish Labs differs:** the benchmark scores one model writing CAD code. Here the model writes a spec or
  constrained CAD code, and a deterministic engine (CAD, collision gate, KiCad, Freerouting, PlatformIO, slicing)
  builds and checks it before a price is shown.

Source for the published numbers: huggingface.co/datasets/AICAD/Text2CAD-Bench (README and release.csv, read
21 Nov 2026).
