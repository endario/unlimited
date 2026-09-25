"""Which of a tier's candidates takes a round, by expected cost in minutes (docs/usage-routing/
phase-2a.md): how often each fails here, how long it takes, and what its quota costs."""

from __future__ import annotations

import math
import random
import statistics
import uuid
from datetime import datetime

from . import outcomes
from .catalog import Catalog

KAPPA = 5.0  # quota price steepness: exp(κ(ρ − 1))
Q = 20.0  # minutes per unit of quota price
TAU = 2.0  # finding rounds' sampling temperature, minutes
PREFER = 1.0  # minutes the first of tie_preference is worth; the rest less, in order


def price(rho: float | None) -> float:
    """What spending this account now costs, in units of a quota at its limit: near zero where the
    quota would expire unused, rising steeply past it. Unknown is priced as at the limit."""
    return 1.0 if rho is None else math.exp(KAPPA * (rho - 1.0))


def candidates(cat: Catalog, tier: str, providers: list[str], now: datetime) -> list[dict]:
    """Each provider's live promotions at `tier`, then its tier model, as the catalog has them."""
    listed = cat.candidates(tier, now)
    return [{"provider": c.provider, "model": c.model, "promoted": c.promoted}
            for p in providers for c in listed if c.provider == p]


def score(cands: list[dict], quota: dict[str, float], stats: dict, deadline: float,
          prefer: list[str]) -> list[dict]:
    """Each candidate with its `rho`, `pi`, `p`, `t_ok`, `t_fail` (minutes) and expected cost `e`."""
    out = []
    for c in cands:
        s = stats.get((c["provider"], c["model"]))
        p = s["p"] if s else outcomes.A0 / (outcomes.A0 + outcomes.B0)
        t_ok = (s["t_ok"] if s else math.exp(outcomes.MU0 + 0.125)) / 60
        # A failure costs its observed time to fail, with this round's deadline worth one attempt.
        fail_w, fail_secs = (s["fail"], (s["t_fail"] or 0) * s["fail"]) if s else (0.0, 0.0)
        t_fail = (deadline + fail_secs) / (1 + fail_w) / 60
        rho = None if c["promoted"] else quota.get(c["provider"])
        pi = 0.0 if c["promoted"] else price(rho)
        out.append({**c, "rho": rho, "pi": pi, "p": p, "t_ok": t_ok, "t_fail": t_fail})
    for c in out:
        others = [o["t_ok"] for o in out if o is not c]
        t_next = statistics.median(others) if others else c["t_ok"]
        bonus = PREFER * (len(prefer) - prefer.index(c["provider"])) / len(prefer) if c["provider"] in prefer else 0.0
        c["e"] = (1 - c["p"]) * c["t_ok"] + c["p"] * (c["t_fail"] + t_next) + Q * c["pi"] - bonus
    return out


def pick(scored: list[dict], kind: str, rng: random.Random) -> int:
    """Final rounds take the lowest expected cost; finding rounds sample, P ∝ exp(−e/τ), and each
    candidate's probability is set on it."""
    if kind == "final":
        best = min(range(len(scored)), key=lambda i: scored[i]["e"])
        for i, c in enumerate(scored):
            c["prob"] = 1.0 if i == best else 0.0
        return best
    low = min(c["e"] for c in scored)
    ws = [math.exp(-(c["e"] - low) / TAU) for c in scored]
    total = sum(ws)
    for c, w in zip(scored, ws):
        c["prob"] = w / total
    r, acc = rng.random() * total, 0.0
    for i, w in enumerate(ws):
        acc += w
        if r < acc:
            return i
    return len(scored) - 1


def choose(cat: Catalog, *, tier: str, kind: str, mode: str | None, providers: list[str],
           quota: dict[str, float], deadline: float, now: datetime,
           rng: random.Random | None = None, log=None) -> dict | None:
    """The decision, logged, or None when no provider has a model at `tier`."""
    cands = candidates(cat, tier, providers, now)
    if not cands:
        return None
    records, _ = outcomes.read(log)
    scored = score(cands, quota, outcomes.stats(outcomes.attempts(records, now), now), deadline,
                   cat.tie_preference)
    seed = random.randrange(1 << 32)  # logged: a sampled pick can be replayed
    i = pick(scored, kind, rng or random.Random(seed))
    decision = {"type": "decision", "decision": uuid.uuid4().hex[:16], "at": now.isoformat(), "tier": tier,
                "kind": kind, "mode": mode, "deadline": deadline, "seed": None if rng else seed,
                "candidates": scored, "pick": i}
    outcomes.append(decision, log)
    return decision
