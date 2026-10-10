"""Each key's vendor account id, asked once per key and kept. One file per vendor, under its own
lock: `{key_id: {"account": id}}` once resolved, `{key_id: {"retry_until": iso,
"failures": n}}` while the vendor has not said. A key belongs to one account for its life, so a
mapping is never asked again; deleting the file makes every key resolve anew."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from .cache import MIN_BACKOFF, default_dir
from .schema import iso, moment
from .state import flock, write_json
from .transport import MAX_BACKOFF


def path(vendor: str, directory: Path | None = None) -> Path:
    """`directory` is the cache directory the readings live in."""
    return (directory or default_dir()) / "identities" / f"{vendor}.json"


def load(vendor: str, directory: Path | None = None) -> dict[str, dict]:
    try:
        body = json.loads(path(vendor, directory).read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in body.items() if isinstance(k, str) and isinstance(v, dict)} \
        if isinstance(body, dict) else {}


def _resolved(held: dict[str, dict]) -> dict[str, str]:
    return {k: v["account"] for k, v in held.items() if isinstance(v.get("account"), str) and v["account"]}


def accounts(vendor: str, directory: Path | None = None) -> dict[str, str]:
    """Key id → vendor account id, for every key resolved on this machine. No network."""
    return _resolved(load(vendor, directory))


def vendor_account(adapter, account: str | None, ids: dict[str, str]) -> str | None:
    """The vendor's id for the account unlimited calls `account`, given `accounts()`."""
    if getattr(adapter, "ACCOUNT_IS_VENDOR_ID", False):
        return account
    return ids.get(account) if account else None


def backoff(failures: int) -> timedelta:
    return min(MIN_BACKOFF * 2 ** max(failures - 1, 0), MAX_BACKOFF)


def resolve(adapter, keys: list, now: datetime, get, directory: Path | None = None) -> dict[str, str]:
    """Ask the vendor for each key here with no mapping and no live deadline; return the mapping.
    Holds the identity file's own lock, never the readings'."""
    whoami = getattr(adapter, "whoami", None)
    if whoami is None:
        return {}
    p = path(adapter.VENDOR, directory)
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with flock(p):
        return _resolve(whoami, p, adapter.VENDOR, keys, now, get, directory)


def _resolve(whoami, p: Path, vendor: str, keys: list, now: datetime, get, directory) -> dict[str, str]:
    held = load(vendor, directory)
    changed = False
    for cred in keys:
        kid = cred.account
        entry = held.get(kid) or {}
        if not kid or entry.get("account"):
            continue
        until = moment(entry.get("retry_until"))
        if until is not None and until > now:
            continue
        account, ans = whoami(cred, now, get)
        if isinstance(account, str) and account:
            held[kid] = {"account": account}
        else:
            failures = entry.get("failures") if isinstance(entry.get("failures"), int) else 0
            failures += 1
            due = now + backoff(failures)
            deadline = getattr(ans, "retry_until", None)
            held[kid] = {"retry_until": iso(max(due, deadline) if deadline else due), "failures": failures}
        changed = True
    if changed:
        write_json(held, p)
    return _resolved(held)
