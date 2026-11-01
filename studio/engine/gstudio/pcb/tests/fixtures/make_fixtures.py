"""Generate electrical.json fixtures (gadget-electrical/1 shape, ARCHITECTURE §7) from library/backplane-v1.json.

Used by the PCB tests until/alongside foundation's gstudio.electrical: the nets are exactly the backplane's slot pin
nets for the populated slots, with node ids "<slot>.<pin>" (e.g. "U1.J1-12", "A1.VO+", "J_I2C1.3") plus the test
pads (BP1.TP1.1 ..), which every board carries. Node rules mirror gstudio.electrical.

Run:  python3 -I make_fixtures.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKPLANE = HERE.parents[4] / "library" / "backplane-v1.json"

MINIMAL = ["U1", "J_PWR", "J_I2C1", "J_IO1", "J_LED"]


def electrical(bp: dict, used: list[str], name: str) -> dict:
    """Same backplane-side node rules as gstudio.electrical (foundation): slot pins as BP1.<slot>.<pin>, support
    parts of the fitted groups as BP1.<ref>.<pad>, and U1 header pins that carry a wanted net as U1.<pin> plus
    BP1.<socket>.<socket_pin>. Module-side nodes are omitted (they are off the board)."""
    slots = {s["id"]: s for s in bp["slots"]}
    nets: dict[str, set[str]] = {}

    def add(net, node):
        if net:
            nets.setdefault(net, set()).add(node)

    groups = {"power", "usb", "test"}
    if "J_LED" in used:
        groups.add("led")
    if any(s.startswith("J_I2C") for s in used):
        groups.add("i2c")
    if "A1" in used:
        groups.add("audio")
    for sid in used:
        if sid == "U1":
            continue
        for p in slots[sid]["pins"]:
            add(p.get("net"), f"BP1.{sid}.{p['pin']}")
    for part in bp["parts"]:
        if part.get("group") in groups and part.get("pins"):
            for pad, net in part["pins"].items():
                add(net, f"BP1.{part['ref']}.{pad}")
    wanted = set(nets) | set(bp.get("always_nets", []))
    for p in slots["U1"]["pins"]:
        if p.get("net") in wanted:
            add(p["net"], f"U1.{p['pin']}")
            add(p["net"], f"BP1.{p['socket']}.{p['socket_pin']}")
    return {
        "schema": "gadget-electrical/1",
        "backplane": bp["id"],
        "fixture": name,
        "core": {"ref": "U1", "module": bp["core"]["module"]},
        "slots_used": used,
        "gpio": dict(bp["gpio"]),
        "nets": [{"name": k, "nodes": sorted(v)} for k, v in sorted(nets.items())],
        "instances": [],
        "connections": [],
        "power": {"rails": [{"name": r["name"], "source": r["source"]} for r in bp["power"]["rails"]]},
    }


def main() -> None:
    bp = json.loads(BACKPLANE.read_text())
    full = [s["id"] for s in bp["slots"]]  # every slot the backplane defines
    (HERE / "electrical.full.json").write_text(json.dumps(electrical(bp, full, "full"), indent=2) + "\n")
    (HERE / "electrical.minimal.json").write_text(json.dumps(electrical(bp, MINIMAL, "minimal"), indent=2) + "\n")


if __name__ == "__main__":
    main()
