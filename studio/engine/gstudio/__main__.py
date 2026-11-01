"""`python -m gstudio ...` entry point (see gstudio.cli)."""
import os
import sys

# Stages run as parallel forked processes; a multi-threaded BLAS pool in the parent makes fork() unsafe and
# oversubscribes the CPUs. Set before anything imports numpy. An explicit environment value still wins.
for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

from gstudio.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
