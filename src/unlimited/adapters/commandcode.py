"""Command Code (commandcode.ai): the billing routes its own CLI reads, on the API key from
$COMMAND_CODE_API_KEY and every `~/.config/commandcode*.env`. Verified live 2026-09-25.

A plan meters dollars in a five-hour and a weekly window that open on first use, and a monthly
allowance anchored to the subscription. Purchased and free credits sit outside all three."""

from __future__ import annotations

from datetime import datetime, timezone

from ..credential import Credential, EnvKeys
from ..schema import OK, UNREAD, credits, failed, limit, moment, number, reading

VENDOR = "commandcode"
BASE = "https://api.commandcode.ai"
CREDITS_URL = BASE + "/alpha/billing/credits"
SUBSCRIPTION_URL = BASE + "/alpha/billing/subscriptions"
WINDOWS = {"fiveHour": ("five_hour", 300), "weekly": ("seven_day", 10080)}
# Each plan's monthly allowance in dollars, as command-code 1.65.2 ships it. The API answers only
# what is left, so the allowance is needed to say how much is used.
ALLOWANCE = {"individual-go": 10, "individual-goat": 70, "individual-pro": 30, "individual-pro-v1": 80,
             "individual-provider": 15, "individual-max": 150, "individual-ultra": 300, "teams-pro": 40}
LIVE = {"active", "trialing", "past_due"}


KEYS = EnvKeys("COMMAND_CODE_API_KEY", "commandcode*.env")
names, discover = KEYS.names, KEYS.discover


def _ms(v: object) -> datetime | None:
    # 0 is a window not yet opened by a first request.
    n = number(v)
    return datetime.fromtimestamp(n / 1000, timezone.utc) if n else None


def _dict(body: dict | None, key: str) -> dict:
    v = body.get(key) if isinstance(body, dict) else None
    return v if isinstance(v, dict) else {}


def windows(body: dict, now: datetime) -> list[dict]:
    wl = _dict(body, "windowLimits")
    if wl.get("limited") is not True:
        return []
    out = []
    for key, (name, minutes) in WINDOWS.items():
        w = _dict(wl, key)
        used, cap = number(w.get("used")), number(w.get("cap"))
        if not cap:
            continue
        resets = _ms(w.get("resetAt"))
        held = w.get("exceeded") is True
        out.append(limit(name, window_minutes=minutes,
                         used_at_least=used / cap if used is not None and (resets is None or resets > now) else None,
                         resets_at=resets, held=held, held_why="exceeded" if held else None))
    return out


def month(credit: dict, sub: dict) -> dict | None:
    """The plan's allowance, from what is left of it. The CLI takes the larger of the plan's figure
    and what is left, since a grant can lift the balance above the plan."""
    left, plan = number(credit.get("monthlyCredits")), sub.get("planId")
    if left is None or sub.get("status") not in LIVE or plan not in ALLOWANCE:
        return None
    allowance = max(ALLOWANCE[plan], left)
    return limit("month", window_minutes=43200, used_at_least=(allowance - left) / allowance,
                 resets_at=moment(sub["currentPeriodEnd"].replace("Z", "+00:00"))
                 if isinstance(sub.get("currentPeriodEnd"), str) else None, held=None)


def _credits(credit: dict, now: datetime) -> dict | None:
    bought, free = number(credit.get("purchasedCredits")), number(credit.get("freeCredits"))
    if bought is None and free is None:
        return None
    balance = (bought or 0) + (free or 0)
    return credits(now, enabled=balance > 0, used=None, limit=None, balance=balance, currency="USD")


def read(cred: Credential, now: datetime, get) -> dict:
    headers = {"Authorization": f"Bearer {cred.secret['key']}"}
    ans = get(CREDITS_URL, headers, now)
    if ans.body is None:
        return failed(VENDOR, cred.account, now, ans)
    credit = _dict(ans.body, "credits")
    # The subscription only adds the month and the plan's name; the windows stand without it.
    sub = _dict(get(SUBSCRIPTION_URL, headers, now).body, "data")
    found = windows(ans.body, now)
    m = month(credit, sub)
    if m:
        found.append(m)
    spend = _credits(credit, now)
    if not found and spend is None:
        return reading(VENDOR, cred.account, now, UNREAD, why="no-limits")
    plan = sub.get("planId") if isinstance(sub.get("planId"), str) else None
    return reading(VENDOR, cred.account, now, OK, limits=found, plan=plan, credits=spend)
