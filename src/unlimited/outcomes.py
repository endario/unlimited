"""What each model's attempts came to on this machine (docs/choice.md): an
append-only log of attempts, and the decayed failure rate and durations read from it."""

from __future__ import annotations

import fcntl
import json
import math
import os
import statistics
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

HALF_LIFE = timedelta(hours=12)
KEEP = timedelta(days=7)  # 14 half-lives: a record's weight is below 10⁻⁴
COMPACT_BYTES = 1 << 20
# Beta prior on failure: a 10% rate, worth five attempts.
A0, B0 = 0.5, 4.5
# Log-duration prior, worth three attempts, until there is history.
N0, MU0 = 3.0, math.log(300.0)
OUTCOMES = ("ok", "timeout", "error", "unavailable")
VERSION = 1  # of each record this log holds


def path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base) / "unlimited" / "decisions.jsonl"


def append(record: dict, p: Path | None = None) -> None:
    p = p or path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(record, separators=(",", ":")) + "\n")
        f.flush()


def read(p: Path | None = None) -> tuple[list[dict], int]:
    """Every record that parses, and how many lines did not (a torn last line, a hand edit)."""
    p = p or path()
    try:
        lines = p.read_text().splitlines()
    except FileNotFoundError:
        return [], 0
    out, bad = [], 0
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(r, dict):
            out.append(r)
        else:
            bad += 1
    return out, bad


def compact(now: datetime, p: Path | None = None) -> None:
    """Drop records older than KEEP, once the file is large enough to matter."""
    p = p or path()
    try:
        if p.stat().st_size < COMPACT_BYTES:
            return
    except FileNotFoundError:
        return
    with open(p, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        kept = []
        for line in f.read().splitlines():
            try:
                t = _time(json.loads(line).get("at"))
            except (ValueError, AttributeError):
                continue
            if t is not None and now - t < KEEP:
                kept.append(line)
        f.seek(0)
        f.truncate()
        f.write("".join(x + "\n" for x in kept))


def _time(v: object) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        t = datetime.fromisoformat(v)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def start(*, provider: str, model: str, effort: str | None, task: str | None, account: str | None,
          decision: str | None, deadline: float, now: datetime, meta: dict | None = None,
          p: Path | None = None) -> str:
    """Record an attempt as it was asked. `task` and `meta` are the caller's own: recorded, never
    read by unlimited."""
    aid = uuid.uuid4().hex[:16]
    append({"v": VERSION, "type": "start", "attempt": aid, "at": now.isoformat(), "provider": provider,
            "model": model, "effort": effort, "task": task, "account": account, "decision": decision,
            "deadline": deadline, "meta": meta or {}}, p)
    return aid


def end(aid: str, *, outcome: str, now: datetime, tokens: dict | None = None, meta: dict | None = None,
        p: Path | None = None) -> None:
    """Record how an attempt came out."""
    append({"v": VERSION, "type": "end", "attempt": aid, "at": now.isoformat(), "outcome": outcome,
            "tokens": tokens or {}, "meta": meta or {}}, p)


def attempts(records: list[dict], now: datetime) -> list[dict]:
    """Each attempt, its start joined to its first end. A start with no end whose deadline has
    passed is a timeout at the deadline: a caller killed mid-attempt still counts. One still inside its
    deadline is left out."""
    ends: dict[str, dict] = {}
    for r in records:
        if r.get("type") == "end" and isinstance(r.get("attempt"), str):
            ends.setdefault(r["attempt"], r)
    out = []
    for r in records:
        if r.get("type") != "start" or not isinstance(r.get("attempt"), str):
            continue
        t0 = _time(r.get("at"))
        if t0 is None or not isinstance(r.get("provider"), str) or not isinstance(r.get("model"), str):
            continue
        deadline = r.get("deadline") if isinstance(r.get("deadline"), (int, float)) else None
        e = ends.get(r["attempt"])
        t1 = _time(e.get("at")) if e else None
        if e and (t1 is None or e.get("outcome") not in OUTCOMES):
            continue  # an end this reader does not understand: not evidence of a timeout
        if e:
            outcome, secs = e["outcome"], max((t1 - t0).total_seconds(), 0.0)
        elif deadline is not None and now - t0 > timedelta(seconds=deadline):
            outcome, secs = "timeout", float(deadline)
        else:
            continue
        tokens = e.get("tokens") if e and isinstance(e.get("tokens"), dict) else {}
        out.append({"provider": r["provider"], "model": r["model"], "effort": r.get("effort"),
                    "task": r.get("task", r.get("kind")), "at": t0, "outcome": outcome, "secs": secs, "tokens": tokens})
    return out


def _weight(at: datetime, now: datetime) -> float:
    return 0.5 ** (max((now - at).total_seconds(), 0.0) / HALF_LIFE.total_seconds())


def stats(done: list[dict], now: datetime) -> dict[tuple[str, str], dict]:
    """Per (provider, model): the decayed failure probability `p`, the expected seconds of a
    success `t_ok` (per effort and kind where it has its own history, else the model's) and the
    decayed mean seconds of a failure `t_fail` (None without one), and the weights behind them.
    `unavailable` is not the model's failure and is not counted."""
    # The spread of log-durations, pooled over every model, weighted as the per-model sums are.
    oks = [(_weight(a["at"], now), math.log(max(a["secs"], 1.0))) for a in done if a["outcome"] == "ok"]
    total = sum(w for w, _ in oks)
    if total > 1:
        mean = sum(w * x for w, x in oks) / total
        var = sum(w * (x - mean) ** 2 for w, x in oks) / (total - 1)
    else:
        var = 0.25
    out: dict[tuple[str, str], dict] = {}
    for a in done:
        if a["outcome"] == "unavailable":
            continue
        k = (a["provider"], a["model"])
        s = out.setdefault(k, {"ok": 0.0, "fail": 0.0, "log_ok": 0.0, "fail_secs": 0.0, "runs": 0,
                               "last": a["at"], "_tokens": []})
        s["runs"] += 1
        s["last"] = max(s["last"], a["at"])
        w = _weight(a["at"], now)
        if a["outcome"] == "ok":
            s["ok"] += w
            s["log_ok"] += w * math.log(max(a["secs"], 1.0))
            if all(isinstance(a["tokens"].get(x), int) for x in ("in", "out")) and a["secs"] > 0:
                s["_tokens"].append((a["tokens"], a["secs"]))
        else:
            s["fail"] += w
            s["fail_secs"] += w * a["secs"]
    for s in out.values():
        s["p"] = (A0 + s["fail"]) / (A0 + B0 + s["fail"] + s["ok"])
        mu = (N0 * MU0 + s["log_ok"]) / (N0 + s["ok"])
        s["t_ok"] = math.exp(mu + var / 2)
        s["t_fail"] = s["fail_secs"] / s["fail"] if s["fail"] > 0 else None
        # What a successful run spent, and how fast it wrote: the observed side of a model's card.
        runs = s.pop("_tokens")
        med = lambda xs: statistics.median(xs) if xs else None
        s["tokens"] = {x: med([t.get(x) for t, _ in runs if isinstance(t.get(x), int)]) for x in ("in", "out", "cache")}
        s["tok_s"] = med([t["out"] / secs for t, secs in runs])
        s["last"] = s["last"].isoformat()
        del s["log_ok"], s["fail_secs"]
    return out
