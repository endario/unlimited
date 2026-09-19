"""OpenCode Go (opencode.ai): `zen/go/v1/usage` on the Go API key opencode keeps.

Go meters dollars of token cost in three windows: a rolling five hours, a UTC calendar week and a
month anchored to the subscription. The endpoint answers each as a whole percent, a reset and a
status. Verified live 2026-09-19."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from ..credential import Credential
from ..schema import OK, UNREAD, failed, limit, reading

VENDOR = "opencode"
URL = "https://opencode.ai/zen/go/v1/usage"
# The month is anchored to the subscription day, so its length varies; 30 days names it.
WINDOWS = {"rolling": ("five_hour", 300), "weekly": ("seven_day", 10080), "monthly": ("month", 43200)}


def _auth_file() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "opencode" / "auth.json"


def discover() -> list[Credential]:
    key = None
    try:
        got = json.loads(_auth_file().read_text())
    except (OSError, ValueError):
        got = {}
    for provider in ("opencode-go", "opencode"):
        entry = got.get(provider) if isinstance(got, dict) else None
        if isinstance(entry, dict) and entry.get("type") == "api" and isinstance(entry.get("key"), str):
            key = entry["key"]
            break
    key = key or os.environ.get("OPENCODE_API_KEY")
    if not key:
        return []
    # The key names no account, so a truncated hash of it stands in.
    return [Credential(hashlib.sha256(key.encode()).hexdigest()[:16], {"key": key})]


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
