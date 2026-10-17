"""DeepSeek: `/user/balance` on each API key, from $DEEPSEEK_API_KEY and every
`~/.config/claude-ds*.env`. The platform is prepaid and names no usage window, so the balance is
the whole reading, as credits."""

from __future__ import annotations

from datetime import datetime

from ..credential import Credential, EnvKeys
from ..schema import OK, UNREAD, credits, failed, number, reading

VENDOR = "deepseek"
URL = "https://api.deepseek.com/user/balance"

KEYS = EnvKeys("DEEPSEEK_API_KEY", "claude-ds*.env", named="CLAUDE_DS_ENV")
names, discover = KEYS.names, KEYS.discover


def _amount(v: object) -> float | None:
    # The platform sends amounts as decimal strings.
    try:
        return number(float(v)) if isinstance(v, str) else number(v)
    except ValueError:
        return None


def _credits(body: dict, now: datetime) -> dict | None:
    infos = body.get("balance_infos")
    info = next((i for i in infos if isinstance(i, dict)), None) if isinstance(infos, list) else None
    total = _amount(info.get("total_balance")) if info else None
    if total is None:
        return None
    currency = info.get("currency")
    return credits(now, enabled=body.get("is_available") is True, used=None, limit=None, balance=total,
                   currency=currency if isinstance(currency, str) else None)


def read(cred: Credential, now: datetime, get) -> dict:
    ans = get(URL, {"Authorization": f"Bearer {cred.secret['key']}"}, now)
    if ans.body is None:
        return failed(VENDOR, cred.account, now, ans)
    spend = _credits(ans.body, now)
    if spend is None:
        return reading(VENDOR, cred.account, now, UNREAD, why="no-limits")
    return reading(VENDOR, cred.account, now, OK, credits=spend)
