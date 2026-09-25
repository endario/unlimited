from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Credential:
    account: str | None
    # Secret material. `repr=False` keeps it out of any traceback or debug print.
    secret: Mapping = field(repr=False)


def account_of(key: str) -> str:
    # A key names no account on its own, so a truncated hash of it stands in.
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def dedupe(keys: list[str | None]) -> list[Credential]:
    """One `Credential` per distinct key, sorted by account id; several sources naming the same key
    collapse to one account."""
    found = {account_of(k): k for k in keys if k}
    return [Credential(a, {"key": k}) for a, k in sorted(found.items())]


def env_value(path: Path, var: str) -> str | None:
    """`var` in `path`, parsed shell-like: the last assignment wins, a quoted value is taken whole,
    and an unquoted one ends at a comment. None when unset, empty or unreadable."""
    found = None
    try:
        lines = path.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    for line in lines:
        k, eq, v = line.partition("=")
        if not eq or k.strip().removeprefix("export ").strip() != var:
            continue
        v = v.strip()
        if v[:1] in ("'", '"') and v[0] in v[1:]:
            v = v[1:v.index(v[0], 1)]
        else:
            v = re.split(r"\s#", v, maxsplit=1)[0].strip()
        found = v or None
    return found


@dataclass(frozen=True)
class EnvKeys:
    """API keys kept as `var` in `~/.config/<glob>` env files, in the environment, and in the file a
    wrapper session names in `named` (its own, exported alongside the key)."""
    var: str
    glob: str
    named: str | None = None

    def files(self) -> list[Path]:
        files = sorted((Path.home() / ".config").glob(self.glob))
        path = os.environ.get(self.named) if self.named else None
        return files + ([Path(path)] if path else [])

    def names(self) -> dict[str, list[str]]:
        """Account id → the files holding its key, by stem (a wrapper's command name)."""
        out: dict[str, list[str]] = {}
        for f in self.files():
            key = env_value(f, self.var)
            if key and f.stem not in out.setdefault(account_of(key), []):
                out[account_of(key)].append(f.stem)
        return out

    def discover(self) -> list[Credential]:
        return dedupe([os.environ.get(self.var)] + [env_value(f, self.var) for f in self.files()])
