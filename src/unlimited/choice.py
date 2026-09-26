"""Which of a tier's candidates to use for a task, by expected cost in minutes (docs/choice.md): how often each fails here, how long it takes, and what its quota costs. What the task
is stays the caller's: it arrives only as generic parameters and an opaque label."""

from __future__ import annotations

import math
import random
import statistics
import uuid
from collections.abc import Collection
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


def named(cat: Catalog, tier: str, names: list[str], now: datetime) -> list[dict]:
    """The live routes the caller names, each once: a provider's routes at `tier`, a model's routes
    at any tier, or one route by its offering id. A name the catalog does not know names none."""
    # A caller's quota keyed by provider stands for the vendor in the one-vendor view; a route on
    # another vendor is priced as unknown (at the limit) unless its own id is given a quota.
    view = cat.to_json(now)["providers"]
    makers = {m["provider"] for m in cat.models.values()}
    live, at_tier = cat.routes(now), cat.routes(now, tier)
    out: dict[str, dict] = {}
    for n in names:
        for r in ([r for r in at_tier if r["provider"] == n] if n in makers
                  else [r for r in live if n in (r["id"], r["model"])]):
            out.setdefault(r["id"], {"provider": r["provider"], "model": r["id"], "vendor": r["vendor"],
                                     "promoted": r["free"], "debit": r["debit"],
                                     "quota_applies": r["vendor"] == view.get(r["provider"], {}).get("usage")})
    return list(out.values())


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


def order(scored: list[dict], temperature: float, rng: random.Random) -> list[int]:
    """Every candidate's index, in the order to try. At temperature 0 by expected cost, lowest
    first; above it sampled without replacement, P ∝ exp(−e/τ) with τ in minutes, so a candidate
    that much worse is e times less likely at each draw. Each candidate's odds of being first are
    set on it as `prob`."""
    if temperature <= 0:
        out = sorted(range(len(scored)), key=lambda i: scored[i]["e"])
        for i, c in enumerate(scored):
            c["prob"] = 1.0 if i == out[0] else 0.0
        return out
    low = min(c["e"] for c in scored)
    ws = [math.exp(-(c["e"] - low) / temperature) for c in scored]
    for c, w in zip(scored, ws):
        c["prob"] = w / sum(ws)
    left, out = list(range(len(scored))), []
    while left:
        r, acc = rng.random() * sum(ws[i] for i in left), 0.0
        for i in left:
            acc += ws[i]
            if r < acc:
                break
        out.append(i)
        left.remove(i)
    return out


def vendors_here(cat: Catalog) -> set[str]:
    """The catalog's vendors a use on this machine could spend: each with an account found here
    (credentials only, no network), and each unlimited has no reader for, which it cannot rule out."""
    from .adapters import REGISTRY
    here = set()
    for v in {o["vendor"] for o in cat.offerings}:
        try:
            if v not in REGISTRY or REGISTRY[v].discover():
                here.add(v)
        except Exception:
            here.add(v)  # a reader that fails says nothing about the account
    return here


def rank(cat: Catalog, *, tier: str, candidates: list[str], attempts: list[dict], quota: dict[str, float],
         deadline: float, now: datetime, temperature: float = 0.0, quota_weight: float = QUOTA_WEIGHT,
         task: str | None = None, meta: dict | None = None, exclude: dict[str, str] | None = None,
         vendors: Collection[str] | None = None, rng: random.Random | None = None) -> dict | None:
    """The decision over `attempts` (as `outcomes.attempts` gives them), reading and writing
    nothing: the whole request, every candidate scored, `order` (indices, the order to try) and
    `pick` (its first). None when no named candidate is live. `exclude` maps a route's id to the
    caller's reason for ruling it out; reasons, `task` and `meta` are recorded, never read. With
    `vendors`, a route on any other vendor is not a candidate (`vendors_here` gives this machine's)."""
    if not (math.isfinite(temperature) and temperature >= 0 and math.isfinite(quota_weight) and quota_weight >= 0
            and math.isfinite(deadline) and deadline > 0):
        raise ValueError("temperature and quota weight must be finite and not negative, the deadline positive")
    exclude = exclude or {}
    cands = [c for c in named(cat, tier, candidates, now)
             if c["model"] not in exclude and (vendors is None or c["vendor"] in vendors)]
    if not cands:
        return None
    scored = score(cands, quota, outcomes.stats(attempts, now), deadline, cat.tie_preference, quota_weight)
    seed = random.randrange(1 << 32)  # recorded: a sampled order can be replayed
    tried = order(scored, temperature, rng or random.Random(seed))
    request = {"tier": tier, "candidates": candidates, "quota": quota, "deadline": deadline,
               "temperature": temperature, "quota_weight": quota_weight, "task": task, "meta": meta or {},
               "exclude": exclude, "vendors": None if vendors is None else sorted(vendors)}
    return {"v": outcomes.VERSION, "type": "decision", "decision": uuid.uuid4().hex[:16], "at": now.isoformat(),
            "request": request, "seed": None if rng else seed, "candidates": scored, "order": tried,
            "pick": tried[0]}


def choose(cat: Catalog, *, tier: str, candidates: list[str], quota: dict[str, float], deadline: float,
           now: datetime, temperature: float = 0.0, quota_weight: float = QUOTA_WEIGHT,
           task: str | None = None, meta: dict | None = None, exclude: dict[str, str] | None = None,
           vendors: Collection[str] | None = None, rng: random.Random | None = None, log=None) -> dict | None:
    """`rank` over unlimited's attempt log, the decision appended to it."""
    records, _ = outcomes.read(log)
    decision = rank(cat, tier=tier, candidates=candidates, attempts=outcomes.attempts(records, now), quota=quota,
                    deadline=deadline, now=now, temperature=temperature, quota_weight=quota_weight, task=task,
                    meta=meta, exclude=exclude, vendors=vendors, rng=rng)
    if decision is not None:
        outcomes.append(decision, log)
    return decision
