"""Whether a model on an account can take a unit of work, from one schema-1 reading
(docs/usage-routing, phase 1). Pure: no I/O and no clock of its own.

The evaluation is 2mw2lt's doc 117 (`steering/spending.verdict`), with its tested rules, over
unlimited's own readings, with two changes: limits are filtered by the model the work runs, and the
scored window is a plan's monthly bucket where it enforces one, else its week. Ordering, tie rules
and fallback stay with each consumer."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from .schema import moment

# The least fraction of a window the paced figure and the score divide by: a window that has only
# just opened says nothing yet about its pace.
FLOOR = 0.05
WEEK_MINUTES = 7 * 24 * 60
# The windows a vendor's reading must carry to be read at all.
EXPECTED = {"anthropic": ("five_hour", "seven_day"), "zai": ("five_hour", "seven_day"),
            "opencode": ("five_hour", "seven_day", "month")}
# A hold unlimited reports is the vendor's own word, except these: the account still runs.
NOT_STOPPED = frozenset({"overage"})
# Roles that bind all work on an account; `weekly_model` binds only its scope's model.
ACCOUNT_ROLES = frozenset({"session", "weekly", "month", "extra"})


def stopped(l: dict) -> bool:
    """Whether the vendor has stopped work under this limit."""
    return l.get("held") is True and l.get("held_why") not in NOT_STOPPED


def applies(l: dict, model_scope: str | None) -> bool:
    """Whether a limit binds work on `model_scope`. A limit with no role key at all comes from a
    reading that predates roles, or a wire that dropped them, and binds: missing data is never read
    as no model-specific limit."""
    if "role" not in l:
        return True
    role = l.get("role")
    if role == "weekly_model":
        scope = l.get("scope")
        return model_scope is not None and isinstance(scope, str) and scope.lower() == model_scope.lower()
    return role in ACCOUNT_ROLES


def _number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _window(l: dict, now: datetime) -> dict | None:
    """One limit as a window this module reads, or None when it is malformed or already reset."""
    used, minutes, resets = l.get("used_at_least"), l.get("window_minutes"), moment(l.get("resets_at"))
    if not (_number(used) and used >= 0) or not (_number(minutes) and minutes > 0) or resets is None:
        return None
    if resets <= now:
        return None
    f = min((resets - now) / timedelta(minutes=minutes), 1.0)
    elapsed = max(1.0 - f, FLOOR)
    p = l.get("projection")
    at_reset = p.get("at_reset") if isinstance(p, dict) else None
    if isinstance(at_reset, list) and len(at_reset) == 2 and all(_number(x) and x >= 0 for x in at_reset):
        lo, hi = float(at_reset[0]), float(at_reset[1])
        raw = p.get("exhausts_at")
        exhausts = moment(raw)
        if raw is not None and exhausts is None:
            return None
        paced = False
    else:
        lo = hi = used / elapsed
        exhausts = None
        paced = True
        if hi >= 1 and used < 1:
            exhausts = now + (resets - now) * ((1 - used) / (hi - used))
    if used >= 1:
        exhausts = now
    if exhausts is not None and exhausts >= resets:
        exhausts = None
    return {"name": l.get("name"), "role": l.get("role"), "used": float(used), "minutes": minutes,
            "resets": resets, "f": f, "lo": lo, "hi": hi, "exhausts": exhausts, "paced": paced,
            "stopped": stopped(l)}


def _scored(live: list[dict]) -> dict | None:
    """The plan's monthly bucket where it enforces one, else its longest window up to a week."""
    months = [w for w in live if w["role"] == "month" or w["name"] == "month"]
    pool = months or [w for w in live if w["minutes"] <= WEEK_MINUTES]
    if not pool:
        return None
    longest = max(w["minutes"] for w in pool)
    return max((w for w in pool if w["minutes"] == longest), key=lambda w: (w["hi"], str(w["name"])))


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t is not None else None


def verdict(reading: object, *, model_scope: str | None, now: datetime, work: timedelta,
            max_age: timedelta, starts: datetime | None = None) -> dict:
    """`unread`, `excluded` or `ranked` for `work` on `model_scope` starting at `starts`.
    `ranked` is advisory: it describes one reading and reserves nothing."""
    starts = starts or now
    if not isinstance(reading, dict):
        return {"state": "unread", "reason": "no-reading"}
    if reading.get("status") != "ok":
        return {"state": "unread", "reason": "not-ok"}
    taken = moment(reading.get("taken_at"))
    if taken is None or taken > now or now - taken > max_age:
        return {"state": "unread", "reason": "stale"}
    limits = reading.get("limits")
    if not isinstance(limits, list):
        return {"state": "unread", "reason": "invalid"}
    windows, present, skipped = [], set(), []
    expected = EXPECTED.get(reading.get("vendor"), ())
    for l in limits:
        if not isinstance(l, dict) or not (l.get("resets_at") or l.get("window_minutes")):
            continue
        if not applies(l, model_scope):
            continue
        present.add(l.get("name"))
        if l.get("resets_at") is None and l.get("used_at_least") == 0:
            continue  # not yet opened: present and unconstrained
        w = _window(l, now)
        if w is None:
            reset = moment(l.get("resets_at"))
            if reset is not None and reset <= now:
                continue  # reset since the reading: says nothing now
            if expected and l.get("name") not in expected:
                skipped.append(l.get("name"))
                continue
            return {"state": "unread", "reason": "invalid", "window": l.get("name")}
        windows.append(w)
    missing = [n for n in expected if n not in present]
    if missing:
        return {"state": "unread", "reason": "no-window", "window": missing[0]}
    live = [w for w in windows if w["resets"] > starts]
    s = _scored(live)
    if s is None:
        return {"state": "unread", "reason": "no-window"}
    runs_out = [w for w in live if w["exhausts"] is not None]
    first = min(runs_out, key=lambda w: w["exhausts"]) if runs_out else None
    out = {"window": s["name"], "used": s["used"], "at_reset": [s["lo"], s["hi"]], "paced": s["paced"],
           "resets_at": _iso(s["resets"]), "exhausts_at": _iso(first["exhausts"]) if first else None,
           "runway": (first["exhausts"] - now).total_seconds() if first else None,
           "binding": sorted({str(w["name"]) for w in live})}
    if skipped:
        out["skipped"] = skipped
    for reason, hit in (("stopped", [w for w in live if w["stopped"]]),
                        ("exhausted", [w for w in live if w["used"] >= 1]),
                        ("runs-out", [w for w in runs_out if w["exhausts"] < starts + work])):
        if hit:
            last = max(hit, key=lambda w: w["resets"])
            return {**out, "state": "excluded", "reason": reason, "by": last["name"],
                    "until": _iso(last["resets"])}
    over = [w for w in live if w["hi"] >= 1 or w["exhausts"] is not None]
    if over:
        until = min(w["exhausts"] or w["resets"] for w in over)
        return {**out, "state": "ranked", "tier": 1, "score": (until - now).total_seconds() / 3600}
    return {**out, "state": "ranked", "tier": 0, "score": (1 - s["hi"]) / max(s["f"], FLOOR)}
