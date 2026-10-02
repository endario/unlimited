"""Locked, private atomic JSON state files."""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def flock(path: Path):
    """Hold this state file's lock through a read-modify-write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path.with_name(path.stem + ".lock"), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def write_json(value: object, path: Path, *, indent: int | None = None) -> None:
    """Replace one private JSON state file without publishing a partial value."""
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(value, f, indent=indent)
        f.write("\n")
    os.replace(tmp, path)
