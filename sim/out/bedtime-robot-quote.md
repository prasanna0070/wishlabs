# Bedtime robot: simulated build cost

Prompt: *I want a cute robot that tells my daughter bedtime stories and remembers her favorite characters.*

Shell sliced for real: **127 g PETG, 9.8 h print** (PrusaSlicer 2.7.2, 0.4 mm nozzle, 0.2 mm layers, 3 perimeters, 15% gyroid, PETG, supports from plate only).
All 8 quotes (20,000 draws each) computed in 1.57 s.


## B. Parent-run (recommended)

| | pilot one-off today | pilot batch of 10 variants | steady state, 100+/month | steady state, toy-style returns |
|---|---:|---:|---:|---:|
| Electronics + hardware (catalog) | ₹4,060 | ₹3,816 | ₹3,510 | ₹3,510 |
| Printed shell + base plate | ₹840 | ₹840 | ₹840 | ₹840 |
| Assembly, flashing, test (labour) | ₹653 | ₹337 | ₹337 | ₹337 |
| One-time setup, spread | ₹3,000 | ₹300 | ₹0 | ₹0 |
| Courier to customer | ₹252 | ₹252 | ₹252 | ₹252 |
| Warranty reserve | ₹284 | ₹257 | ₹243 | ₹243 |
| Returns, spread over kept orders | ₹539 | ₹385 | ₹351 | ₹2,584 |
| Landed cost per kept order | ₹9,628 | ₹6,187 | ₹5,533 | ₹7,766 |
| Simulated landed cost p5 / p50 / p95 | ₹9,063 / ₹9,621 / ₹10,218 | ₹5,718 / ₹6,183 / ₹6,672 | ₹5,064 / ₹5,524 / ₹6,031 | ₹6,986 / ₹7,752 / ₹8,596 |
| **Price to charge, incl. 18% GST** | **₹16,899** | **₹10,899** | **₹9,699** | **₹13,699** |
| Gross margin at that price (p50 / p95 cost) | 30% / 26% | 30% / 25% | 30% / 24% | 30% / 23% |

Cloud AI running cost (not in the price above): ₹15 to ₹90 a month, likely ₹40.
Quote status: **ESTIMATE_REVIEW_REQUIRED**. (1) child-directed product: needs safety, toy-category and child-data review before any binding price (blueprint §11). (2) no supplier rate card or contract: every cost is a planning range or assumption (blueprint §7.1, §1.3). (3) delivery pincode unknown: courier cost is an estimate (blueprint §8.5).

## A. As asked (mic + live AI)

| | pilot one-off today | pilot batch of 10 variants | steady state, 100+/month | steady state, toy-style returns |
|---|---:|---:|---:|---:|
| Electronics + hardware (catalog) | ₹4,039 | ₹3,796 | ₹3,492 | ₹3,492 |
| Printed shell + base plate | ₹840 | ₹840 | ₹840 | ₹840 |
| Assembly, flashing, test (labour) | ₹653 | ₹337 | ₹337 | ₹337 |
| One-time setup, spread | ₹3,000 | ₹300 | ₹0 | ₹0 |
| Courier to customer | ₹252 | ₹252 | ₹252 | ₹252 |
| Warranty reserve | ₹282 | ₹256 | ₹242 | ₹242 |
| Returns, spread over kept orders | ₹538 | ₹384 | ₹350 | ₹2,576 |
| Landed cost per kept order | ₹9,605 | ₹6,165 | ₹5,513 | ₹7,738 |
| Simulated landed cost p5 / p50 / p95 | ₹9,042 / ₹9,601 / ₹10,195 | ₹5,697 / ₹6,163 / ₹6,647 | ₹5,046 / ₹5,505 / ₹6,008 | ₹6,969 / ₹7,725 / ₹8,568 |
| **Price to charge, incl. 18% GST** | **₹16,899** | **₹10,899** | **₹9,699** | **₹13,599** |
| Gross margin at that price (p50 / p95 cost) | 30% / 26% | 30% / 25% | 30% / 24% | 30% / 23% |

Cloud AI running cost (not in the price above): ₹90 to ₹450 a month, likely ₹200.
Quote status: **UNSUPPORTED_AT_LAUNCH**. (1) child-directed device with a microphone and cloud AI: excluded at launch (blueprint §1.1, §4.1). (2) no supplier rate card or contract: every cost is a planning range or assumption (blueprint §7.1, §1.3). (3) delivery pincode unknown: courier cost is an estimate (blueprint §8.5).

Cross-check: an Indian print bureau at ₹3-5/cm³ (IamRapid, published) would charge about ₹301-₹501 for the two printed parts (100 cm³ of model volume).

## Margin at the purchase-test prices (parent-run, prices incl. 18% GST)

| Scenario | ₹5,999 | ₹7,999 | ₹9,999 |
|---|---:|---:|---:|
| pilot one-off today | -92% (bad case -104%) | -45% (bad case -54%) | -16% (bad case -23%) |
| pilot batch of 10 variants | -24% (bad case -34%) | 6% (bad case -1%) | 24% (bad case 18%) |
| steady state, 100+/month | -11% (bad case -21%) | 16% (bad case 8%) | 32% (bad case 26%) |
| steady state, toy-style returns | -55% (bad case -72%) | -17% (bad case -30%) | 6% (bad case -4%) |

## Biggest levers (steady state, parent-run)

| Change | Typical cost change | Price change | Basis |
|---|---:|---:|---|
| Ship without the USB-C adapter | -₹446 | -₹700 | drops catalog row 04; parent uses any phone charger (also avoids BIS adapter registration) |
| Drop the microSD story library | -₹258 | -₹400 | drops catalog row 37; stories fit in the board's own flash, fewer offline |
| Faster printer (Bambu-class) | -₹207 | -₹300 | ASSUMPTION: print time x0.45 vs the Prusa-default slicer estimate |
| Smaller box (20 x 15 x 17 cm) | -₹61 | -₹100 | ASSUMPTION: tighter packaging drops billable weight |
| Toy-style returns (20-30%) | ₹2,228 | ₹4,000 | RESEARCH: BubblePal return rate |

Costliest parts (blueprint planning ranges): ESP32-S3 core dev board, flash+PSRAM ₹650-₹1,300; Approved external USB-C adapter ₹250-₹600; Standard expansion/backplane PCB ₹120-₹450; MicroSD module + qualified storage ₹120-₹380; 0.96-inch mono OLED ₹130-₹350; I2S amplifier board ₹110-₹300.
