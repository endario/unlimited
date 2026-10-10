"""Each key's vendor account id, asked once per key and kept. One file per vendor, written only under
that vendor's cache lock: `{key_id: {"account": id}}` once resolved, `{key_id: {"retry_until": iso,
"failures": n}}` while the vendor has not said. A key belongs to one account for its life, so a
mapping is never asked again; deleting the file makes every key resolve anew."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from .cache import MIN_BACKOFF, default_dir
from .schema import iso, moment
from .state import write_json
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


def accounts(vendor: str, directory: Path | None = None) -> dict[str, str]:
    """Key id → vendor account id, for every key resolved on this machine. No network."""
    return {k: v["account"] for k, v in load(vendor, directory).items()
            if isinstance(v.get("account"), str) and v["account"]}


def backoff(failures: int) -> timedelta:
    return min(MIN_BACKOFF * 2 ** max(failures - 1, 0), MAX_BACKOFF)


def resolve(adapter, keys: list, now: datetime, get, directory: Path | None = None) -> dict[str, str]:
    """Ask the vendor for each key here with no mapping and no live deadline; return the mapping.
    The caller holds the vendor's cache lock."""
    held = load(adapter.VENDOR, directory)
    whoami = getattr(adapter, "whoami", None)
    changed = False
    for cred in keys if whoami else []:
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
            vendor = getattr(ans, "retry_until", None)
            held[kid] = {"retry_until": iso(max(due, vendor) if vendor else due), "failures": failures}
        changed = True
    if changed:
        p = path(adapter.VENDOR, directory)
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_json(held, p)
    return {k: v["account"] for k, v in held.items() if isinstance(v.get("account"), str) and v["account"]}
