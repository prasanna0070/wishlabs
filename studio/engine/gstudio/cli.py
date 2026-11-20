"""Command line for the Wish Labs engine.

  python -m gstudio build    <spec.json> <out> [--only cad,pcb] [--fast] [--version N] [--brief-file brief.json]
                             [--stories-audio | --no-stories-audio] [--price-check live|cache_only|off] [--no-auto-repair]
  python -m gstudio preview  <spec.json> <out>
  python -m gstudio validate <spec.json>

`build` prints one line per stage transition to stderr and the summary JSON to stdout (or a short table with
--quiet). Exit code: 0 when every selected stage is done or skipped, 1 when a stage failed, 2 on bad usage.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def _printer(quiet: bool):
    seen: dict[str, str] = {}
    said = {"n": 0, "draft": 0}

    def cb(status: dict) -> None:
        if quiet:
            return
        msgs = status.get("messages") or []
        for m in msgs[said["n"]:]:
            print(f"[{time.strftime('%H:%M:%S')}]        > {m.get('text')}", file=sys.stderr, flush=True)
        said["n"] = len(msgs)
        if (status.get("draft_rev") or 0) > said["draft"]:
            said["draft"] = status["draft_rev"]
            print(f"[{time.strftime('%H:%M:%S')}]        draft r{said['draft']}: {status.get('draft')}", file=sys.stderr, flush=True)
        for s in status.get("stages", []):
            if seen.get(s["id"]) != s["state"]:
                seen[s["id"]] = s["state"]
                if s["state"] == "queued":
                    continue
                extra = f" ({s['seconds']:.1f} s)" if s.get("seconds") is not None and s["state"] != "running" else ""
                print(f"[{time.strftime('%H:%M:%S')}] {status.get('percent', 0):5.1f}% {s['id']:<10} {s['state']:<8}"
                      f"{extra} {s.get('message', '')[:140]}", file=sys.stderr, flush=True)

    return cb


def _read_json(path: str):
    return json.loads(Path(path).read_text())


def brief_facts(brief: dict) -> dict:
    """MAJ-36: {brief_version, concept_id} recorded in project.json, read from the approved brief itself."""
    out = {}
    if not isinstance(brief, dict):
        return out
    v = next((brief.get(k) for k in ("version", "brief_version", "v") if brief.get(k) is not None), None)
    if v is None and isinstance(brief.get("meta"), dict):
        v = brief["meta"].get("version")
    if v is not None:
        out["brief_version"] = v
    c = next((brief.get(k) for k in ("concept_id", "selected_concept", "concept") if brief.get(k) is not None), None)
    if isinstance(c, dict):
        c = c.get("id")
    if c is not None:
        out["concept_id"] = c
    return out


def cmd_build(a: argparse.Namespace) -> int:
    from gstudio.pipeline import build, parse_only

    try:
        only = parse_only(a.only)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    opts = {"only": only, "fast": a.fast}
    if a.stories_audio is not None:
        opts["stories_audio"] = a.stories_audio  # unset: the pipeline turns it on for story_player (BLK-4)
    if a.price_check:
        opts["price_check"] = a.price_check
    if a.no_auto_repair:
        opts["auto_repair"] = False
    if a.version is not None:
        opts["version"] = a.version
    if a.project_id:
        opts["project_id"] = a.project_id
    if a.brief_file:
        opts["brief"] = _read_json(a.brief_file)
        opts.update(brief_facts(opts["brief"]))
    if a.options_file:
        extra = _read_json(a.options_file)
        opts.update(extra)
        if isinstance(extra.get("brief"), dict):
            for k, v in brief_facts(extra["brief"]).items():
                if extra.get(k) is None:
                    opts[k] = v
    if a.jobs:
        opts["jobs"] = a.jobs
    from gstudio.pipeline import Terminated

    try:
        summary = build(_read_json(a.spec), a.out, opts, _printer(a.quiet))
    except Terminated:
        print("terminated: stage processes stopped, status.json records the error", file=sys.stderr)
        return 143
    if a.json:
        print(json.dumps(summary, indent=1, default=str))
    else:
        print(f"{summary['state']} in {summary['seconds']:.1f} s -> {summary['out']}")
        for sid, s in summary["stages"].items():
            sec = f"{s['seconds']:.1f}s" if s.get("seconds") is not None else "-"
            print(f"  {sid:<10} {s['state']:<8} {sec:>8}  {(s.get('message') or '')[:110]}")
        if summary.get("error"):
            print(f"error: {summary['error']}")
    return 0 if summary["state"] == "done" else 1


def cmd_validate(a: argparse.Namespace) -> int:
    from gstudio.pipeline import validate

    r = validate(_read_json(a.spec))
    if not a.full and r.get("normalized"):
        r = dict(r, normalized={"name": r["normalized"].get("name"), "slug": r["normalized"].get("slug"),
                                "instances": [i.get("ref") for i in (r["normalized"].get("derived") or {}).get("instances", [])]})
    print(json.dumps(r, indent=1, default=str))
    return 0 if r["ok"] else 1


def cmd_preview(a: argparse.Namespace) -> int:
    from gstudio.pipeline import preview

    t0 = time.perf_counter()
    r = preview(_read_json(a.spec), a.out)
    r["seconds"] = round(time.perf_counter() - t0, 2)
    print(json.dumps(r, indent=1, default=str))
    return 0 if r["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    a = make_parser().parse_args(argv)
    return a.fn(a)


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gstudio", description="Wish Labs engine")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build every artefact for a spec")
    b.add_argument("spec")
    b.add_argument("out")
    b.add_argument("--only", help="comma-separated stages: normalize,electrical,cad,pcb,firmware,order,manual,checks,docs,package")
    b.add_argument("--fast", action="store_true", help="fewer Monte Carlo draws and faster stage settings")
    sa = b.add_mutually_exclusive_group()
    sa.add_argument("--stories-audio", dest="stories_audio", action="store_true", default=None,
                    help="synthesise story audio (Vertex TTS) in the firmware stage (default: on for story_player)")
    sa.add_argument("--no-stories-audio", dest="stories_audio", action="store_false", help="text-only story card")
    b.add_argument("--price-check", choices=("live", "cache_only", "off"), help="re-read vendor listings for seen prices (default live)")
    b.add_argument("--no-auto-repair", action="store_true", help="no automatic repair round when the CAD collision check fails")
    b.add_argument("--version", type=int, help="version number recorded in project.json")
    b.add_argument("--project-id")
    b.add_argument("--brief-file", help="approved brief JSON recorded in project.json")
    b.add_argument("--options-file", help="extra options JSON merged into the build options")
    b.add_argument("--jobs", type=int, help="max stage processes in parallel (default 4)")
    b.add_argument("--json", action="store_true", help="print the full summary JSON on stdout")
    b.add_argument("--quiet", action="store_true", help="no per-stage progress lines")
    b.set_defaults(fn=cmd_build)
    v = sub.add_parser("validate", help="validate + normalize a spec; prints {ok, errors, normalized}")
    v.add_argument("spec")
    v.add_argument("--full", action="store_true", help="print the whole normalized spec")
    v.set_defaults(fn=cmd_validate)
    pv = sub.add_parser("preview", help="shell-only preview render (PNG + GLB)")
    pv.add_argument("spec")
    pv.add_argument("out")
    pv.set_defaults(fn=cmd_preview)
    return p


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
