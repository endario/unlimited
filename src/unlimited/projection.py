"""Where each open window is heading: arithmetic on the readings unlimited has already taken.

A reading says how much of a window is used; it cannot say whether that is a lot for the time
elapsed. Two sources say where it is heading:

- This window's own readings give two paces extended to the reset: the average since the window
  opened (long memory), and a recency-weighted pace whose half-life is a fourteenth of the window
  (12 hours of a week, ~21 minutes of 5 hours), so a burst lifts it and a quiet spell lets it fall.
- Past windows of the same limit give the shape of use: how much each one still used from this
  point of the window to its end. That knows the night is quiet and Monday is busy, which no pace
  does. A past window that hit its limit is extended at its own pace, as demand it could not spend.

With few past windows the projection is the paces'; it moves to the past windows' as they
accumulate. `at_reset` is [low, high], unclamped: 1.07 means use would pass the limit.
`recent_at_reset` is the recent pace alone, extended to the reset: today's momentum, which
past windows do not move.
`exhausts_at` is when the high end reaches 1.0, if before the reset. `run_out` is how likely use
passes the limit, from the past windows that, shifted to today's use, did. None of it is advice.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from .schema import iso, moment

# Readings of one window whose resets differ by less than this are the same window; vendors
# re-derive the reset per request and it drifts by fractions of a second.
SAME_WINDOW = timedelta(minutes=10)
# At most this many samples per window: a sample closer than window/SAMPLES to the last is skipped.
SAMPLES = 500
HALF_LIFE = 14  # the recent pace halves its memory every window/HALF_LIFE
MIN_SPAN = 50  # nothing is projected before window/MIN_SPAN has elapsed
GRID = 168  # a past window is kept as its use at GRID+1 even points (hourly for a week)
KEEP = 8  # past windows kept per limit
MIN_PAST, FULL_PAST = 3, 8  # past windows start to count at MIN_PAST, fully at FULL_PAST
AGE_TAU = 3.0  # a past window's weight is exp(-windows ago / AGE_TAU)
COVERED = 6 / 7  # a past window is kept only if it was read this far into it


def _key(account: str | None, name: str) -> str:
    return f"{account}\t{name}"


def _sample(s: object) -> tuple | None:
    """A stored [taken_at, used, resets_at], parsed; None for anything else in the cache file."""
    if not (isinstance(s, list) and len(s) == 3 and isinstance(s[1], (int, float))):
        return None
    t, at = moment(s[0]), moment(s[2])
    return (t, s[1], at) if t is not None and at is not None else None


def _entry(v: object) -> tuple[list[tuple], list[dict]]:
    """A key's stored (samples, past windows). The pre-history shape was a bare sample list."""
    if isinstance(v, list):
        v = {"samples": v}
    if not isinstance(v, dict):
        return [], []
    samples = [p for p in map(_sample, v.get("samples") or []) if p] if isinstance(v.get("samples"), list) else []
    past = [p for p in v.get("past") or [] if isinstance(p, dict) and moment(p.get("resets_at"))
            and isinstance(p.get("curve"), list) and len(p["curve"]) == GRID + 1
            and all(isinstance(x, (int, float)) for x in p["curve"])] if isinstance(v.get("past"), list) else []
    return samples, past


def _stored(samples: list[tuple], past: list[dict], window_minutes: int | None) -> dict:
    out = {"samples": [[iso(t), u, iso(at)] for t, u, at in samples], "past": past[-KEEP:]}
    if window_minutes:
        out["window_minutes"] = window_minutes
    return out


def _trackable(l: dict) -> bool:
    return isinstance(l.get("name"), str) and bool(l.get("window_minutes")) and l.get("used_at_least") is not None \
        and moment(l.get("resets_at")) is not None


def _curve(samples: list[tuple], window: timedelta) -> list[float] | None:
    """A finished window's use at GRID+1 even points of it, or None if it was not read to its end."""
    resets = samples[-1][2]
    start = resets - window
    pts = [(0.0, 0.0)] + sorted(((t - start) / window, u) for t, u, _ in samples if start <= t <= resets)
    if len(pts) < 3 or pts[-1][0] < COVERED:
        return None
    out, j = [], 0
    for i in range(GRID + 1):
        x = i / GRID
        while j + 1 < len(pts) and pts[j + 1][0] < x:
            j += 1
        if j + 1 >= len(pts):
            y = pts[-1][1]
        else:
            (x0, y0), (x1, y1) = pts[j], pts[j + 1]
            y = y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
        out.append(round(max(y, out[-1] if out else 0.0), 4))  # use only accumulates
    return out


def _archive(samples: list[tuple], past: list[dict], window_minutes: int | None) -> list[dict]:
    if not samples or not window_minutes:
        return past
    curve = _curve(samples, timedelta(minutes=window_minutes))
    return past + [{"resets_at": iso(samples[-1][2]), "curve": curve}] if curve else past


def record(history: dict, readings: list[dict]) -> dict:
    """`history` with each ok reading's open windows appended. A window that has reset is kept
    as a past window, and its limit's samples start over."""
    out = dict(history)
    for r in readings:
        taken = moment(r.get("taken_at"))
        if r.get("status") != "ok" or taken is None:
            continue
        for l in r.get("limits", []):
            if not _trackable(l):
                continue
            key, resets, minutes = _key(r.get("account"), l["name"]), moment(l["resets_at"]), l["window_minutes"]
            entry = out.get(key)
            samples, past = _entry(entry)
            if samples and abs(samples[-1][2] - resets) >= SAME_WINDOW:
                if taken <= samples[-1][0]:
                    continue  # an older reading of a window already moved past
                was = entry.get("window_minutes") if isinstance(entry, dict) else None
                # A reset moved back is the vendor correcting this window, not a new one.
                past = _archive(samples, past, was or minutes) if resets > samples[-1][2] else past
                samples = []
            if samples and taken - samples[-1][0] < timedelta(minutes=minutes) / SAMPLES:
                continue
            out[key] = _stored(samples + [(taken, l["used_at_least"], resets)], past, minutes)
    return out


def prune(history: dict, now: datetime) -> dict:
    """Windows that have reset move to the past; a limit with nothing left goes."""
    out = {}
    for k, v in history.items():
        samples, past = _entry(v)
        minutes = v.get("window_minutes") if isinstance(v, dict) else None
        if samples and samples[-1][2] <= now:
            past, samples = _archive(samples, past, minutes), []
        if samples or past:
            out[k] = _stored(samples, past, minutes)
    return out


def _paces(pts: list[tuple], start: datetime, window: timedelta) -> tuple[float, float]:
    """(average, recent) use per second. The window opened at zero use."""
    t, u = pts[-1]
    avg = u / (t - start).total_seconds()
    half = (window / HALF_LIFE).total_seconds()
    num = den = 0.0
    prev = (start, 0.0)
    for p in pts:
        dt = (p[0] - prev[0]).total_seconds()
        if dt > 0:
            mid = (t - (prev[0] + (p[0] - prev[0]) / 2)).total_seconds()
            w = dt * 0.5 ** (mid / half)
            num, den = num + w * max(p[1] - prev[1], 0.0) / dt, den + w
            prev = p
    return avg, (num / den if den else avg)


def _at(curve: list[float], x: float) -> float:
    i = min(int(x * GRID), GRID - 1)
    return curve[i] + (curve[i + 1] - curve[i]) * (x * GRID - i)


def _extended(curve: list[float]) -> list[float]:
    """A window that hit its limit, continued at its own pace to the end: the demand it could not spend."""
    cap = next((i for i, y in enumerate(curve) if y >= 0.999), None)
    if cap is None or cap == 0 or cap == GRID:
        return curve
    return curve[:cap] + [curve[cap] * i / cap for i in range(cap, GRID + 1)]


def _quantile(values: list[float], weights: list[float], q: float) -> float:
    pairs = sorted(zip(values, weights))
    total, run = sum(weights), 0.0
    for v, w in pairs:
        run += w
        if run >= q * total:
            return v
    return pairs[-1][0]


def _history(past: list[dict], resets: datetime, window: timedelta, x: float, u: float):
    """From past windows: (low, high, run_out, ETA in seconds or None, strength 0..1), or None."""
    scoped = [p for p in past if moment(p["resets_at"]) < resets - SAME_WINDOW]
    if not scoped:
        return None
    ws = [math.exp(-max((resets - moment(p["resets_at"])) / window, 0) / AGE_TAU) for p in scoped]
    strength = min(max((len(scoped) - MIN_PAST + 1) / (FULL_PAST - MIN_PAST + 1), 0.0), 1.0)
    if strength == 0:
        return None
    ends, etas, out_w = [], [], 0.0
    for p, w in zip(scoped, ws):
        c = _extended(p["curve"])
        shift = u - _at(c, x)
        ends.append(c[-1] + shift)
        if c[-1] + shift >= 1:
            out_w += w
            i = next(i for i in range(math.floor(x * GRID) + 1, GRID + 1) if c[i] + shift >= 1)
            etas.append((max(i / GRID - x, 0) * window.total_seconds(), w))
    run_out = (out_w + 0.5) / (sum(ws) + 1)
    eta = _quantile([e for e, _ in etas], [w for _, w in etas], 0.5) if etas else None
    return _quantile(ends, ws, 0.25), _quantile(ends, ws, 0.75), run_out, eta, strength


def project(entry: object, l: dict) -> dict | None:
    """The projection for limit `l` from its stored history, or None where there is too little."""
    if not _trackable(l):
        return None
    samples, past = _entry(entry)
    window = timedelta(minutes=l["window_minutes"])
    resets = moment(l["resets_at"])
    start = resets - window
    pts = [(t, u) for t, u, at in samples if abs(at - resets) < SAME_WINDOW and t >= start]
    if not pts:
        return None
    t, u = pts[-1]
    left = (resets - t).total_seconds()
    if left <= 0 or t - start < window / MIN_SPAN:
        return None
    avg, recent = _paces(pts, start, window)
    lo, hi = sorted([u + avg * left, u + recent * left])
    now_pace = u + recent * left
    fast = max(avg, recent)
    eta = (1 - u) / fast if u < 1 < u + fast * left else None
    run_out = None
    hist = _history(past, resets, window, (t - start) / window, u)
    if hist:
        h_lo, h_hi, run_out, h_eta, s = hist
        lo, hi = s * h_lo + (1 - s) * lo, s * h_hi + (1 - s) * hi
        if run_out >= 0.5 and h_eta is not None:
            eta = s * h_eta + (1 - s) * (eta if eta is not None else left)
        elif hi < 1:
            eta = None
    exhausts = t + timedelta(seconds=eta) if eta is not None and u < 1 and eta < left else None
    return {"at_reset": [round(lo, 4), round(hi, 4)], "recent_at_reset": round(now_pace, 4),
            "exhausts_at": iso(exhausts),
            "run_out": round(run_out, 3) if run_out is not None else None,
            "samples": len(pts), "past_windows": len(past), "since": iso(pts[0][0])}


def attach(r: dict, history: dict) -> dict:
    """`r` with a `projection` on each limit (None where there is none)."""
    return dict(r, limits=[dict(l, projection=project(history.get(_key(r.get("account"), l.get("name"))), l))
                           for l in r.get("limits", [])])
