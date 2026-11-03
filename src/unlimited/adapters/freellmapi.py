"""FreeLLMAPI (github.com/tashfeenahmed/freellmapi): a self-hosted gateway pooling free-tier keys
behind one OpenAI- and Anthropic-compatible `/v1`, routing and failing over among them itself.
Read on its unified key, from $FREELLMAPI_API_KEY and every `~/.config/freellmapi*.env`, at the
`FREELLMAPI_URL` beside it (default the gateway's own `http://localhost:3001`). Verified live
against v0.16.7, 2026-10-23.

The pool publishes no allowance, only each upstream platform's state, so its one limit is a lower
bound: the share of platforms that cannot serve now (rate-limited or invalid, or the share of a
reported request allowance spent), against the UTC day on which free tiers' daily caps reset."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..credential import Credential, EnvKeys
from ..schema import OK, UNREAD, failed, limit, moment, number, reading

VENDOR = "freellmapi"
BASE, DEFAULT_BASE = "FREELLMAPI_URL", "http://localhost:3001"
KEYS = EnvKeys("FREELLMAPI_API_KEY", "freellmapi*.env")
names = KEYS.names
DOWN = {"rate_limited", "invalid"}


def discover() -> list[Credential]:
    return [c if "base" in c.secret else Credential(c.account, {**c.secret, "base": DEFAULT_BASE})
            for c in KEYS.discover_based(BASE)]


def _get(cred: Credential, path: str, now: datetime, get):
    return get(cred.secret["base"] + path, {"Authorization": f"Bearer {cred.secret['key']}"}, now)


def _utc(v: object) -> datetime | None:
    return moment(v.replace("Z", "+00:00")) if isinstance(v, str) else None


def _spent(p: dict) -> float:
    if p.get("status") in DOWN:
        return 1.0
    pct = number(p.get("requests_remaining_pct"))
    return 1 - min(max(pct, 0), 100) / 100 if pct is not None else 0.0


def limits(body: dict, now: datetime) -> list[dict]:
    rows = body.get("providers")
    platforms = [p for p in (rows if isinstance(rows, list) else []) if isinstance(p, dict)]
    if not platforms:
        return []
    down = [p for p in platforms if p.get("status") in DOWN]
    held = len(down) == len(platforms)
    resumes = [t for p in down if (t := _utc(p.get("resume_at"))) and t > now]
    midnight = datetime.combine(now.astimezone(timezone.utc).date() + timedelta(days=1),
                                datetime.min.time(), timezone.utc)
    return [limit("upstreams", window_minutes=1440,
                  used_at_least=sum(_spent(p) for p in platforms) / len(platforms),
                  resets_at=min(resumes) if held and resumes else midnight,
                  held=held, held_why="all-upstreams-unavailable" if held else None)]


def read(cred: Credential, now: datetime, get) -> dict:
    ans = _get(cred, "/v1/providers", now, get)
    if ans.body is None:
        return failed(VENDOR, cred.account, now, ans)
    found = limits(ans.body, now)
    if not found:
        return reading(VENDOR, cred.account, now, UNREAD, why="no-upstreams")
    return reading(VENDOR, cred.account, now, OK, limits=found)


def models(cred: Credential, now: datetime, get) -> dict:
    """The models the pool can serve now, `auto` and `fusion` among them, as a routes reading."""
    ans = _get(cred, "/v1/models?available=true", now, get)
    if ans.body is None:
        return dict(failed(VENDOR, cred.account, now, ans), routes=[])
    rows = ans.body.get("data")
    ids = sorted({m["id"] for m in (rows if isinstance(rows, list) else [])
                  if isinstance(m, dict) and isinstance(m.get("id"), str)})
    return dict(reading(VENDOR, cred.account, now, OK, why=ans.why, retry_until=ans.retry_until),
                routes=[{"id": i, "dispatchable": True} for i in ids])
