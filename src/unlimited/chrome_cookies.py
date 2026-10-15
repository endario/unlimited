"""Sign-in cookies read from Chrome's cookie store (macOS only), for vendors whose usage only their
own web page reads."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

CHROME = Path.home() / "Library/Application Support/Google/Chrome"


def profiles() -> list[Path]:
    return sorted(p.parent for p in CHROME.glob("*/Cookies"))


def rows(profile: Path, wanted: list[tuple[str, str]]) -> tuple[dict[str, bytes], int] | None:
    """The encrypted value of each (host, name) cookie present, keyed by name, and the store's
    schema version. Read from a copy: Chrome holds the live file locked."""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "Cookies"
        try:
            shutil.copyfile(profile / "Cookies", db)
            con = sqlite3.connect(db)
            got = {}
            for host, name in wanted:
                row = con.execute("select encrypted_value from cookies where host_key = ? and name = ?",
                                  (host, name)).fetchone()
                if row:
                    got[name] = row[0]
            ver = con.execute("select value from meta where key = 'version'").fetchone()
            con.close()
        except (OSError, sqlite3.Error, ValueError, TypeError):
            return None
    try:
        return got, int(ver[0]) if ver else 0
    except (ValueError, TypeError):
        return None


def password() -> str | None:
    # macOS asks the person once; "Always Allow" lets this read it again without asking.
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", "Chrome Safe Storage", "-w"],
                           capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def decrypt(blob: bytes, version: int, password: Callable[[], str | None] = password) -> str | None:
    """Chrome on macOS: `v10` + AES-128-CBC under a key derived from the keychain's
    "Chrome Safe Storage" password. From store version 24 the plaintext opens with SHA-256(host)."""
    pw = password() if blob.startswith(b"v10") else None
    if pw is None:
        return None
    key = hashlib.pbkdf2_hmac("sha1", pw.encode(), b"saltysalt", 1003, 16)
    out = subprocess.run(["openssl", "enc", "-d", "-aes-128-cbc", "-K", key.hex(), "-iv", "20" * 16],
                         input=blob[3:], capture_output=True)
    if out.returncode != 0:
        return None
    plain = out.stdout[32:] if version >= 24 else out.stdout
    try:
        return plain.decode()
    except UnicodeDecodeError:
        return None


class Lazy(Mapping):
    """A secret computed on first use: `discover()` runs on cache hits too, and decrypting asks
    the keychain."""

    def __init__(self, load: Callable[[], dict]):
        self._load, self._got = load, None

    def _secret(self) -> dict:
        if self._got is None:
            self._got = self._load()
        return self._got

    def __getitem__(self, key):
        return self._secret()[key]

    def __iter__(self):
        return iter(self._secret())

    def __len__(self):
        return len(self._secret())
