"""The per-home cache. One file and one flock per vendor: the lock is held across the upstream
read, so a second process asking for the same vendor waits for the first one's answer instead of
asking again. Different vendors never wait on each other."""

from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from . import projection
from .schema import iso, moment, settled


def default_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "unlimited"


def _age(r: dict, now: datetime) -> float | None:
    t = moment(r.get("taken_at"))
    return (now - t).total_seconds() if t is not None else None


def _backing_off(r: dict, now: datetime) -> bool:
    until = moment(r.get("retry_until"))
    return until is not None and until > now


def _write(path: Path, readings: list[dict], history: dict) -> None:
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"readings": readings, "history": history}, f)
    os.replace(tmp, path)


def _newer(a: dict | None, b: dict | None) -> dict | None:
    if a is None or b is None:
        return a or b
    ta, tb = moment(a.get("taken_at")), moment(b.get("taken_at"))
    return b if ta is None or (tb is not None and tb > ta) else a


# A failure that says nothing about the account: the last good reading stays, keeping its own
# `taken_at` so a consumer can see how old it is. An auth refusal is news and replaces it.
# A throttle or a server fault is the same kind of failure; it also holds off every caller, for at
# least MIN_BACKOFF, since Anthropic's usage endpoint answers 429 with `Retry-After: 0`.
TRANSIENT = frozenset({"unreachable", "not-json", "not-an-object"})
MIN_BACKOFF = timedelta(minutes=5)


def _throttled(r: dict) -> bool:
    why = r.get("why") or ""
    return why == "http-429" or why.startswith("http-5")


def through(adapter, *, max_age: float, clock, get, directory: Path | None = None) -> list[dict]:
    """This vendor's readings: for each account, the newest of the cached reading and any local
    source the adapter has, asking upstream only when neither is younger than `max_age`."""
    directory = directory or default_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / f"{adapter.VENDOR}.json"
    with open(directory / f"{adapter.VENDOR}.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        now = clock()  # after the wait: a reader that queued behind a fresh read sees it as fresh
        try:
            body = json.loads(path.read_text())
            held, history = body.get("readings"), body.get("history")
        except (OSError, ValueError, AttributeError):
            held, history = None, None
        history = history if isinstance(history, dict) else {}
        cached = {r.get("account"): r for r in held or [] if isinstance(r, dict)}
        local = {}
        for r in getattr(adapter, "local", lambda now: [])(now):
            local[r["account"]] = _newer(local.get(r["account"]), r)
        out = []
        for cred in adapter.discover():
            prior = cached.get(cred.account)
            best = _newer(prior if prior and prior.get("status") == "ok" else None,
                          local.get(cred.account))
            age = _age(best, now) if best else None
            # A refusal's deadline binds every caller, whatever --max-age it asked for, and stays
            # with whichever reading is kept.
            if best is not None and prior is not None and _backing_off(prior, now):
                best = dict(best, retry_until=prior["retry_until"])
            if best is not None and age is not None and 0 <= age < max_age:
                # A local source names no plan; the account's plan does not change with its source.
                if best.get("plan") is None and prior is not None and prior.get("plan") is not None:
                    best = dict(best, plan=prior["plan"])
                out.append(best)
            elif prior is not None and _backing_off(prior, now):
                out.append(best or prior)
            else:
                got = adapter.read(cred, now, get)
                if got["status"] != "ok" and _throttled(got):
                    until = moment(got.get("retry_until"))
                    got = dict(got, retry_until=iso(max(until or now, now + MIN_BACKOFF)))
                if got["status"] != "ok" and best is not None and got.get("why") in TRANSIENT:
                    got = best
                elif got["status"] != "ok" and best is not None and _throttled(got):
                    # The last good reading stands, carrying the deadline before anyone asks again.
                    got = dict(best, retry_until=got["retry_until"])
                out.append(got)
        history = projection.prune(projection.record(history, out), now)
        _write(path, out, history)
        return [projection.attach(settled(r, now), history) for r in out]
