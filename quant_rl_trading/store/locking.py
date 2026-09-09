"""POSIX local-store write exclusion; lock files stay so waiters share one inode."""

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path


@contextmanager
def ingest_lock(root: Path, table: str, ingest_run_id: str) -> Iterator[None]:
    key = sha256(f"{table}\0{ingest_run_id}".encode()).hexdigest()
    directory = root.resolve() / "_locks"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"{key}.lock").open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
