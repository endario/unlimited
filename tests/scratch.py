"""A temporary directory removed when the test run exits."""
from __future__ import annotations

import atexit
import shutil
import tempfile


def mkdtemp() -> str:
    d = tempfile.mkdtemp()
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return d
