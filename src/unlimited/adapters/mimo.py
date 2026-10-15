"""Xiaomi MiMo (platform.xiaomimimo.com): team seat credit allowances and the balance, on the platform's Chrome sign-in."""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
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
# The platform's dates carry no zone; its own console labels a period end as UTC.

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
    who = (cred.secret.get("cookies") or {}).get("userId")
    return (who if isinstance(who, str) and who else None), None


def _data(ans) -> dict | None:
    b = ans.body
    return b["data"] if isinstance(b, dict) and b.get("code") == 0 and isinstance(b.get("data"), dict) else None


def _when(s) -> datetime | None:
    try:
        t = datetime.fromisoformat(s) if isinstance(s, str) else None
    except ValueError:
        return None
    return t if t is None or t.tzinfo else t.replace(tzinfo=timezone.utc)


def _seat(data: dict, name: str, scope: str) -> dict | None:
    seat = data.get("seat") if isinstance(data.get("seat"), dict) else {}
    total, used = number(seat.get("creditsTotal")), number(seat.get("creditsUsed"))
    if seat.get("seatStatus") != "ASSIGNED" or not total or used is None:
        return None
    expired = data.get("expired") is True
    return limit(name, window_minutes=MONTH, used_at_least=used / total,
                 resets_at=_when(seat.get("nextResetTime")) or _when(data.get("currentPeriodEnd")),
                 held=expired or None, held_why="expired" if expired else None, scope=scope)


def _seats(get, h, now, pid: str, role) -> list[tuple[dict, str | None]]:
    """An owner or admin sees every member's seat; anyone else only their own."""
    if role in ("OWNER", "ADMIN"):
        ans = get(f"{BASE}/project/{pid}/teamTokenPlan/detail", h, now)
        plan = _data(ans) if ans.body is not None else None
        ans = get(f"{BASE}/project/{pid}/members", h, now)
        members = ans.body.get("data") if plan and isinstance(ans.body, dict) and ans.body.get("code") == 0 else None
        if isinstance(members, list):
            return [({**plan, "seat": m["seat"]}, m.get("displayName") if isinstance(m.get("displayName"), str) else None)
                    for m in members if isinstance(m, dict) and isinstance(m.get("seat"), dict)]
    ans = get(f"{BASE}/project/{pid}/teamTokenPlan/my/seat", h, now)
    mine = _data(ans) if ans.body is not None else None
    return [(mine, None)] if mine else []


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
        pid, name = quote(p['projectId'], safe=''), p.get("projectNameEn") or p.get("projectName") or p["projectId"]
        for seat, who in _seats(get, h, now, pid, p.get("role")):
            got = _seat(seat, "month" + (f":{len(limits) + 1}" if limits else ""), who or name)
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
