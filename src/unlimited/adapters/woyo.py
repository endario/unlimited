"""Woyo gateway logs: a stateless, allowlisted source of measured token observations."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone

from .. import usage
from ..credential import Credential, EnvKeys
from ..schema import OK, UNREAD, failed, reading

VENDOR = "woyo"
URL = "http://aicoding.woyoalliance.com:90/api/log/token"
KEYS = EnvKeys("FY_API_KEY", "*.env")
names, discover = KEYS.names, KEYS.discover
attach = usage.attach


def _id(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return hashlib.sha256(value.encode()).hexdigest()
    except UnicodeEncodeError:
        return None


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _at(value: object) -> str | None:
    seconds = _integer(value)
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _uncertainty(at: str | None) -> dict:
    if at is None:
        return {}
    try:
        return {"starts_at": at, "ends_at": (datetime.fromisoformat(at) + timedelta(seconds=1)).isoformat()}
    except (OverflowError, ValueError):
        return {}


def _metadata(value: object) -> dict | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _snapshot(rows: list, now: datetime) -> dict:
    anchors: list[str] = []
    candidates: list[tuple[str, dict]] = []
    consumption = Counter()
    unparsed: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            unparsed.append({})
            continue
        identity = _id(row.get("request_id"))
        kind = _integer(row.get("type"))
        if kind is None:
            unparsed.append(_uncertainty(_at(row.get("created_at"))))
            continue
        if kind != 2:
            if identity is not None:
                anchors.append(identity)
            continue
        if identity is not None:
            anchors.append(identity)
            consumption[identity] += 1
        at = _at(row.get("created_at"))
        prompt = _integer(row.get("prompt_tokens"))
        output = _integer(row.get("completion_tokens"))
        metadata = _metadata(row.get("other"))
        if identity is None or at is None or prompt is None or output is None or metadata is None:
            unparsed.append(_uncertainty(at))
            continue
        cache_read = 0 if "cache_tokens" not in metadata else _integer(metadata.get("cache_tokens"))
        cache_creation = 0 if "cache_creation_tokens" not in metadata else _integer(metadata.get("cache_creation_tokens"))
        if cache_read is None or cache_creation is None:
            unparsed.append(_uncertainty(at))
            continue
        anthropic = metadata.get("usage_semantic") == "anthropic" or metadata.get("claude") is True
        candidates.append((identity, {"id": identity, "at": at, "input_tokens": prompt + (cache_read + cache_creation if anthropic else 0),
                                      "output_tokens": output, "cache_read_tokens": cache_read,
                                      "cache_creation_tokens": cache_creation}))
    duplicate = {identity for identity, count in consumption.items() if count > 1}
    events = []
    for identity, event in candidates:
        if identity in duplicate:
            unparsed.append(_uncertainty(event["at"]))
        else:
            events.append(event)
    return {"events": events, "anchors": list(dict.fromkeys(anchors)), "empty": not rows, "unparsed": unparsed}


def read(cred: Credential, now: datetime, get) -> dict:
    ans = get(URL, {"Authorization": f"Bearer {cred.secret['key']}"}, now)
    if ans.body is None:
        return failed(VENDOR, cred.account, now, ans)
    rows = ans.body.get("data") if isinstance(ans.body, dict) and ans.body.get("success") is True else None
    if not isinstance(rows, list):
        return reading(VENDOR, cred.account, now, UNREAD, why="no-log-data")
    return dict(reading(VENDOR, cred.account, now, OK, limits=[]), _usage_snapshot=_snapshot(rows, now))
