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
# The gateway's address is the operator's, so it lives beside the key, never here.
BASE = "FY_API_BASE_URL"
LOG_PATH, MODELS_PATH = "/api/log/token", "/v1/models"
KEYS = EnvKeys("FY_API_KEY", "*.env")
names = KEYS.names
budget_keys = KEYS
attach = usage.attach


def discover() -> list[Credential]:
    """Each key's credential, carrying the gateway base its env file (or the environment) names."""
    return KEYS.discover_based(BASE)


def _get(cred: Credential, path: str, now: datetime, get):
    return get(cred.secret["base"] + path, {"Authorization": f"Bearer {cred.secret['key']}"}, now)


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


def _model(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


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
        model = _model(row.get("model_name"))
        anthropic = metadata.get("usage_semantic") == "anthropic" or metadata.get("claude") is True
        candidates.append((identity, {"id": identity, "at": at, "input_tokens": prompt + (cache_read + cache_creation if anthropic else 0),
                                      "output_tokens": output, "cache_read_tokens": cache_read,
                                      "cache_creation_tokens": cache_creation,
                                      **({"model": model} if model is not None else {})}))
    duplicate = {identity for identity, count in consumption.items() if count > 1}
    events = []
    for identity, event in candidates:
        if identity in duplicate:
            unparsed.append(_uncertainty(event["at"]))
        else:
            events.append(event)
    return {"events": events, "anchors": list(dict.fromkeys(anchors)), "empty": not rows, "unparsed": unparsed}


def whoami(cred: Credential, now: datetime, get):
    """The gateway's user id for the key, from its logged requests: the one id every row names.
    None while the key has logged nothing, so it is asked again later."""
    if "base" not in cred.secret:
        return None, None
    ans = _get(cred, LOG_PATH, now, get)
    rows = ans.body.get("data") if isinstance(ans.body, dict) and ans.body.get("success") is True else None
    ids = {row.get("user_id") for row in rows if isinstance(row, dict)} if isinstance(rows, list) else set()
    [who] = ids if len(ids) == 1 else [None]
    return (str(who) if _integer(who) else None), ans


def read(cred: Credential, now: datetime, get) -> dict:
    if "base" not in cred.secret:
        return reading(VENDOR, cred.account, now, UNREAD, why="no-base-url")
    ans = _get(cred, LOG_PATH, now, get)
    if ans.body is None:
        return failed(VENDOR, cred.account, now, ans)
    rows = ans.body.get("data") if isinstance(ans.body, dict) and ans.body.get("success") is True else None
    if not isinstance(rows, list):
        return reading(VENDOR, cred.account, now, UNREAD, why="no-log-data")
    return dict(reading(VENDOR, cred.account, now, OK, limits=[]), _usage_snapshot=_snapshot(rows, now))


def models(cred: Credential, now: datetime, get) -> dict:
    if "base" not in cred.secret:
        return dict(reading(VENDOR, cred.account, now, UNREAD, why="no-base-url"), routes=[])
    ans = _get(cred, MODELS_PATH, now, get)
    if ans.body is None:
        return dict(failed(VENDOR, cred.account, now, ans), routes=[])
    rows = ans.body.get("data") if isinstance(ans.body, dict) else None
    ids = sorted({model["id"] for model in (rows if isinstance(rows, list) else [])
                  if isinstance(model, dict) and isinstance(model.get("id"), str) and model["id"]})
    # Listed aliases need a successful dispatch; this is not an uptime guarantee.
    dispatchable = {"glm-5.3", "glm-5.3-flash", "gpt-6-luna", "gpt-6.1-sol", "gpt-6-astra"}
    return dict(reading(VENDOR, cred.account, now, OK, why=ans.why, retry_until=ans.retry_until),
                routes=[{"id": model, "dispatchable": model in dispatchable} for model in ids])
