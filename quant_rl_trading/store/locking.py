"""POSIX local-store write exclusion; lock files stay so waiters share one inode."""

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from threading import RLock, local

_registry_lock = RLock()
_account_locks: dict[tuple[int, str], RLock] = {}
_held = local()


@contextmanager
def account_lock(root: Path) -> Iterator[None]:
    """One FUND per root. Reentrant within a thread, exclusive across processes."""
    identity = (os.getpid(), str(root.resolve()))
    with _registry_lock:
        lock = _account_locks.setdefault(identity, RLock())
    with lock:
        held = getattr(_held, "accounts", set())
        if identity in held:
            yield
            return
        with ingest_lock(root, "account", "FUND"):
            _held.accounts = held | {identity}
            try:
                yield
            finally:
                _held.accounts = held


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
