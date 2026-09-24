"""OpenCode Go (opencode.ai): `zen/go/v1/usage` on the Go API key opencode keeps.

Go meters dollars of token cost in three windows: a rolling five hours, a UTC calendar week and a
month anchored to the subscription. The endpoint answers each as a whole percent, a reset and a
status. Verified live 2026-09-19."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from ..credential import Credential, account_of, dedupe
from ..schema import OK, UNREAD, failed, limit, reading

VENDOR = "opencode"
URL = "https://opencode.ai/zen/go/v1/usage"
# The month is anchored to the subscription day, so its length varies; 30 days names it.
WINDOWS = {"rolling": ("five_hour", 300), "weekly": ("seven_day", 10080), "monthly": ("month", 43200)}
# A second (or Nth) Go key that never went through `opencode auth login` on this machine — e.g.
# one a launcher hands a worker by environment — names itself OPENCODE_2_API_KEY, OPENCODE_3_...
ENV_KEY = re.compile(r"^OPENCODE(?:_\d+)?_API_KEY$")
# The exact shape the provisioning wizard creates: a numbered slot, never an open-ended prefix
# match, so a renamed or unrelated ".opencode-backup" is never mistaken for a live identity.
_SLOT = re.compile(r"^\.opencode-(\d+)$")


def _auth_file(data_home: Path) -> Path:
    return data_home / "opencode" / "auth.json"


def data_homes() -> list[Path]:
    """The default XDG_DATA_HOME, plus every isolated one (`~/.opencode-2`, `~/.opencode-3`, ...),
    in slot order. auth.json holds one key per provider name, so a second concurrent Go
    subscription needs its own data directory, the same isolation this machine already uses for a
    second Claude or GLM account; none may narrow discovery to just the default."""
    default = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    slots = []
    for p in Path.home().glob(".opencode-*"):
        m = _SLOT.match(p.name)
        # A slot pointed at directly as XDG_DATA_HOME is already `default`; never list it twice.
        if m and p.is_dir() and p.resolve() != default.resolve():
            slots.append((int(m.group(1)), p))
    return [default] + [p for _, p in sorted(slots)]


def key_in(data_home: Path) -> str | None:
    try:
        got = json.loads(_auth_file(data_home).read_text())
    except (OSError, ValueError):
        return None
    for provider in ("opencode-go", "opencode"):
        entry = got.get(provider) if isinstance(got, dict) else None
        if isinstance(entry, dict) and entry.get("type") == "api" and isinstance(entry.get("key"), str):
            return entry["key"]
    return None


def discover() -> list[Credential]:
    keys = [key_in(d) for d in data_homes()] + [v for k, v in os.environ.items() if ENV_KEY.match(k)]
    return dedupe(keys)


def _iso(v: object) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else None


def limits(body: dict, now: datetime) -> list[dict]:
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    out = []
    for key, (name, minutes) in WINDOWS.items():
        w = usage.get(key)
        if not isinstance(w, dict):
            continue
        resets = _iso(w.get("resetsAt"))
        pct = w.get("percent")
        limited = w.get("status") == "rate-limited"
        out.append(limit(name, window_minutes=minutes,
                         used_at_least=pct / 100 if isinstance(pct, (int, float)) and not isinstance(pct, bool)
                         and (resets is None or resets > now) else None,
                         resets_at=resets, held=limited, held_why="rate-limited" if limited else None))
    return out


def read(cred: Credential, now: datetime, get) -> dict:
    ans = get(URL, {"Authorization": f"Bearer {cred.secret['key']}"}, now)
    if ans.body is None:
        if ans.status == 403:
            # A key whose account holds no Go subscription: nothing to read, not a refusal.
            return reading(VENDOR, cred.account, now, UNREAD, why="no-subscription")
        return failed(VENDOR, cred.account, now, ans)
    found = limits(ans.body, now)
    if not found:
        return reading(VENDOR, cred.account, now, UNREAD, why="no-limits")
    return reading(VENDOR, cred.account, now, OK, limits=found)
