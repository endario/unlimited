"""Neuralwatt Cloud: `/v1/quota` on each API key, from $NEURALWATT_API_KEY and every
`~/.config/neuralwatt*.env`. The subscription's monthly energy allowance is a limit; a key's
spending allowance is another; the prepaid balance is credits."""

from __future__ import annotations

import math
import os
from datetime import datetime
from pathlib import Path

from ..credential import Credential, account_of, dedupe
from ..schema import OK, UNREAD, credits, failed, limit, moment, reading

VENDOR = "neuralwatt"
URL = "https://api.neuralwatt.com/v1/quota"
PERIOD_MINUTES = {"daily": 1440, "weekly": 10080, "monthly": 43200}


def env_files() -> list[Path]:
    return sorted((Path.home() / ".config").glob("neuralwatt*.env"))


def key_in(path: Path) -> str | None:
    try:
        for line in path.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip().removeprefix("export ").strip() == "NEURALWATT_API_KEY":
                return v.strip().strip("'\"") or None
    except (OSError, UnicodeDecodeError):
        pass
    return None


def names() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for f in env_files():
        key = key_in(f)
        if key and f.stem not in out.setdefault(account_of(key), []):
            out[account_of(key)].append(f.stem)
    return out


def discover() -> list[Credential]:
    return dedupe([os.environ.get("NEURALWATT_API_KEY")] + [key_in(f) for f in env_files()])


def _num(x: object) -> float | None:
    if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x):
        return float(x)
    return None


def _utc(v: object) -> datetime | None:
    return moment(v.replace("Z", "+00:00")) if isinstance(v, str) else None


def limits(body: dict) -> list[dict]:
    out = []
    sub = body.get("subscription") if isinstance(body.get("subscription"), dict) else {}
    included, used = _num(sub.get("kwh_included")), _num(sub.get("kwh_used"))
    if included:
        annual = sub.get("billing_interval") == "year"
        out.append(limit("month", window_minutes=43200,
                         used_at_least=used / included if used is not None else None,
                         resets_at=None if annual else _utc(sub.get("current_period_end")),
                         held=sub.get("in_overage") is True,
                         held_why="overage" if sub.get("in_overage") is True else None))
    key = body.get("key") if isinstance(body.get("key"), dict) else {}
    allow = key.get("allowance") if isinstance(key.get("allowance"), dict) else {}
    cap, spent = _num(allow.get("limit_usd")), _num(allow.get("spent_usd"))
    if cap:
        blocked = allow.get("blocked") is True
        out.append(limit("key allowance", window_minutes=PERIOD_MINUTES.get(allow.get("period")),
                         used_at_least=spent / cap if spent is not None else None, resets_at=None,
                         held=blocked, held_why="blocked" if blocked else None, role="extra", scope="key"))
    return out


def _credits(body: dict, now: datetime) -> dict | None:
    bal = body.get("balance") if isinstance(body.get("balance"), dict) else {}
    remaining = _num(bal.get("credits_remaining_usd"))
    if remaining is None:
        return None
    return credits(_utc(body.get("snapshot_at")) or now, enabled=remaining > 0,
                   used=_num(bal.get("credits_used_usd")), limit=_num(bal.get("total_credits_usd")),
                   balance=remaining, currency="USD")


def read(cred: Credential, now: datetime, get) -> dict:
    ans = get(URL, {"Authorization": f"Bearer {cred.secret['key']}"}, now)
    if ans.body is None:
        return failed(VENDOR, cred.account, now, ans)
    sub = ans.body.get("subscription") if isinstance(ans.body.get("subscription"), dict) else {}
    plan = sub.get("plan") if isinstance(sub.get("plan"), str) else None
    found, spend = limits(ans.body), _credits(ans.body, now)
    if not found and spend is None:
        return reading(VENDOR, cred.account, now, UNREAD, why="no-limits")
    return reading(VENDOR, cred.account, now, OK, limits=found, plan=plan, credits=spend)
