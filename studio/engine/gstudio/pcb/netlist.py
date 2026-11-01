"""Netlist extraction from the generated schematic (kicad-cli) and comparison with electrical.json."""

from __future__ import annotations

from pathlib import Path

from . import sexpr
from .design import Design, resolve_node


def parse_kicad_netlist(path: Path) -> dict[str, list[tuple[str, str]]]:
    root = sexpr.parse(path.read_text())
    nets_node = sexpr.find(root, "nets")
    out: dict[str, list[tuple[str, str]]] = {}
    for net in sexpr.find_all(nets_node or [], "net"):
        name = str(sexpr.find(net, "name")[1])
        nodes = []
        for nd in sexpr.find_all(net, "node"):
            ref = str(sexpr.find(nd, "ref")[1])
            pin = str(sexpr.find(nd, "pin")[1])
            if ref.startswith("#"):
                continue
            nodes.append((ref, pin))
        if name.startswith("/"):
            name = name[1:]
        out[name] = sorted(set(nodes))
    return out


def _is_unconnected(name: str) -> bool:
    return name.startswith("unconnected-") or name.startswith("Net-(")


def compare(design: Design, sch_nets: dict[str, list[tuple[str, str]]], bp: dict) -> dict:
    """Check the schematic netlist against (a) the design model and (b) electrical.json.

    (a) exact: every design net == schematic net (same name, same node set); no extra named nets.
    (b) electrical.json: for each of its nets, the on-board nodes of populated slots must sit on the schematic
        net of the same name, and no schematic net may join two electrical.json nets. Nets that touch only one
        on-board pad are left unconnected by design (reported, not an error).
    """
    problems: list[str] = []
    want = {k: sorted(set(v)) for k, v in design.nets().items()}
    got = {k: v for k, v in sch_nets.items() if not _is_unconnected(k)}
    for net, nodes in want.items():
        if net not in got:
            problems.append(f"design net {net} missing from schematic")
        elif got[net] != nodes:
            problems.append(f"net {net}: schematic {got[net]} != design {nodes}")
    for net in got:
        if net not in want:
            problems.append(f"schematic has extra net {net}: {got[net]}")
    # electrical.json
    used = {p.ref for p in design.parts if not p.board_only}
    node_net = {n: k for k, v in got.items() for n in v}
    split_ok = []
    checked = 0
    single = []
    for net, nodes in design.electrical_nets.items():
        onboard = []
        for nd in nodes:
            r = resolve_node(nd, bp)
            if r and r[0] in used:
                onboard.append(r)
        if len(onboard) < 2 and net not in want:
            single.append(net)
            continue
        for r in onboard:
            checked += 1
            key = f"{r[0]}.{r[1]}"
            if key in design.splits:
                if node_net.get(r) == design.splits[key]:
                    split_ok.append(f"{key}: {net} -> {design.splits[key]} (series part)")
                    continue
                problems.append(f"{key}: expected split net {design.splits[key]}, schematic has {node_net.get(r)!r}")
            elif node_net.get(r) != net:
                problems.append(f"electrical.json {net}: {key} is on schematic net {node_net.get(r)!r}")
    return {"ok": not problems, "problems": problems, "electrical_nodes_checked": checked,
            "intentional_splits": split_ok, "single_pad_nets_left_unconnected": sorted(single), "nets": len(got)}


def to_json(sch_nets: dict[str, list[tuple[str, str]]]) -> dict:
    return {"schema": "gadget-netlist/1",
            "nets": [{"name": k, "nodes": [f"{r}.{p}" for r, p in v]}
                     for k, v in sorted(sch_nets.items()) if not _is_unconnected(k)],
            "unconnected_pins": sorted(f"{r}.{p}" for k, v in sch_nets.items() if _is_unconnected(k) for r, p in v)}


__all__ = ["parse_kicad_netlist", "compare", "to_json"]
