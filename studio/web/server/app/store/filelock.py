"""Exclusive lock on an open file, across processes: flock on Unix, a byte-range lock on Windows."""
from __future__ import annotations

import sys
from typing import IO

if sys.platform == "win32":
    import errno
    import msvcrt
    import time

    _POLL_S = 0.01
    _BUSY = (errno.EACCES, errno.EDEADLK)

    def lock(f: IO) -> None:
        # LK_LOCK gives up after 10 tries; flock waits for as long as it takes, so poll the non-blocking call.
        while True:
            f.seek(0)
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError as e:
                if e.errno not in _BUSY:
                    raise
                time.sleep(_POLL_S)

    def unlock(f: IO) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def lock(f: IO) -> None:
        fcntl.flock(f, fcntl.LOCK_EX)

    def unlock(f: IO) -> None:
        fcntl.flock(f, fcntl.LOCK_UN)
