"""Xiaomi MiMo (platform.xiaomimimo.com): each team token plan key's seat allowance, on the platform's Chrome sign-in."""

from __future__ import annotations

import functools
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .. import chrome_cookies
from ..credential import Credential, EnvKeys
from ..schema import OK, UNREAD, failed, limit, number, reading

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


def _jar(p: Path) -> chrome_cookies.Lazy:
    return chrome_cookies.Lazy(lambda: _load(p))


@functools.cache
def _logins() -> tuple[chrome_cookies.Lazy, ...]:
    """Every Chrome profile signed in to the platform; each decrypts on first use, once a process."""
    if sys.platform != "darwin":
        return ()
    return tuple(_jar(p) for p in chrome_cookies.profiles() if _cookies(p))


# Each key is its own account; its usage is read on the console's sign-in, which no key can open.
KEYS = EnvKeys(re.compile(r"MIMO(_\d+)?_API_KEY"), "claude-mimo*.env")
names, discover = KEYS.names, KEYS.discover
# A team token plan key. An individual plan's (`tp-`) or pay-as-you-go (`sk-`) key has no seat.
TEAM = "ttp-"


def _headers(jar) -> dict[str, str]:
    return {"Cookie": "; ".join(f"{k}={v}" for k, v in jar.items()), "User-Agent": "Mozilla/5.0"}


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


def _redacted(red) -> tuple[str, str] | None:
    """The visible head and tail of a redacted key; the run of `*` between them is not one per character."""
    m = re.fullmatch(r"([^*]+)\*+([^*]+)", red) if isinstance(red, str) else None
    return (m[1], m[2]) if m else None


def _fits(key: str, shown: tuple[str, str]) -> bool:
    head, tail = shown
    return len(key) > len(head) + len(tail) and key.startswith(head) and key.endswith(tail)


def _survey(get, now):
    """Every sign-in's projects: (headers, project, its own seat key shown redacted, the user id).
    Returns (projects, the first refusal or None, whether any sign-in decrypted, vendor error)."""
    found, refused, usable, erred = [], None, False, False
    for jar in _logins():
        cookies = jar.get("cookies")
        if not cookies:
            continue
        usable, h = True, _headers(cookies)
        ans = get(BASE + "/projects", h, now)
        if ans.body is None:
            refused = refused or ans
            continue
        data = _data(ans)
        if data is None:
            erred = True
            continue
        for p in data.get("projects") or []:
            if not (isinstance(p, dict) and isinstance(p.get("projectId"), str)):
                continue
            pid = quote(p["projectId"], safe="")
            ans = get(f"{BASE}/project/{pid}/teamTokenPlan/apiKey", h, now)
            got = _data(ans) if ans.body is not None else None
            found.append((h, pid, p, _redacted(got.get("redactedApiKey")) if got else None, cookies.get("userId")))
    return found, refused, usable, erred


def _team(get, h, now, pid: str) -> list[tuple[dict, dict]]:
    """An owner's or admin's view of the plan and every member, as (seat reading, member)."""
    ans = get(f"{BASE}/project/{pid}/teamTokenPlan/detail", h, now)
    plan = _data(ans) if ans.body is not None else None
    if not plan:
        return []
    ans = get(f"{BASE}/project/{pid}/members", h, now)
    members = ans.body.get("data") if isinstance(ans.body, dict) and ans.body.get("code") == 0 else None
    return [({**plan, "seat": m["seat"]}, m) for m in members if isinstance(m, dict) and isinstance(m.get("seat"), dict)
            and m["seat"].get("seatStatus") == "ASSIGNED"] if isinstance(members, list) else []


def _find(key: str, get, now):
    """The seat `key` belongs to, as (seat reading, scope) or (None, why, refusal)."""
    if not key.startswith(TEAM):
        return None, "not-team-key", None
    if not _logins():
        return None, "no-sign-in", None
    projects, refused, usable, erred = _survey(get, now)
    if not usable:
        return None, "no-credential", None
    for h, pid, p, shown, _ in projects:
        if shown and _fits(key, shown):
            ans = get(f"{BASE}/project/{pid}/teamTokenPlan/my/seat", h, now)
            mine = _data(ans) if ans.body is not None else None
            return (mine, p.get("projectNameEn") or p["projectId"], None) if mine else (None, "no-seat", ans)
    # Elimination, and nothing beyond it: a key no sign-in's own seat shows is the one seat no
    # sign-in holds, only while it is the sole such key and an owner's view shows exactly one such seat.
    shown = [s for *_, s, _ in projects if s]
    loose = [k for c in discover() if (k := c.secret["key"]).startswith(TEAM) and not any(_fits(k, s) for s in shown)]
    signed = {u for *_, u in projects if u}
    free = [(seat, m) for h, pid, p, _, _ in projects if p.get("role") in ("OWNER", "ADMIN")
            for seat, m in _team(get, h, now, pid) if str(m.get("userId")) not in signed] if loose == [key] else []
    if len(free) == 1:
        seat, m = free[0]
        return seat, m.get("displayName") if isinstance(m.get("displayName"), str) else None, None
    if refused:
        return None, None, refused
    return None, "vendor-error" if erred and not projects else "no-seat-for-key", None


def whoami(cred: Credential, now: datetime, get):
    seat, _, ans = _find(cred.secret["key"], get, now)
    who = (seat.get("seat") or {}).get("seatId") if seat else None
    return (who if isinstance(who, str) and who else None), ans


def read(cred: Credential, now: datetime, get) -> dict:
    seat, scope, ans = _find(cred.secret["key"], get, now)
    if seat is None:
        return failed(VENDOR, cred.account, now, ans) if scope is None else reading(VENDOR, cred.account, now, UNREAD, why=scope)
    got = _seat(seat, "month", scope)
    if got is None:
        return reading(VENDOR, cred.account, now, UNREAD, why="no-seat")
    plan = seat.get("planName") if isinstance(seat.get("planName"), str) else None
    return reading(VENDOR, cred.account, now, OK, limits=[got], plan=plan)
