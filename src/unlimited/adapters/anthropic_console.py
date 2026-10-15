"""Anthropic API credits (Claude Console, platform.claude.com): the prepaid balance the Console's own
page reads. No public endpoint answers it, and an individual organisation has no Admin API, so this
reads it on the Console's sign-in cookie, taken from Chrome's cookie store (macOS only).

The account is the Console sign-in's own id, which is the same `accountUuid` a Claude Code sign-in
to that login carries, so both readings share a `vendor_account` and this machine's names for it."""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

from .. import chrome_cookies
from ..credential import Credential
from ..schema import OK, UNREAD, credits, failed, limit, moment, number, reading
from . import anthropic

VENDOR = "anthropic-console"
BASE = "https://platform.claude.com/api"
# The login and its memberships. `/bootstrap` answers the same and more, in some 600 KB, which
# urllib was seen to lose mid-chunk.
ACCOUNT_URL = BASE + "/account"
HOST = ".platform.claude.com"


def _profiles() -> list[Path]:
    return chrome_cookies.profiles()


def _row(profile: Path) -> tuple[bytes, int] | None:
    """The encrypted `sessionKey` and the store's schema version."""
    got = chrome_cookies.rows(profile, [(HOST, "sessionKey")])
    return (got[0]["sessionKey"], got[1]) if got and "sessionKey" in got[0] else None


def _password() -> str | None:
    return chrome_cookies.password()


def _decrypt(blob: bytes, version: int) -> str | None:
    return chrome_cookies.decrypt(blob, version, _password)


def _session(profile: Path) -> chrome_cookies.Lazy:
    def load() -> dict:
        row = _row(profile)
        return {"session": _decrypt(*row) if row else None}
    return chrome_cookies.Lazy(load)


def discover() -> list[Credential]:
    if sys.platform != "darwin":
        return []
    # A profile's sign-in can change login, and an identity is resolved once per key, so the key
    # names the sign-in too: the stored cookie, still encrypted, so no keychain is asked here.
    return [Credential(f"chrome:{p.name}:{hashlib.sha256(row[0]).hexdigest()[:8]}", _session(p))
            for p in _profiles() if (row := _row(p))]


def _headers(cred: Credential) -> dict[str, str]:
    # The Console's edge cuts a non-browser agent's body off mid-chunk, most reads.
    return {"Cookie": f"sessionKey={cred.secret['session']}", "User-Agent": "Mozilla/5.0"}


def whoami(cred: Credential, now: datetime, get):
    if not cred.secret.get("session"):
        return None, None
    ans = get(ACCOUNT_URL, _headers(cred), now)
    who = (ans.body or {}).get("uuid")
    return (who if isinstance(who, str) and who else None), ans


def names() -> dict[str, list[str]]:
    """A profile's cookie is named as the Claude Code sign-in to the same login is."""
    from .. import identities
    cc = anthropic.names()
    return {local: cc[who] for local, who in identities.accounts(VENDOR).items() if who in cc}


def _api_org(body: dict) -> str | None:
    members = body.get("memberships")
    for m in members if isinstance(members, list) else []:
        org = m.get("organization") if isinstance(m, dict) else None
        caps = org.get("capabilities") if isinstance(org, dict) else None
        if isinstance(caps, list) and "api" in caps and isinstance(org.get("uuid"), str) and org["uuid"]:
            return org["uuid"]
    return None


def _end(t: dict) -> datetime | None:
    return moment(t["expires_at"].replace("Z", "+00:00")) if isinstance(t.get("expires_at"), str) else None


def windows(grants: list[dict], now: datetime) -> list[dict]:
    """Each grant still holding credit as a window: granted at its open, lapsing at its reset, used
    as its spent share. That is the shape every other window has, so it is projected like one."""
    out = []
    for t in sorted(grants, key=lambda t: _end(t) or datetime.max.replace(tzinfo=timezone.utc)):
        start, end = (moment(t[k].replace("Z", "+00:00")) if isinstance(t.get(k), str) else None
                      for k in ("granted_at", "expires_at"))
        granted, left = number(t.get("granted_amount_minor_units")), number(t.get("remaining_amount_minor_units"))
        if not (start and end and granted and left is not None and start < end):
            continue
        # One name across grants, so a new grant's window follows the last one's history as a
        # new week follows the last; a second grant held at once is named apart.
        out.append(limit("api_credit" + (f":{len(out) + 1}" if out else ""), window_minutes=round((end - start).total_seconds() / 60),
                         used_at_least=max(granted - left, 0) / granted if end > now else None,
                         resets_at=end, held=None, role="extra", scope="API credit"))
    return out


def read(cred: Credential, now: datetime, get) -> dict:
    if not cred.secret.get("session"):
        return reading(VENDOR, cred.account, now, UNREAD, why="no-credential")
    acct = get(ACCOUNT_URL, _headers(cred), now)
    if acct.body is None:
        return failed(VENDOR, cred.account, now, acct)
    org = _api_org(acct.body)
    if org is None:
        return reading(VENDOR, cred.account, now, UNREAD, why="no-api-organization")
    ans = get(f"{BASE}/organizations/{org}/prepaid/credits", _headers(cred), now)
    if ans.body is None:
        return failed(VENDOR, cred.account, now, ans)
    bal = ans.body.get("balance") if isinstance(ans.body.get("balance"), dict) else {}
    c = bal.get("credits") if isinstance(bal.get("credits"), dict) else {}
    minor, exp = c.get("amount_minor"), c.get("exponent")
    if not all(isinstance(x, int) and not isinstance(x, bool) for x in (minor, exp)) or not 0 <= exp <= 6:
        return reading(VENDOR, cred.account, now, UNREAD, why="no-balance")
    balance = minor / 10 ** exp
    # The earliest expiry among the grants still holding credit, in the vendor's own date.
    lists = [ans.body.get(k) for k in ("promo_tranches", "tranches")]
    grants = [t for l in lists if isinstance(l, list) for t in l
              if isinstance(t, dict) and (number(t.get("remaining_amount_minor_units")) or 0) > 0]
    ends = [m for m in (moment(t["expires_at"].replace("Z", "+00:00")) for t in grants
                        if isinstance(t.get("expires_at"), str)) if m]
    return reading(VENDOR, cred.account, now, OK, limits=windows(grants, now),
                   credits=credits(now, enabled=balance > 0, used=None, limit=None, balance=balance,
                                   currency="USD", expires_at=min(ends) if ends else None))
