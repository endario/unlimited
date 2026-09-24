from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Credential:
    account: str | None
    # Secret material. `repr=False` keeps it out of any traceback or debug print.
    secret: dict = field(repr=False)


def account_of(key: str) -> str:
    # A key names no account on its own, so a truncated hash of it stands in.
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def dedupe(keys: list[str | None]) -> list[Credential]:
    """One `Credential` per distinct key, sorted by account id; several sources naming the same key
    collapse to one account."""
    found = {account_of(k): k for k in keys if k}
    return [Credential(a, {"key": k}) for a, k in sorted(found.items())]
