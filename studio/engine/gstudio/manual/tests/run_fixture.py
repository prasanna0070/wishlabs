"""Dev helper: build the Mini Bot fixture and run the manual stage on it.

    PYTHONPATH=engine python engine/gstudio/manual/tests/run_fixture.py <out_dir> [--workers N]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "fixtures"))

from conftest import make_ctx  # noqa: E402
import make_minibot  # noqa: E402


def main() -> None:
    out = Path(sys.argv[1])
    workers = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else 0
    if "--keep" not in sys.argv or not (out / "cad").is_dir():
        t = time.time()
        make_minibot.build(out)
        print(f"fixture built in {time.time() - t:.1f}s", file=sys.stderr)
    from gstudio.manual import run

    opts = {"manual_workers": workers} if workers else {}
    ctx = make_ctx(out, **opts)
    ctx.progress = lambda s, m, p=None: print(f"[{time.strftime('%H:%M:%S')}] {s} {m}", file=sys.stderr, flush=True)
    s = run(ctx)
    print(json.dumps({k: v for k, v in s.items() if k not in ("images",)}, indent=1))


if __name__ == "__main__":
    main()
