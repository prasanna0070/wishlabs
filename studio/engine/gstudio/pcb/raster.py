"""SVG -> PNG via the headless Chromium shipped for Playwright (no cairo/inkscape on the builder)."""

from __future__ import annotations

import glob
import os
import re
import subprocess
import tempfile
from pathlib import Path


def chromium() -> str | None:
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/gs/pw/browsers")
    for pat in ("chromium_headless_shell-*/chrome-linux/headless_shell", "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
                "chromium-*/chrome-linux/chrome", "chromium-*/chrome-linux64/chrome"):
        hits = sorted(glob.glob(os.path.join(base, pat)))
        if hits:
            return hits[-1]
    return None


def svg_size(svg: Path) -> tuple[float, float]:
    head = svg.read_text(errors="ignore")[:4000]
    m = re.search(r'viewBox="([-\d.]+)\s+([-\d.]+)\s+([\d.]+)\s+([\d.]+)"', head)
    if m:
        return float(m.group(3)), float(m.group(4))
    w = re.search(r'width="([\d.]+)', head)
    h = re.search(r'height="([\d.]+)', head)
    return (float(w.group(1)) if w else 1000.0, float(h.group(1)) if h else 700.0)


def svg_to_png(svg: Path, png: Path, width: int = 2400, background: str = "#ffffff") -> Path | None:
    exe = chromium()
    if exe is None:
        return None
    vw, vh = svg_size(svg)
    height = max(200, int(round(width * vh / vw)))
    with tempfile.TemporaryDirectory() as td:
        html = Path(td) / "page.html"
        html.write_text(f"<!doctype html><html><head><meta charset='utf-8'><style>html,body{{margin:0;background:{background}}}"
                        f"img{{display:block;width:{width}px;height:{height}px}}</style></head>"
                        f"<body><img src='file://{svg.resolve()}'></body></html>")
        cmd = [exe, "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--allow-file-access-from-files",
               f"--window-size={width},{height}", f"--screenshot={png.resolve()}", f"file://{html}"]
        try:
            subprocess.run(cmd, capture_output=True, timeout=90, check=False)
        except subprocess.TimeoutExpired:
            return None
    return png if png.exists() else None


__all__ = ["svg_to_png", "chromium"]
