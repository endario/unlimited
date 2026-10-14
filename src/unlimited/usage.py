"""Durable, locally observed rolling gateway-log token totals."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from unlimited import state
from unlimited.schema import iso


VERSION = 1
HORIZON = timedelta(days=30)
PERIODS = (timedelta(days=7),)
DIAGNOSTIC = "state-unreadable"
_EVENT_FIELDS = ("id", "at", "input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")
_EVENT_MODEL = "model"


def path(vendor: str, account: str) -> Path:
    """The private store for one vendor/account observation stream."""
    identity = hashlib.sha256(f"{vendor}\0{account}".encode()).hexdigest()
    return state.default_dir() / "usage" / f"{identity}.json"


def attach(r: dict, now: datetime) -> dict:
    """Import a successful private snapshot, then decorate a reading with rolling facts."""
    out = dict(r)
    snapshot = out.pop("_usage_snapshot", None)
    now = _utc(now)
    p = path(str(out.get("vendor", "")), str(out.get("account", "")))
    try:
        with state.flock(p):
            current = _load(p)
            if snapshot is not None:
                _import(current, snapshot, now)
                state.write_json(current, p)
            elif p.exists():
                _prune(current, now)
                state.write_json(current, p)
            return _attach(out, current, now)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return _unknown(out, now, diagnostic=DIAGNOSTIC)


def events(r: dict) -> list[dict] | None:
    """The retained events for a decorated reading, or None when its ledger is unreadable."""
    p = path(str(r.get("vendor", "")), str(r.get("account", "")))
    try:
        with state.flock(p):
            return list(_load(p)["events"].values())
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _new() -> dict:
    return {"version": VERSION, "events": {}, "anchors": [], "empty": False, "monitored": [],
            "unparsed": [], "collected_at": None, "first_collected_at": None}


def _load(p: Path) -> dict:
    try:
        with p.open() as f:
            value = json.load(f)
    except FileNotFoundError:
        return _new()
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != VERSION:
        raise ValueError("unsupported usage state")
    required = {"events", "anchors", "empty", "monitored", "unparsed", "collected_at", "first_collected_at"}
    if set(value) != required | {"version"} or not isinstance(value["events"], dict) \
            or not isinstance(value["anchors"], list) or not isinstance(value["empty"], bool) \
            or not isinstance(value["monitored"], list) or not isinstance(value["unparsed"], list):
        raise ValueError("corrupt usage state")
    for identity, event in value["events"].items():
        if not isinstance(identity, str) or _event(event) is None or identity != event["id"]:
            raise ValueError("corrupt usage event")
    if not all(isinstance(anchor, str) for anchor in value["anchors"]):
        raise ValueError("corrupt usage anchors")
    for name in ("monitored", "unparsed"):
        if any(_interval(interval) is None for interval in value[name]):
            raise ValueError("corrupt usage intervals")
    for name in ("collected_at", "first_collected_at"):
        if value[name] is not None and _moment(value[name]) is None:
            raise ValueError("corrupt collection time")
    collected_at = _moment(value["collected_at"])
    first_collected_at = _moment(value["first_collected_at"])
    if (collected_at is None) != (first_collected_at is None) or \
            (collected_at is not None and first_collected_at > collected_at):
        raise ValueError("inconsistent collection times")
    if collected_at is None:
        if value["monitored"]:
            raise ValueError("monitoring before collection")
    elif any(_moment(interval["starts_at"]) < first_collected_at or
             _moment(interval["ends_at"]) > collected_at for interval in value["monitored"]):
        raise ValueError("monitoring outside collection bounds")
    return value


def _import(current: dict, snapshot: object, now: datetime) -> None:
    previous = _moment(current["collected_at"])
    anchors, empty, events, uncertainties = _snapshot(snapshot, now)
    if previous is not None and now < previous:
        current["first_collected_at"] = iso(now)
        current["monitored"] = []
    elif previous is not None and (bool(set(current["anchors"]) & set(anchors)) or (current["empty"] and empty)):
        current["monitored"] = _merge([*current["monitored"], _range(previous, now)])
    for event in events:
        retained = current["events"].get(event["id"])
        if retained is None:
            current["events"][event["id"]] = event
        elif _same_tokens(retained, event) and _EVENT_MODEL not in retained and _EVENT_MODEL in event:
            # A later observation may name a legacy event without changing its measured totals.
            current["events"][event["id"]] = dict(retained, model=event[_EVENT_MODEL])
    current["anchors"] = anchors
    current["empty"] = empty
    current["unparsed"] = _merge([*current["unparsed"], *uncertainties])
    current["collected_at"] = iso(now)
    if current["first_collected_at"] is None:
        current["first_collected_at"] = iso(now)
    _prune(current, now)


def _snapshot(snapshot: object, now: datetime) -> tuple[list[str], bool, list[dict], list[dict]]:
    horizon = _range(now - HORIZON, now)
    if not isinstance(snapshot, dict):
        return [], False, [], [horizon]
    raw_events = snapshot.get("events")
    raw_anchors = snapshot.get("anchors")
    raw_unparsed = snapshot.get("unparsed")
    empty = snapshot.get("empty")
    if not isinstance(raw_events, list) or not isinstance(raw_anchors, list) or not isinstance(raw_unparsed, list) \
            or not isinstance(empty, bool):
        return [], False, [], [horizon]
    events, uncertainties = [], []
    for raw in raw_events:
        parsed = _event(raw)
        if parsed is None or _moment(parsed["at"]) > now:
            uncertainties.append(horizon)
        else:
            events.append(parsed)
    anchors = []
    for anchor in raw_anchors:
        if isinstance(anchor, str) and anchor:
            anchors.append(anchor)
        else:
            uncertainties.append(horizon)
    for raw in raw_unparsed:
        parsed = _interval(raw)
        uncertainties.append(parsed if parsed is not None else horizon)
    return list(dict.fromkeys(anchors)), empty, events, _merge(uncertainties)


def _event(value: object) -> dict | None:
    if not isinstance(value, dict) or set(value) not in (set(_EVENT_FIELDS), set(_EVENT_FIELDS) | {_EVENT_MODEL}):
        return None
    if not isinstance(value["id"], str) or not value["id"] or _moment(value["at"]) is None:
        return None
    if any(not isinstance(value[name], int) or isinstance(value[name], bool) or value[name] < 0
           for name in _EVENT_FIELDS[2:]):
        return None
    if _EVENT_MODEL in value and (not isinstance(value[_EVENT_MODEL], str) or not value[_EVENT_MODEL]):
        return None
    return {name: value[name] for name in value}


def _same_tokens(a: dict, b: dict) -> bool:
    return all(a[name] == b[name] for name in _EVENT_FIELDS[2:])


def _interval(value: object) -> dict | None:
    if not isinstance(value, dict) or set(value) != {"starts_at", "ends_at"}:
        return None
    starts, ends = _moment(value["starts_at"]), _moment(value["ends_at"])
    if starts is None or ends is None or ends <= starts:
        return None
    return _range(starts, ends)


def _moment(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return _utc(parsed) if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("usage times must be aware")
    return value.astimezone(timezone.utc)


def _range(starts: datetime, ends: datetime) -> dict:
    return {"starts_at": iso(starts), "ends_at": iso(ends)}


def _merge(intervals: list[dict]) -> list[dict]:
    spans = sorted((_interval(interval) for interval in intervals), key=lambda span: span["starts_at"] if span else "")
    out: list[dict] = []
    for span in spans:
        if span is None:
            continue
        if out and _moment(span["starts_at"]) <= _moment(out[-1]["ends_at"]):
            if _moment(span["ends_at"]) > _moment(out[-1]["ends_at"]):
                out[-1]["ends_at"] = span["ends_at"]
        else:
            out.append(dict(span))
    return out


def _prune(current: dict, now: datetime) -> None:
    cutoff = now - HORIZON
    current["events"] = {identity: event for identity, event in current["events"].items()
                         if _moment(event["at"]) >= cutoff}
    for name in ("monitored", "unparsed"):
        current[name] = _merge([interval for interval in current[name]
                                if _moment(interval["ends_at"]) > cutoff])


def _attach(r: dict, current: dict, now: datetime) -> dict:
    collected = _moment(current["collected_at"])
    if collected is None:
        return _unknown(r, now)
    first = _moment(current["first_collected_at"])
    if first is None:
        return _unknown(r, now, diagnostic=DIAGNOSTIC)
    events = list(current["events"].values())
    periods = []
    for duration in PERIODS:
        starts = now - duration
        included = [event for event in events if starts <= _moment(event["at"]) < now]
        periods.append(_period(starts, now, duration, included, current, first))
    return dict(r, usage={"source": "gateway-log", "as_of": iso(now), "collected_at": iso(collected),
                          "periods": periods})


def _unknown(r: dict, now: datetime, diagnostic: str | None = None) -> dict:
    periods = []
    for duration in PERIODS:
        periods.append({"window_minutes": int(duration.total_seconds() // 60), "starts_at": iso(now - duration),
                        "ends_at": iso(now), "input_tokens": None, "output_tokens": None,
                        "total_tokens": None, "cache_read_tokens": None, "cache_creation_tokens": None,
                        "coverage": "unknown", "reasons": []})
    usage = {"source": "gateway-log", "as_of": iso(now), "collected_at": None, "periods": periods}
    if diagnostic is not None:
        usage["diagnostic"] = diagnostic
    return dict(r, usage=usage)


def _period(starts: datetime, ends: datetime, duration: timedelta, events: list[dict], current: dict,
            first: datetime) -> dict:
    input_tokens = sum(event["input_tokens"] for event in events)
    output_tokens = sum(event["output_tokens"] for event in events)
    cache_read_tokens = sum(event["cache_read_tokens"] for event in events)
    cache_creation_tokens = sum(event["cache_creation_tokens"] for event in events)
    reasons = []
    if starts < first:
        reasons.append("horizon-edge")
    if ends > first and not _covers(current["monitored"], max(starts, first), ends):
        reasons.append("tail-gap")
    if any(_intersects(interval, starts, ends) for interval in current["unparsed"]):
        reasons.append("unparsed")
    coverage = "observed" if not reasons else "partial"
    return {"window_minutes": int(duration.total_seconds() // 60), "starts_at": iso(starts), "ends_at": iso(ends),
            "input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": input_tokens + output_tokens,
            "cache_read_tokens": cache_read_tokens, "cache_creation_tokens": cache_creation_tokens,
            "coverage": coverage, "reasons": reasons}


def _covers(intervals: list[dict], starts: datetime, ends: datetime) -> bool:
    cursor = starts
    for interval in intervals:
        left, right = _moment(interval["starts_at"]), _moment(interval["ends_at"])
        if right <= cursor:
            continue
        if left > cursor:
            return False
        cursor = max(cursor, right)
        if cursor >= ends:
            return True
    return cursor >= ends


def _intersects(interval: dict, starts: datetime, ends: datetime) -> bool:
    return _moment(interval["starts_at"]) < ends and _moment(interval["ends_at"]) > starts
