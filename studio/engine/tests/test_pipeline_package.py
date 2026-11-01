"""Package stage: delivered-file set, manifest verification (QB-44), zip layout, delivery counts from files (QB-45)."""
import json
import zipfile
from pathlib import Path

from gstudio import package


def make_tree(out: Path) -> None:
    for rel, data in {"spec.json": "{}", "build.json": json.dumps({"steps": [{"id": "a", "visuals": [{"path": "manual/a.png"}, {"path": "manual/missing.png"}]}],
                                                                   "purchased": [{"name": "x", "estimate": True}, {"name": "y", "estimate": False}],
                                                                   "tests": [{"title": "Power on", "expected": "boots", "kind": "physical"}]}),
                      "cad/components.json": json.dumps({"PRINT_Shell": {"bbox": [-10, 10, -5, 5, 0, 30]}, "REFERENCE_Cable": {"bbox": [-99, 99, 0, 1, 0, 1]}}),
                      "print/PRINT_Shell.stl": "solid x\nendsolid x\n", "print/TEST_Coupon.stl": "solid t\nendsolid t\n",
                      "manual/a.png": "png", "manual/orphan.png": "png", "BUILD_GUIDE.md": "# guide\n",
                      "logs/cad.log": "log", ".gstudio/stages/cad.json": "{}", "status.json": "{}", "firmware/.pio/build/x.o": "obj",
                      "firmware/src/main.cpp": "int main(){}", ".hidden": "x", "cad/.params.json.tmp": "x"}.items():
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data)


def test_delivered_files_excludes_runtime_state(tmp_path):
    make_tree(tmp_path)
    rels = [p.relative_to(tmp_path).as_posix() for p in package.delivered_files(tmp_path)]
    assert "spec.json" in rels and "firmware/src/main.cpp" in rels and "print/PRINT_Shell.stl" in rels
    for bad in ("logs/cad.log", ".gstudio/stages/cad.json", "status.json", "firmware/.pio/build/x.o", ".hidden", "cad/.params.json.tmp"):
        assert bad not in rels
    assert rels == sorted(rels)


def test_manifest_verifies_and_detects_tampering(tmp_path):
    make_tree(tmp_path)
    files = package.delivered_files(tmp_path)
    m = package.make_manifest(tmp_path, files, "2026-11-01T00:00:00Z")
    assert len(m["files"]) == len(files) and all(len(f["sha256"]) == 64 for f in m["files"])
    (tmp_path / "manifest.json").write_text(json.dumps(m))
    assert package.verify_manifest(tmp_path, m) == []
    (tmp_path / "spec.json").write_text("{ }")  # same size, different bytes? no: one more byte
    probs = package.verify_manifest(tmp_path, m)
    assert any("spec.json" in p for p in probs)
    (tmp_path / "spec.json").write_text("{}")
    (tmp_path / "extra.txt").write_text("new")
    assert any("not in manifest: extra.txt" in p for p in package.verify_manifest(tmp_path, m))


def test_zip_has_top_folder_and_is_deterministic(tmp_path):
    make_tree(tmp_path)
    files = package.delivered_files(tmp_path)
    m = package.make_manifest(tmp_path, files, "t")
    (tmp_path / "manifest.json").write_text(json.dumps(m))
    z1 = tmp_path / "export.zip"
    package.make_zip(tmp_path, "luna-owl", files, z1)
    b1 = z1.read_bytes()
    package.make_zip(tmp_path, "luna-owl", files, z1)
    assert z1.read_bytes() == b1
    assert package.verify_zip(z1, "luna-owl", m) == []
    with zipfile.ZipFile(z1) as zf:
        names = zf.namelist()
    assert all(n.startswith("luna-owl/") for n in names)
    assert "luna-owl/manifest.json" in names and "luna-owl/spec.json" in names
    assert not any("logs/" in n or "export.zip" in n for n in names)


def test_delivery_counts_come_from_files(tmp_path):
    make_tree(tmp_path)
    build = json.loads((tmp_path / "build.json").read_text())
    d = package.delivery_checks(tmp_path, build, {"summary": {"passed": 3}, "checks": [{"id": "watertight:PRINT_Shell", "status": "passed"}]})
    assert d["printFiles"] == 1 and d["testCoupons"] == 1
    assert d["manualImages"] == 2 and d["missingImages"] == ["manual/missing.png"] and d["orphanImages"] == ["manual/orphan.png"]
    assert d["assemblySteps"] == 1 and d["purchasedEntries"] == 2 and d["unconfirmedPrices"] == ["x"]
    assert d["assemblySizeMm"] == [20, 10, 30]          # REFERENCE_ envelopes excluded
    assert d["watertightPrintFiles"] == 1
    assert d["physicalChecksStillNeeded"] == [{"title": "Power on", "expected": "boots"}]
    assert d["physicalTesting"].startswith("Not performed")


def test_default_physical_checks_when_manual_lists_none(tmp_path):
    d = package.delivery_checks(tmp_path, {"tests": [{"title": "fw", "kind": "digital", "status": "passed"}]}, None)
    assert len(d["physicalChecksStillNeeded"]) == len(package.DEFAULT_PHYSICAL)
    assert "default" in d["physicalChecksSource"]


def test_checks_tolerate_failed_stage_records(tmp_path):
    """A failed stage's record has summary: null; the file checks must still run (regression)."""
    from gstudio import checks

    (tmp_path / "firmware").mkdir()
    (tmp_path / "pcb").mkdir()
    (tmp_path / "pcb" / "drc.json").write_text(json.dumps({"violations": [{"severity": "error"}, {"severity": "warning"}], "unconnected_items": [1]}))
    recs = {"firmware": {"state": "failed", "summary": None}, "pcb": {"state": "done", "summary": None}, "cad": {}}
    cs = {c["id"]: c for c in checks.file_checks(tmp_path, recs)}
    assert cs["fw-compiled"]["status"] == "failed"
    assert cs["pcb-drc"]["status"] == "failed" and "1 errors, 1 warnings, 1 unconnected" in cs["pcb-drc"]["detail"]


def test_pins_cross_check_per_instance_pin(tmp_path):
    from gstudio import checks

    (tmp_path / "firmware" / "include").mkdir(parents=True)
    elec = {"gpio": {"I2C_SDA": 8, "IO1_SIG": 4},
            "connections": [{"from": "DISP1.SDA", "to": "BP1.J_I2C1.3", "net": "I2C_SDA"}, {"from": "BTN1.SIG", "to": "BP1.J_IO1.3", "net": "IO1_SIG"},
                            {"from": "LED1.DIN", "to": "BP1.J_LED.3", "net": "LED_DIN_5V"}]}
    spec = {"electronics": {"power": {"input": "usb_c_5v"}}}
    (tmp_path / "firmware" / "include" / "pins.h").write_text("#define GS_DISP1_SDA 8\n#define GS_BTN1_SIG 4\n")
    c = {x["id"]: x for x in checks.cross_checks(tmp_path, spec, {}, elec)}["pins-match"]
    assert c["status"] == "passed" and "2 constants compared" in c["detail"]
    (tmp_path / "firmware" / "include" / "pins.h").write_text("#define GS_DISP1_SDA 8\n#define GS_BTN1_SIG 5\n")
    c = {x["id"]: x for x in checks.cross_checks(tmp_path, spec, {}, elec)}["pins-match"]
    assert c["status"] == "failed" and "GS_BTN1_SIG: electrical 4" in c["detail"]


def test_stage_physical_checks_reach_delivery_checks(tmp_path):
    (tmp_path / ".gstudio" / "stages").mkdir(parents=True)
    (tmp_path / ".gstudio" / "stages" / "pcb.json").write_text(json.dumps({"state": "done", "summary": {"checks": [
        {"id": "pcb.fabricated", "title": "A fabricated board passes continuity", "kind": "physical", "status": "not_run", "detail": "order and probe"},
        {"id": "pcb.drc", "title": "DRC", "kind": "digital", "status": "passed"}]}}))
    d = package.delivery_checks(tmp_path, {"tests": [{"title": "Power on", "expected": "boots", "kind": "physical"}]}, None)
    titles = [x["title"] for x in d["physicalChecksStillNeeded"]]
    assert titles == ["Power on", "A fabricated board passes continuity"]
