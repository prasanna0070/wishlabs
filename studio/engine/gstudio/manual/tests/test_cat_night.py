"""Review M2 of hack/catalog 12a1874 (i-to-a.md): the build guide of a night light with a motion sensor says it is not a
baby monitor, and a child's night light and its cord stay out of reach (never in or on a cot). Pure Python."""

from __future__ import annotations

from types import SimpleNamespace as NS

from gstudio.manual.guide import night_light_lines


def _a(*roles):
    return NS(features=[NS(role=r) for r in roles], internals=[])


def _d(flags, child=False):
    return NS(spec={"safety": {"flags": list(flags)}, "audience": {"child_directed": child}})


def test_baby_night_light_gets_both_lines():
    out = " ".join(night_light_lines(_d(["CHILD_DIRECTED", "NIGHT_USE"], child=True), _a("led", "motion", "button")))
    assert "not a baby monitor" in out and "under a blanket" in out
    assert "out of the child's reach" in out and "never in or on a cot" in out


def test_child_flag_alone_counts_as_child():
    out = " ".join(night_light_lines(_d(["CHILD_DIRECTED", "NIGHT_USE"]), _a("led")))
    assert "never in or on a cot" in out and "baby monitor" not in out  # no motion sensor: no monitor sentence


def test_adult_hallway_light_with_motion_gets_only_the_monitor_line():
    out = night_light_lines(_d(["NIGHT_USE"]), _a("led", "motion"))
    assert len(out) == 1 and "not a baby monitor" in out[0]


def test_no_night_use_no_lines():
    assert night_light_lines(_d([], child=True), _a("led", "motion")) == []
