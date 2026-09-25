"""xAI Grok (SuperGrok sign-in via the Grok CLI): the CLI proxy's billing answer on the token the
Grok CLI keeps in `~/.grok/auth.json`. Undocumented; the route the Grok CLI itself uses."""

from __future__ import annotations

import json
import os
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

from ..credential import Credential
from ..schema import OK, UNREAD, failed, limit, reading

VENDOR = "xai"
URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"
SETTINGS_URL = "https://cli-chat-proxy.grok.com/v1/settings"
TOKEN_URL = "https://auth.x.ai/oauth2/token"
PERIODS = {"USAGE_PERIOD_TYPE_WEEKLY": ("seven_day", 10080), "USAGE_PERIOD_TYPE_MONTHLY": ("month", 43200)}


def _auth_file() -> Path:
    return Path(os.environ.get("GROK_HOME") or Path.home() / ".grok") / "auth.json"


def _iso(v: object) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else None


def discover() -> list[Credential]:
    try:
        got = json.loads(_auth_file().read_text())
    except (OSError, ValueError):
        return []
    out = []
    for entry in got.values() if isinstance(got, dict) else []:
        if isinstance(entry, dict) and isinstance(entry.get("key"), str) and entry.get("user_id"):
            out.append(Credential(str(entry["user_id"]), {
                "key": entry["key"], "expires": entry.get("expires_at"),
                "refresh": entry.get("refresh_token"), "client": entry.get("oidc_client_id")}))
    return out


def limits(body: dict, now: datetime) -> list[dict]:
    cfg = body.get("config") if isinstance(body.get("config"), dict) else {}
    period = cfg.get("currentPeriod") if isinstance(cfg.get("currentPeriod"), dict) else {}
    start = _iso(period.get("start") or cfg.get("billingPeriodStart"))
    end = _iso(period.get("end") or cfg.get("billingPeriodEnd"))
    if end is None:
        return []
    kind = period.get("type")
    name, minutes = PERIODS.get(kind, (None, None)) if isinstance(kind, str) else (None, None)
    if minutes is None and start is not None:
        minutes = int((end - start).total_seconds() // 60)
    pct = cfg.get("creditUsagePercent")
    used = pct / 100 if isinstance(pct, (int, float)) and not isinstance(pct, bool) and end > now else None
    return [limit(name or "period", window_minutes=minutes, used_at_least=used, resets_at=end, held=None)]


def _token_file() -> Path:
    from ..cache import default_dir
    return default_dir() / "credentials" / "xai.json"


def _held() -> dict:
    try:
        got = json.loads(_token_file().read_text())
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _hold(account: str, key: str, expires: datetime) -> None:
    held = _held()
    held[account] = {"key": key, "expires": expires.isoformat()}
    path = _token_file()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(held, f)
    os.replace(tmp, path)


def _live(entry: object, now: datetime) -> str | None:
    if not isinstance(entry, dict) or not isinstance(entry.get("key"), str):
        return None
    exp = _iso(entry.get("expires"))
    return entry["key"] if exp is None or exp > now + timedelta(minutes=1) else None


def _key(cred: Credential, now: datetime, get) -> str | dict:
    """A live access token, or the reading that says why there is none. A renewed token is held in
    unlimited's own store; auth.json is the Grok CLI's and is never written."""
    key = _live(cred.secret, now) or _live(_held().get(cred.account), now)
    if key:
        return key
    if not cred.secret.get("refresh") or not cred.secret.get("client"):
        return reading(VENDOR, cred.account, now, UNREAD, why="credential-expired")
    form = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": cred.secret["refresh"],
                                   "client_id": cred.secret["client"]}).encode()
    ans = get(TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, now, data=form)
    if ans.status in (400, 401):
        # The refresh token itself is refused: only `grok login` recovers.
        return reading(VENDOR, cred.account, now, UNREAD, why="credential-expired")
    body = ans.body or {}
    if not isinstance(body.get("access_token"), str):
        return failed(VENDOR, cred.account, now, ans)
    life = body.get("expires_in")
    life = life if isinstance(life, int) and not isinstance(life, bool) and life > 0 else 3600
    _hold(cred.account, body["access_token"], now + timedelta(seconds=life))
    return body["access_token"]


def read(cred: Credential, now: datetime, get) -> dict:
    key = _key(cred, now, get)
    if isinstance(key, dict):
        return key
    headers = {"Authorization": f"Bearer {key}", "x-xai-token-auth": "xai-grok-cli"}
    ans = get(URL, headers, now)
    if ans.body is None:
        return failed(VENDOR, cred.account, now, ans)
    found = limits(ans.body, now)
    if not found:
        return reading(VENDOR, cred.account, now, UNREAD, why="no-limits")
    tier = (get(SETTINGS_URL, headers, now).body or {}).get("subscription_tier_display")
    return reading(VENDOR, cred.account, now, OK, limits=found, plan=tier if isinstance(tier, str) else None)
