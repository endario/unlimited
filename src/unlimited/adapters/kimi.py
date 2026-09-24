"""Moonshot Kimi Code coding plan: `/usages` on each API key the claude-kimi wrappers read."""

from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..credential import Credential, account_of, dedupe
from ..schema import OK, UNREAD, failed, limit, reading

VENDOR = "kimi"
BASE_URL = "https://api.kimi.com/coding/v1"
URL = f"{BASE_URL}/usages"
FALLBACK_URL = f"{BASE_URL}/usage"
# Minutes per Kimi window unit, keyed by the confirmed real string with its "TIME_UNIT_" prefix
# stripped. Only 5-hour and 7-day windows are confirmed on the Kimi Code coding plan.
UNIT_MINUTES = {"MINUTE": 1, "HOUR": 60, "DAY": 1440}
NAMES = {300: "five_hour", 10080: "seven_day"}
# `usages.limit_5h/limit_7d.used_ratio`: read only for a window `usage`/`limits` didn't already
# cover. Confirmed against the real endpoint (and MoonshotAI/kimi-code#3951) to sit stuck at 0
# while the account's real `usage`/`limits` counts, in the same response, show real activity.
RATIO_WINDOWS = {"limit_5h": 300, "limit_7d": 10080}


def env_files() -> list[Path]:
    """Every wrapper's env file (claude-kimi, claude-kimi-2, ...), and $CLAUDE_KIMI_ENV. A Kimi
    session exports its own file and key, so neither may narrow discovery to that one account."""
    files = sorted((Path.home() / ".config").glob("claude-kimi*.env"))
    named = os.environ.get("CLAUDE_KIMI_ENV")
    return files + ([Path(named)] if named else [])


def key_in(path: Path) -> str | None:
    try:
        for line in path.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip().removeprefix("export ").strip() == "KIMI_API_KEY":
                return v.strip().strip("'\"") or None
    except (OSError, UnicodeDecodeError):
        pass
    return None


def discover() -> list[Credential]:
    keys = [os.environ.get("KIMI_API_KEY")] + [key_in(f) for f in env_files()]
    return dedupe(keys)


def _num(x: object) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _float(x: object) -> float | None:
    # `/usages`' `limit`/`used`/`remaining` come back as strings; its `used_ratio` does not.
    if _num(x):
        v = float(x)
    elif isinstance(x, str):
        try:
            v = float(x)
        except ValueError:
            return None
    else:
        return None
    return v if math.isfinite(v) else None


def _pick(d: dict, *keys: str) -> object:
    """The first of `keys` present with a non-null value — never a truthiness check, so a real
    `0` or `""` is read rather than treated as absent."""
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None


def _reset_at(d: dict, now: datetime) -> datetime | None:
    v = _pick(d, "resetTime", "reset_at", "reset_time")
    if isinstance(v, str):
        try:
            t = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    reset_in = _float(d.get("reset_in"))
    return now + timedelta(seconds=reset_in) if reset_in is not None else None


def _used_ratio(d: dict) -> float | None:
    lim = _float(d.get("limit"))
    used = _float(d.get("used"))
    if used is None:
        remaining = _float(d.get("remaining"))
        used = lim - remaining if remaining is not None and lim is not None else None
    return used / lim if used is not None and lim is not None and lim > 0 else None


def _window_minutes(window: dict) -> int | None:
    duration = _float(window.get("duration"))
    unit = str(_pick(window, "timeUnit", "time_unit") or "").upper().removeprefix("TIME_UNIT_")
    per_minute = UNIT_MINUTES.get(unit)
    if duration is None or per_minute is None:
        return None
    product = duration * per_minute
    return int(product) if math.isfinite(product) else None


def _windowed(body: dict, now: datetime) -> dict[str, dict]:
    """The confirmed real shape: `limits[]`'s own windows, plus the top-level `usage` aggregate
    (the vendor's own Kimi Code CLI labels it "Weekly Usage"; its reset time matches
    `usages.limit_7d`'s) — merged by resolved window name so the same window never appears twice."""
    out: dict[str, dict] = {}
    raw_limits = body.get("limits")
    for item in raw_limits if isinstance(raw_limits, list) else []:
        if not isinstance(item, dict):
            continue
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else item
        window = item.get("window") if isinstance(item.get("window"), dict) else {}
        minutes = _window_minutes(window)
        used, resets = _used_ratio(detail), _reset_at(detail, now)
        if used is None and resets is None:
            continue
        if minutes is None:
            name = f"window_{len(out)}"
        else:
            name = NAMES.get(minutes, f"window_{minutes}m")
        out[name] = limit(name, window_minutes=minutes, used_at_least=used, resets_at=resets, held=None)
    usage = body.get("usage")
    if isinstance(usage, dict) and "seven_day" not in out:
        used, resets = _used_ratio(usage), _reset_at(usage, now)
        if used is not None or resets is not None:
            out["seven_day"] = limit("seven_day", window_minutes=10080, used_at_least=used,
                                     resets_at=resets, held=None)
    return out


def _ratio_limits(usages: dict, now: datetime) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for key, minutes in RATIO_WINDOWS.items():
        w = usages.get(key)
        if not isinstance(w, dict):
            continue
        ratio, resets = _float(w.get("used_ratio")), _reset_at(w, now)
        if ratio is None and resets is None:
            continue
        name = NAMES[minutes]
        out[name] = limit(name, window_minutes=minutes, used_at_least=ratio, resets_at=resets, held=None)
    return out


def limits(body: dict, now: datetime) -> list[dict]:
    found = _windowed(body, now)
    usages = body.get("usages")
    if isinstance(usages, dict):
        # Fills only the windows `limits`/`usage` didn't cover; never overrides a real count with
        # the distrusted ratio (see `RATIO_WINDOWS`).
        for name, l in _ratio_limits(usages, now).items():
            found.setdefault(name, l)
    return list(found.values())


def read(cred: Credential, now: datetime, get) -> dict:
    headers = {"Authorization": f"Bearer {cred.secret['key']}"}
    ans = get(URL, headers, now)
    if ans.status == 404:
        ans = get(FALLBACK_URL, headers, now)
    if ans.body is None:
        return failed(VENDOR, cred.account, now, ans)
    found = limits(ans.body, now)
    if not found:
        return reading(VENDOR, cred.account, now, UNREAD, why="no-limits")
    return reading(VENDOR, cred.account, now, OK, limits=found)
