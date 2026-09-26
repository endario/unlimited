"""Which of a tier's candidates to use for a task, by expected cost in minutes (docs/choice.md): how often each fails here, how long it takes, and what its quota costs. What the task
is stays the caller's: it arrives only as generic parameters and an opaque label."""

from __future__ import annotations

import math
import random
import statistics
import uuid
from datetime import datetime

from . import outcomes
from .catalog import Catalog

KAPPA = 5.0  # quota price steepness: exp(κ(ρ − 1))
QUOTA_WEIGHT = 20.0  # default minutes one unit of quota price is worth
PREFER = 1.0  # minutes the first of tie_preference is worth; the rest less, in order


def price(rho: float | None) -> float:
    """What spending this account now costs, in units of a quota at its limit: near zero where the
    quota would expire unused, rising steeply past it. Unknown is priced as at the limit."""
    return 1.0 if rho is None else math.exp(KAPPA * (rho - 1.0))


def candidates(cat: Catalog, tier: str, providers: list[str], now: datetime) -> list[dict]:
    """Each allowed provider's live routes at `tier`, as the catalog lists them."""
    # A caller's quota is keyed by provider, and stands for the vendor in the one-vendor view; a
    # route on another vendor is priced as unknown (at the limit) until quota is keyed by route.
    view = cat.to_json(now)["providers"]
    return [{"provider": r["provider"], "model": r["id"], "vendor": r["vendor"], "promoted": r["free"],
             "debit": r["debit"], "quota_applies": r["vendor"] == view.get(r["provider"], {}).get("usage")}
            for p in providers for r in cat.routes(now, tier) if r["provider"] == p]


def score(cands: list[dict], quota: dict[str, float], stats: dict, deadline: float,
          prefer: list[str], quota_weight: float = QUOTA_WEIGHT) -> list[dict]:
    """Each candidate with its `rho`, `pi`, `p`, `t_ok`, `t_fail` (minutes) and expected cost `e`."""
    out = []
    for c in cands:
        s = stats.get((c["provider"], c["model"]))
        p = s["p"] if s else outcomes.A0 / (outcomes.A0 + outcomes.B0)
        t_ok = (s["t_ok"] if s else math.exp(outcomes.MU0 + 0.125)) / 60
        # A failure costs its observed time to fail, with this call's deadline worth one attempt.
        fail_w, fail_secs = (s["fail"], (s["t_fail"] or 0) * s["fail"]) if s else (0.0, 0.0)
        t_fail = (deadline + fail_secs) / (1 + fail_w) / 60
        # A caller's projection for the route itself, else for the provider (which stands for the
        # route on the provider's usual vendor only).
        rho = (None if c["promoted"] else quota[c["model"]] if c["model"] in quota
               else quota.get(c["provider"]) if c.get("quota_applies", True) else None)
        # A route that debits its account more for a run costs that much more of it.
        pi = 0.0 if c["promoted"] else c.get("debit", 1) * price(rho)
        out.append({**c, "rho": rho, "pi": pi, "p": p, "t_ok": t_ok, "t_fail": t_fail})
    for c in out:
        others = [o["t_ok"] for o in out if o is not c]
        t_next = statistics.median(others) if others else c["t_ok"]
        bonus = PREFER * (len(prefer) - prefer.index(c["provider"])) / len(prefer) if c["provider"] in prefer else 0.0
        c["e"] = (1 - c["p"]) * c["t_ok"] + c["p"] * (c["t_fail"] + t_next) + quota_weight * c["pi"] - bonus
    return out


def pick(scored: list[dict], temperature: float, rng: random.Random) -> int:
    """At temperature 0 the lowest expected cost; above it a sample, P ∝ exp(−e/τ) with τ in
    minutes, so a candidate that much worse is e times less likely. Each candidate's probability is
    set on it."""
    if temperature <= 0:
        best = min(range(len(scored)), key=lambda i: scored[i]["e"])
        for i, c in enumerate(scored):
            c["prob"] = 1.0 if i == best else 0.0
        return best
    low = min(c["e"] for c in scored)
    ws = [math.exp(-(c["e"] - low) / temperature) for c in scored]
    total = sum(ws)
    for c, w in zip(scored, ws):
        c["prob"] = w / total
    r, acc = rng.random() * total, 0.0
    for i, w in enumerate(ws):
        acc += w
        if r < acc:
            return i
    return len(scored) - 1


def choose(cat: Catalog, *, tier: str, providers: list[str], quota: dict[str, float], deadline: float,
           now: datetime, temperature: float = 0.0, quota_weight: float = QUOTA_WEIGHT,
           task: str | None = None, meta: dict | None = None, exclude: list[str] | None = None,
           rng: random.Random | None = None, log=None) -> dict | None:
    """The decision, logged with the whole request and the whole result, or None when no provider
    has a model at `tier`. `task` and `meta` are recorded, never read."""
    if not (math.isfinite(temperature) and temperature >= 0 and math.isfinite(quota_weight) and quota_weight >= 0
            and math.isfinite(deadline) and deadline > 0):
        raise ValueError("temperature and quota weight must be finite and not negative, the deadline positive")
    cands = [c for c in candidates(cat, tier, providers, now) if c["model"] not in (exclude or [])]
    if not cands:
        return None
    records, _ = outcomes.read(log)
    scored = score(cands, quota, outcomes.stats(outcomes.attempts(records, now), now), deadline,
                   cat.tie_preference, quota_weight)
    seed = random.randrange(1 << 32)  # logged: a sampled pick can be replayed
    i = pick(scored, temperature, rng or random.Random(seed))
    request = {"tier": tier, "providers": providers, "quota": quota, "deadline": deadline,
               "temperature": temperature, "quota_weight": quota_weight, "task": task, "meta": meta or {},
               "exclude": exclude or []}
    decision = {"v": outcomes.VERSION, "type": "decision", "decision": uuid.uuid4().hex[:16],
                "at": now.isoformat(), "request": request, "seed": None if rng else seed,
                "candidates": scored, "pick": i}
    outcomes.append(decision, log)
    return decision
