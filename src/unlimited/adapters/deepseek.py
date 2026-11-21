"""DeepSeek: `/user/balance` on each API key, from $DEEPSEEK_API_KEY and every
`~/.config/claude-ds*.env`. The platform is prepaid and names no usage window, so the balance is
the whole reading, as credits."""

from __future__ import annotations

from datetime import datetime

from ..credential import Credential, EnvKeys
from ..schema import OK, UNREAD, balance, failed, reading

VENDOR = "deepseek"
URL = "https://api.deepseek.com/user/balance"

KEYS = EnvKeys("DEEPSEEK_API_KEY", "claude-ds*.env", named="CLAUDE_DS_ENV")
names, discover = KEYS.names, KEYS.discover


def read(cred: Credential, now: datetime, get) -> dict:
    ans = get(URL, {"Authorization": f"Bearer {cred.secret['key']}"}, now)
    if ans.body is None:
        return failed(VENDOR, cred.account, now, ans)
    spend = balance(ans.body, "total_balance", now)
    if spend is None:
        return reading(VENDOR, cred.account, now, UNREAD, why="no-limits")
    return reading(VENDOR, cred.account, now, OK, credits=spend)
