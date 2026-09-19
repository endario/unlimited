"""xAI Grok (SuperGrok sign-in via the Grok CLI): the CLI proxy's billing answer on the token the
Grok CLI keeps in `~/.grok/auth.json`. Undocumented; the route the Grok CLI itself uses."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from ..credential import Credential
from ..schema import OK, UNREAD, failed, limit, reading

VENDOR = "xai"
URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"
SETTINGS_URL = "https://cli-chat-proxy.grok.com/v1/settings"
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
            out.append(Credential(str(entry["user_id"]), {"key": entry["key"], "expires": entry.get("expires_at")}))
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


def read(cred: Credential, now: datetime, get) -> dict:
    exp = _iso(cred.secret.get("expires"))
    if exp is not None and exp <= now:
        # The Grok CLI renews its own token on its next run; unlimited never writes auth.json.
        return reading(VENDOR, cred.account, now, UNREAD, why="credential-expired")
    ans = get(URL, {"Authorization": f"Bearer {cred.secret['key']}", "x-xai-token-auth": "xai-grok-cli"}, now)
    if ans.body is None:
        return failed(VENDOR, cred.account, now, ans)
    found = limits(ans.body, now)
    if not found:
        return reading(VENDOR, cred.account, now, UNREAD, why="no-limits")
    headers = {"Authorization": f"Bearer {cred.secret['key']}", "x-xai-token-auth": "xai-grok-cli"}
    tier = (get(SETTINGS_URL, headers, now).body or {}).get("subscription_tier_display")
    return reading(VENDOR, cred.account, now, OK, limits=found, plan=tier if isinstance(tier, str) else None)
