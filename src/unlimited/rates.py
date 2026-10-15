"""A vendor's time-of-day rates: the hours its quota is charged at another factor than usual.

A rate is published by the vendor, not read from its usage answer, so the catalog declares it and
each reading carries the spans it resolves to. A span is absolute (UTC instants) so a reader needs
no calendar, time zone or plan to apply it: outside every span the factor is 1.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, time, timedelta, timezone

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
HORIZON = timedelta(days=8)  # past the longest window a reading has (a week)
_CLOCK = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$|^24:00$")
_OFFSET = re.compile(r"^([+-])(\d{2}):(\d{2})$")


def _clock(v: str) -> timedelta:
    h, m = v.split(":")
    return timedelta(hours=int(h), minutes=int(m))


def _tz(v: str) -> timezone:
    sign, h, m = _OFFSET.match(v).groups()
    delta = timedelta(hours=int(h), minutes=int(m))
    return timezone(-delta if sign == "-" else delta)


def problem(r: object) -> str | None:
    """Why a `[[rates]]` entry is not one, or None."""
    if not isinstance(r, dict):
        return "a table"
    if not isinstance(r.get("vendor"), str) or not r["vendor"]:
        return "a vendor"
    if not isinstance(r.get("meter", ""), str):
        return "meter, the limit kind it applies to, is a string"
    offerings = r.get("offerings", [])
    if not (isinstance(offerings, list) and all(isinstance(o, str) for o in offerings)):
        return "offerings is a list of offering ids"
    days = r.get("days", list(DAYS))
    if not (isinstance(days, list) and days and all(d in DAYS for d in days)):
        return f"days is a list of {', '.join(DAYS)}"
    if not all(isinstance(r.get(k), str) and _CLOCK.match(r[k]) for k in ("from", "to")):
        return "from and to are HH:MM (to may be 24:00)"
    if _clock(r["from"]) >= _clock(r["to"]):
        return "from is before to"
    if not (isinstance(r.get("tz"), str) and _OFFSET.match(r["tz"])):
        return "tz is a fixed offset, +HH:MM"
    f = r.get("factor")
    if not (isinstance(f, (int, float)) and not isinstance(f, bool) and math.isfinite(f) and f > 0):
        return "factor is a positive number"
    return None


def applies(r: dict, reading: dict) -> bool:
    """Whether a rate is this reading's account's: its vendor, and its meter among the limits."""
    if r["vendor"] != reading.get("vendor"):
        return False
    meter = r.get("meter")
    return not meter or any(isinstance(l, dict) and l.get("kind") == meter for l in reading.get("limits") or [])


def spans(rates: list[dict], reading: dict, now: datetime) -> list[dict]:
    """The reading's rate spans from now to HORIZON, earliest first: `{offerings, from, to, factor}`,
    `offerings` None for every offering on the account."""
    end = now + HORIZON
    out = []
    for r in rates:
        if not applies(r, reading):
            continue
        tz = _tz(r["tz"])
        days = set(r.get("days", DAYS))
        first: date = now.astimezone(tz).date() - timedelta(days=1)
        for i in range((HORIZON.days) + 2):
            day = first + timedelta(days=i)
            if DAYS[day.weekday()] not in days:
                continue
            midnight = datetime.combine(day, time(), tz)
            start, stop = max(midnight + _clock(r["from"]), now), min(midnight + _clock(r["to"]), end)
            if start < stop:
                out.append({"offerings": list(r["offerings"]) if r.get("offerings") else None,
                            "from": start.astimezone(timezone.utc).isoformat(),
                            "to": stop.astimezone(timezone.utc).isoformat(), "factor": r["factor"]})
    return sorted(out, key=lambda s: (s["from"], s["to"]))
