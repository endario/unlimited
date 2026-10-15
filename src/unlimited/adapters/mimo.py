"""Xiaomi MiMo (platform.xiaomimimo.com): each team seat's monthly credit allowance and the
pay-as-you-go balance, as the platform's own page reads them. Its API keys are refused on these
routes, so this reads them on the platform's sign-in cookies, taken from Chrome's cookie store
(macOS only).

The account is the login's Xiaomi `userId`, which its own cookie carries."""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from .. import chrome_cookies
from ..credential import Credential
from ..schema import OK, UNREAD, credits, failed, limit, number, reading

VENDOR = "mimo"
BASE = "https://platform.xiaomimimo.com/api/v1"
HOST, ROOT = ".platform.xiaomimimo.com", ".xiaomimimo.com"
COOKIES = [(HOST, "api-platform_serviceToken"), (HOST, "api-platform_ph"), (HOST, "api-platform_slh"),
           (ROOT, "userId")]
# The platform's dates carry no zone; they are Beijing time.
BEIJING = timezone(timedelta(hours=8))
MONTH = 43200


def _cookies(profile: Path) -> dict | None:
    got = chrome_cookies.rows(profile, COOKIES)
    return got if got and "api-platform_serviceToken" in got[0] else None


def _load(profile: Path) -> dict:
    got = _cookies(profile)
    if got is None:
        return {"cookies": None}
    plain = {name: chrome_cookies.decrypt(blob, got[1]) for name, blob in got[0].items()}
    return {"cookies": plain if all(plain.values()) else None}


def discover() -> list[Credential]:
    if sys.platform != "darwin":
        return []
    # The key names the sign-in by its stored token, still encrypted, so no keychain is asked here.
    return [Credential(f"chrome:{p.name}:{hashlib.sha256(got[0]['api-platform_serviceToken']).hexdigest()[:8]}",
                       chrome_cookies.Lazy(lambda p=p: _load(p)))
            for p in chrome_cookies.profiles() if (got := _cookies(p))]


def _headers(cred: Credential) -> dict[str, str]:
    jar = cred.secret["cookies"]
    return {"Cookie": "; ".join(f"{k}={v}" for k, v in jar.items()), "User-Agent": "Mozilla/5.0"}


def whoami(cred: Credential, now: datetime, get):
    """The login's own id is in its cookie: no request."""
    who = (cred.secret.get("cookies") or {}).get("userId")
    return (who if isinstance(who, str) and who else None), None


def _data(ans) -> dict | None:
    """The platform answers `{code, message, data}`; anything but code 0 with a data object is no answer."""
    b = ans.body
    return b["data"] if isinstance(b, dict) and b.get("code") == 0 and isinstance(b.get("data"), dict) else None


def _when(s) -> datetime | None:
    try:
        t = datetime.fromisoformat(s) if isinstance(s, str) else None
    except ValueError:
        return None
    return t if t is None or t.tzinfo else t.replace(tzinfo=BEIJING)


def _seat(data: dict, name: str, scope: str) -> dict | None:
    seat = data.get("seat") if isinstance(data.get("seat"), dict) else {}
    total, used = number(seat.get("creditsTotal")), number(seat.get("creditsUsed"))
    if seat.get("seatStatus") != "ASSIGNED" or not total or used is None:
        return None
    expired = data.get("expired") is True
    return limit(name, window_minutes=MONTH, used_at_least=used / total,
                 resets_at=_when(seat.get("nextResetTime")) or _when(data.get("currentPeriodEnd")),
                 held=expired or None, held_why="expired" if expired else None, scope=scope)


def _amount(x) -> float | None:
    """The balance comes as a decimal string ("0.00")."""
    try:
        return number(float(x)) if isinstance(x, str) else number(x)
    except ValueError:
        return None


def read(cred: Credential, now: datetime, get) -> dict:
    if not cred.secret.get("cookies"):
        return reading(VENDOR, cred.account, now, UNREAD, why="no-credential")
    h = _headers(cred)
    ans = get(BASE + "/projects", h, now)
    if ans.body is None:
        return failed(VENDOR, cred.account, now, ans)
    data = _data(ans)
    if data is None:
        return reading(VENDOR, cred.account, now, UNREAD, why="vendor-error")
    projects = [p for p in data.get("projects") or [] if isinstance(p, dict) and isinstance(p.get("projectId"), str)]
    limits, plans = [], []
    for p in projects:
        ans = get(f"{BASE}/project/{quote(p['projectId'], safe='')}/teamTokenPlan/my/seat", h, now)
        seat = _data(ans) if ans.body is not None else None
        if seat is None:
            continue
        got = _seat(seat, "month" + (f":{len(limits) + 1}" if limits else ""),
                    p.get("projectNameEn") or p.get("projectName") or p["projectId"])
        if got:
            limits.append(got)
            if isinstance(seat.get("planName"), str):
                plans.append(seat["planName"])
    ans = get(BASE + "/balance", h, now)
    bal = _data(ans) if ans.body is not None else None
    amount = _amount(bal.get("balance")) if bal else None
    spend = (credits(now, enabled=amount > 0, used=None, limit=None, balance=amount,
                     currency=bal.get("currency") if isinstance(bal.get("currency"), str) else None)
             if amount is not None else None)
    if not limits and spend is None:
        return reading(VENDOR, cred.account, now, UNREAD, why="no-seat")
    return reading(VENDOR, cred.account, now, OK, limits=limits, plan=plans[0] if plans else None, credits=spend)
