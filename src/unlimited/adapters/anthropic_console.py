"""Anthropic API credits (Claude Console, platform.claude.com): the prepaid balance the Console's own
page reads. No public endpoint answers it, and an individual organisation has no Admin API, so this
reads it on the Console's sign-in cookie, taken from Chrome's cookie store (macOS only).

The account is the Console sign-in's own id, which is the same `accountUuid` a Claude Code sign-in
to that login carries, so both readings share a `vendor_account` and this machine's names for it."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from ..credential import Credential
from ..schema import OK, UNREAD, credits, failed, moment, number, reading
from . import anthropic

VENDOR = "anthropic-console"
BASE = "https://platform.claude.com/api"
HOST = ".platform.claude.com"
CHROME = Path.home() / "Library/Application Support/Google/Chrome"


def _profiles() -> list[Path]:
    return sorted(p.parent for p in CHROME.glob("*/Cookies"))


def _row(profile: Path) -> tuple[bytes, int] | None:
    """The encrypted `sessionKey` and the store's schema version. Read from a copy: Chrome holds
    the live file locked."""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "Cookies"
        try:
            shutil.copyfile(profile / "Cookies", db)
            con = sqlite3.connect(db)
            row = con.execute("select encrypted_value from cookies where host_key = ? and name = 'sessionKey'",
                              (HOST,)).fetchone()
            ver = con.execute("select value from meta where key = 'version'").fetchone()
            con.close()
        except (OSError, sqlite3.Error, ValueError, TypeError):
            return None
    try:
        return (row[0], int(ver[0]) if ver else 0) if row else None
    except (ValueError, TypeError):
        return None


def _password() -> str | None:
    # macOS asks the person once; "Always Allow" lets this read it again without asking.
    r = subprocess.run(["security", "find-generic-password", "-s", "Chrome Safe Storage", "-w"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def _decrypt(blob: bytes, version: int) -> str | None:
    """Chrome on macOS: `v10` + AES-128-CBC under a key derived from the keychain's
    "Chrome Safe Storage" password. From store version 24 the plaintext opens with SHA-256(host)."""
    password = _password() if blob.startswith(b"v10") else None
    if password is None:
        return None
    key = hashlib.pbkdf2_hmac("sha1", password.encode(), b"saltysalt", 1003, 16)
    out = subprocess.run(["openssl", "enc", "-d", "-aes-128-cbc", "-K", key.hex(), "-iv", "20" * 16],
                         input=blob[3:], capture_output=True)
    if out.returncode != 0:
        return None
    plain = out.stdout[32:] if version >= 24 else out.stdout
    try:
        return plain.decode()
    except UnicodeDecodeError:
        return None


class _Session(Mapping):
    """The cookie, decrypted on first use: `discover()` runs on cache hits too, and decrypting
    asks the keychain."""

    def __init__(self, profile: Path):
        self._profile, self._got = profile, None

    def _secret(self) -> dict:
        if self._got is None:
            row = _row(self._profile)
            self._got = {"session": _decrypt(*row) if row else None}
        return self._got

    def __getitem__(self, key):
        return self._secret()[key]

    def __iter__(self):
        return iter(self._secret())

    def __len__(self):
        return len(self._secret())


def discover() -> list[Credential]:
    if sys.platform != "darwin":
        return []
    return [Credential(f"chrome:{p.name}", _Session(p)) for p in _profiles() if _row(p)]


def _headers(cred: Credential) -> dict[str, str]:
    return {"Cookie": f"sessionKey={cred.secret['session']}"}


def whoami(cred: Credential, now: datetime, get):
    if not cred.secret.get("session"):
        return None, None
    ans = get(BASE + "/bootstrap", _headers(cred), now)
    acct = (ans.body or {}).get("account") if isinstance((ans.body or {}).get("account"), dict) else {}
    who = acct.get("uuid")
    return (who if isinstance(who, str) and who else None), ans


def names() -> dict[str, list[str]]:
    """A profile's cookie is named as the Claude Code sign-in to the same login is."""
    from .. import identities
    cc = anthropic.names()
    return {local: cc[who] for local, who in identities.accounts(VENDOR).items() if who in cc}


def _api_org(body: dict) -> str | None:
    acct = body.get("account") if isinstance(body.get("account"), dict) else {}
    for m in acct.get("memberships") or []:
        org = m.get("organization") if isinstance(m, dict) else None
        caps = org.get("capabilities") if isinstance(org, dict) else None
        if isinstance(caps, list) and "api" in caps and isinstance(org.get("uuid"), str) and org["uuid"]:
            return org["uuid"]
    return None


def read(cred: Credential, now: datetime, get) -> dict:
    if not cred.secret.get("session"):
        return reading(VENDOR, cred.account, now, UNREAD, why="no-credential")
    boot = get(BASE + "/bootstrap", _headers(cred), now)
    if boot.body is None:
        return failed(VENDOR, cred.account, now, boot)
    org = _api_org(boot.body)
    if org is None:
        return reading(VENDOR, cred.account, now, UNREAD, why="no-api-organization")
    ans = get(f"{BASE}/organizations/{org}/prepaid/credits", _headers(cred), now)
    if ans.body is None:
        return failed(VENDOR, cred.account, now, ans)
    bal = ans.body.get("balance") if isinstance(ans.body.get("balance"), dict) else {}
    c = bal.get("credits") if isinstance(bal.get("credits"), dict) else {}
    minor, exp = c.get("amount_minor"), c.get("exponent")
    if not all(isinstance(x, int) and not isinstance(x, bool) for x in (minor, exp)) or not 0 <= exp <= 6:
        return reading(VENDOR, cred.account, now, UNREAD, why="no-balance")
    balance = minor / 10 ** exp
    # The earliest expiry among the grants still holding credit, in the vendor's own date.
    lists = [ans.body.get(k) for k in ("promo_tranches", "tranches")]
    grants = [t for l in lists if isinstance(l, list) for t in l
              if isinstance(t, dict) and (number(t.get("remaining_amount_minor_units")) or 0) > 0]
    ends = [m for m in (moment(t["expires_at"].replace("Z", "+00:00")) for t in grants
                        if isinstance(t.get("expires_at"), str)) if m]
    return reading(VENDOR, cred.account, now, OK,
                   credits=credits(now, enabled=balance > 0, used=None, limit=None, balance=balance,
                                   currency="USD", expires_at=min(ends) if ends else None))
