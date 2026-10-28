"""Where each open window is heading: arithmetic on the readings unlimited has already taken.

A reading says how much of a window is used; it cannot say whether that is a lot for the time
elapsed. The projection is a vote of the windows before it:

- Each past window of the same limit votes by what it still used from this point of the window to
  its end. That knows the night is quiet and Monday is busy, which no pace does. This window's level
  against the typical one scales the vote, at half strength.
- A past window that hit its limit is a floor, never extended: it is silent once it was already at
  the limit at this point, and otherwise may have wanted up to twice what it drew.
- Each vote is blurred, more the more it still draws, so one window never decides the forecast.
- This window's own pace is one more vote, half the average since it opened and the recent pace
  (whose half-life is a fourteenth of the window). It counts for less as past windows accumulate:
  for windows of a day or longer from the third, for shorter ones from the sixth.

`at_reset` is [low, high], the quartiles of the blurred votes, never below what is already used and
unclamped above: 1.07 means use would pass the limit. `recent_at_reset` is the recent pace alone,
extended to the reset: today's momentum, which past windows do not move. `run_out` is how likely
use reaches 95%, which is as full as readings usually show a window that ran out. `exhausts_at` is
when the votes that reach it do, if before the reset. None of it is advice.
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
MIN_PAST = 3  # past windows that mark a forecast as backed by history rather than a guess
RAN = 0.95  # a window this full ran out: readings stop a hair short of 1.0
AGE_TAU = 4.0  # a past window's weight is exp(-windows ago / AGE_TAU)
LEVEL_OFFSET, LEVEL_LO, LEVEL_HI = 0.05, 0.25, 4.0  # this window's level against theirs, then taken at half strength
SPREAD_BASE, SPREAD_GROW = 0.03, 0.25  # a vote's blur: this, plus SPREAD_GROW of what it still draws
LIFT = 2.0  # a past window that ran dry may have wanted up to this many times what it drew
LONG_WINDOW = timedelta(days=1)  # from this length, three past windows outweigh the own pace
RAMP_LONG, RAMP_SHORT = 3, 6
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


def _quantile(values: list[float], weights: list[float], q: float) -> float:
    pairs = sorted(zip(values, weights))
    total, run = sum(weights), 0.0
    for v, w in pairs:
        run += w
        if run >= q * total:
            return v
    return pairs[-1][0]


def _cdf(votes: list[tuple[float, float, float]], v: float) -> float:
    """The blurred votes' cumulative share at `v`; votes are (mean, spread, weight)."""
    return sum(w * 0.5 * (1 + math.erf((v - m) / sd / math.sqrt(2))) for m, sd, w in votes)


def _mixture_quantile(votes: list[tuple[float, float, float]], q: float) -> float:
    lo, hi = -0.5, 4.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if _cdf(votes, mid) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _past_votes(past: list[dict], resets: datetime, window: timedelta, x: float, u: float):
    """What past windows say from x of the way through, at use u: [(mean, spread, weight, phase it
    reaches the limit or None)], weights summing to one. Each one's draw from here on, this window's
    level against theirs; one that ran dry is a floor, lifted by at most what it drew, and silent
    when it was already at the limit by x."""
    kept = []
    for p in past:
        if moment(p["resets_at"]) >= resets - SAME_WINDOW:
            continue
        cap = next((i for i, y in enumerate(p["curve"]) if y >= RAN), None)
        if cap is not None and cap / GRID <= x:
            continue
        kept.append(p)
    if not kept:
        return []
    ws = [math.exp(-max((resets - moment(p["resets_at"])) / window, 0) / AGE_TAU) for p in kept]
    total = sum(ws)
    typical = sum(w * _at(p["curve"], x) for w, p in zip(ws, kept)) / total
    k = math.sqrt(min(max((u + LEVEL_OFFSET) / (typical + LEVEL_OFFSET), LEVEL_LO), LEVEL_HI))
    votes = []
    for w, p in zip(ws, kept):
        c = p["curve"]
        here = _at(c, x)
        drew = c[-1] - here
        mean = u + k * drew
        if c[-1] >= RAN:
            mean = max(mean, min(1.0, u + LIFT * drew))
        crosses = next((i / GRID for i in range(math.floor(x * GRID) + 1, GRID + 1) if u + k * (c[i] - here) >= RAN), None)
        votes.append((mean, SPREAD_BASE + SPREAD_GROW * k * drew, w / total, crosses))
    return votes


def project(entry: object, l: dict, taken: datetime | None = None) -> dict | None:
    """The projection for limit `l` from its stored history, or None where there is too little.
    `l` read at `taken` is the newest point when the stored samples, thinned, trail it."""
    if not _trackable(l):
        return None
    samples, past = _entry(entry)
    window = timedelta(minutes=l["window_minutes"])
    resets = moment(l["resets_at"])
    start = resets - window
    pts = [(t, u) for t, u, at in samples if abs(at - resets) < SAME_WINDOW and t >= start]
    if not pts:
        return None
    stored = len(pts)
    if taken is not None and pts[-1][0] < taken < resets:
        pts.append((taken, l["used_at_least"]))
    t, u = pts[-1]
    left = (resets - t).total_seconds()
    if left <= 0 or t - start < window / MIN_SPAN:
        return None
    avg, recent = _paces(pts, start, window)
    x = (t - start) / window
    pace = (avg + recent) / 2
    history = _past_votes(past, resets, window, x, u)
    ramp = RAMP_LONG if window >= LONG_WINDOW else RAMP_SHORT
    s = min(len(history) / ramp, 1.0)
    own = (u + pace * left, SPREAD_BASE + SPREAD_GROW * pace * left, 1 - s)
    votes, crossings = [own], []
    if u < RAN and pace > 0 and (RAN - u) / pace <= left:
        crossings.append((x + (RAN - u) / pace / window.total_seconds(), own[2]))
    for mean, sd, w, crosses in history:
        votes.append((mean, sd, w * s))
        if crosses is not None:
            crossings.append((crosses, w * s))
    run_out = 1 - _cdf(votes, RAN)
    exhausts = None
    if run_out >= 0.5:
        told = [(phase, w) for phase, w in crossings if w > 0]  # a vote the mixture discarded tells no time
        if told:
            phase = _quantile([p for p, _ in told], [w for _, w in told], 0.5)
            if phase < 1:
                exhausts = start + timedelta(microseconds=round(phase * window / timedelta(microseconds=1)))

    def bound(q: float) -> float:
        return round(max(_mixture_quantile(votes, q), u), 4)  # use only accumulates, and never below zero

    return {"at_reset": [bound(0.25), bound(0.75)], "recent_at_reset": round(u + recent * left, 4),
            "exhausts_at": iso(exhausts), "run_out": round(run_out, 3),
            "samples": stored, "past_windows": len(past), "since": iso(pts[0][0])}


def attach(r: dict, history: dict) -> dict:
    """`r` with a `projection` on each limit (None where there is none)."""
    taken = moment(r.get("taken_at"))
    return dict(r, limits=[dict(l, projection=project(history.get(_key(r.get("account"), l.get("name"))), l, taken))
                           for l in r.get("limits", [])])
