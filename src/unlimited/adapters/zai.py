"""Z.ai GLM coding plan: `quota/limit` on each API key the claude-glm wrappers read."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from ..credential import Credential
from ..schema import OK, REFUSED, UNREAD, failed, limit, reading

VENDOR = "zai"
URL = "https://api.z.ai/api/monitor/usage/quota/limit"
# Minutes per Z.ai unit: 3 is hours, 6 is the plan's week. An unknown unit keeps no length.
UNIT_MINUTES = {3: 60, 6: 10080}
NAMES = {300: "five_hour", 10080: "seven_day"}


def _env_files() -> list[Path]:
    """Every wrapper's env file (claude-glm, claude-glm-2, ...), and $CLAUDE_GLM_ENV. A GLM
    session exports its own file and key, so neither may narrow discovery to that one account."""
    files = sorted((Path.home() / ".config").glob("claude-glm*.env"))
    named = os.environ.get("CLAUDE_GLM_ENV")
    return files + ([Path(named)] if named else [])


def key_in(path: Path) -> str | None:
    try:
        for line in path.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip().removeprefix("export ").strip() == "GLM_API_KEY":
                return v.strip().strip("'\"") or None
    except OSError:
        pass
    return None


def account_of(key: str) -> str:
    # Z.ai publishes no account id, so a truncated hash of the key stands in.
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def discover() -> list[Credential]:
    keys = [os.environ.get("GLM_API_KEY")] + [key_in(f) for f in _env_files()]
    found = {account_of(k): k for k in keys if k}
    return [Credential(a, {"key": k}) for a, k in sorted(found.items())]


def _millis(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def _num(x: object) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def limits(body: dict, now: datetime) -> list[dict]:
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    out = []
    for l in data.get("limits") or []:
        if not isinstance(l, dict):
            continue
        unit, number = l.get("unit"), l.get("number")
        minutes = (UNIT_MINUTES[unit] * number if unit in UNIT_MINUTES
                   and isinstance(number, int) and not isinstance(number, bool) else None)
        resets = _millis(l.get("nextResetTime"))
        total, spent, pct = l.get("usage"), l.get("currentValue"), l.get("percentage")
        kind = l.get("type") if isinstance(l.get("type"), str) else None
        if resets is None and kind == "TOKENS_LIMIT" and _num(pct) and pct == 0:
            # A token window nothing has opened yet is answered with no reset time.
            used = 0.0
        elif resets is None or resets <= now:
            used = None
        elif _num(total) and _num(spent) and total > 0:
            used = spent / total
        else:
            # Some accounts are answered in TOKENS_LIMIT with only a percentage, whatever the plan.
            used = pct / 100 if _num(pct) and 0 <= pct <= 100 else None
        # Z.ai has no word for a held limit short of the 429 at the cap, so `held` is unknown.
        # Credit and token limits are usage windows; a TIME_LIMIT is the monthly tool quota.
        name = NAMES.get(minutes) if kind in ("CREDIT_LIMIT", "TOKENS_LIMIT") else None
        out.append(limit(name or f"{str(kind).lower()} {unit}x{number}", window_minutes=minutes,
                         used_at_least=used, resets_at=resets, held=None, kind=kind))
    return out


def read(cred: Credential, now: datetime, get) -> dict:
    ans = get(URL, {"Authorization": f"Bearer {cred.secret['key']}"}, now)
    if ans.body is None:
        return failed(VENDOR, cred.account, now, ans)
    if ans.body.get("success") is False:
        code = ans.body.get("code")
        return reading(VENDOR, cred.account, now, REFUSED,
                       why=f"vendor-{code}" if isinstance(code, int) else "vendor-refused")
    found = limits(ans.body, now)
    if not found:
        # An empty answer is unknown, never "nothing used". Z.ai answers success with empty data
        # for a team key sent without its organisation headers.
        return reading(VENDOR, cred.account, now, UNREAD, why="no-limits")
    level = (ans.body.get("data") or {}).get("level") if isinstance(ans.body.get("data"), dict) else None
    return reading(VENDOR, cred.account, now, OK, limits=found, plan=level if isinstance(level, str) else None)
