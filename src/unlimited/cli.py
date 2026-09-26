"""`unlimited` (a table for people), `unlimited read [--vendor V]... [--max-age S] --json`,
`unlimited models [--tier T] [--provider P] [--json]`,
`unlimited off [TARGET [--for D] [--why W]]`, `unlimited on TARGET`,
`unlimited attempt start|end ...`, `unlimited outcomes [--json]`, `unlimited choose ... --json`,
`unlimited cards [--tier T] [--json]`, `unlimited verdict --work S [--model-scope M] --json`,
`unlimited capture claude-statusline`."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

from . import cache, transport
from .adapters import REGISTRY


def _version() -> str:
    from importlib import metadata
    try:
        return metadata.version("unlimited")
    except metadata.PackageNotFoundError:
        return "unknown"  # run from a source tree, not installed


def _models(a) -> int:
    from . import catalog
    try:
        cat = catalog.load()
    except catalog.CatalogError as e:
        print(f"unlimited: catalog: {e}", file=sys.stderr)
        return 2
    if a.catalog:
        json.dump(cat.to_json(datetime.now(timezone.utc)), sys.stdout)
        return 0
    if a.provider:
        model = cat.model(a.provider, a.tier)
        if model is None:
            print(f"unlimited: {a.provider} has no model at {a.tier}", file=sys.stderr)
            return 1
        print(model)
        return 0
    got = cat.candidates(a.tier, datetime.now(timezone.utc))
    if a.json:
        json.dump([{"provider": c.provider, "model": c.model, "promoted": c.promoted} for c in got], sys.stdout)
    else:
        for c in got:
            print(f"{c.provider:<10} {c.model}" + ("  (promotion)" if c.promoted else ""))
    return 0


def _duration(text: str) -> timedelta:
    units = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    try:
        n, unit = float(text[:-1]), units[text[-1]]
    except (ValueError, KeyError, IndexError):
        raise argparse.ArgumentTypeError(f"{text!r}: expected a number and m, h, d or w, e.g. 90m, 1d")
    if not 0 < n < 1e6:
        raise argparse.ArgumentTypeError(f"{text!r}: must be positive and finite")
    return timedelta(seconds=n * units[text[-1]])


def _switch(a) -> int:
    """Switch a provider, a model or a `provider:model` pair off (or back on) on this machine."""
    from . import catalog
    try:
        cat = catalog.load()
        now = datetime.now(timezone.utc)
        off = [x for x in cat.off if (u := catalog.moment_utc(x.get("until"))) is None or u > now]
    except catalog.CatalogError as e:
        print(f"unlimited: catalog: {e}", file=sys.stderr)
        return 2
    if a.cmd == "off" and a.target is None:
        for x in off:
            print(f"{x['target']:<48} {'until ' + x['until'] if x.get('until') else 'until switched on'}"
                  + (f"  ({x['why']})" if x.get("why") else ""))
        return 0
    provider, _, model = a.target.partition(":")
    routes = [cat.route(o["id"]) for o in cat.offerings]
    pairs = {(r["provider"], x) for r in routes for x in (r["id"], r["model"])}
    names = {x for r in routes for x in (r["provider"], r["model"], r["vendor"], r["id"])}
    known = (provider, model) in pairs if model else provider in names
    if not known:
        print(f"unlimited: {a.target}: not a provider, model, vendor, offering or provider:model pair in the "
              f"catalog", file=sys.stderr)
        return 1
    kept = [x for x in off if x["target"] != a.target]
    if a.cmd == "on":
        if len(kept) == len(off):
            print(f"unlimited: {a.target} is not switched off here", file=sys.stderr)
            return 1
    else:
        kept.append({"target": a.target, "until": (now + a.for_).isoformat() if a.for_ else None,
                     "why": a.why})
    catalog.write_switches(kept)
    return 0


def _attempt(a) -> int:
    from . import outcomes
    now = datetime.now(timezone.utc)
    if a.phase == "start":
        outcomes.compact(now)
        print(outcomes.start(provider=a.provider, model=a.model, effort=a.effort, task=a.task,
                             account=a.account, decision=a.decision, deadline=a.deadline, now=now,
                             offering=a.offering,
                             meta=dict(a.meta or [])))
    else:
        tokens = {k: v for k, v in (("in", a.tokens_in), ("out", a.tokens_out), ("cache", a.tokens_cache))
                  if v is not None}
        outcomes.end(a.id, outcome=a.outcome, now=now, tokens=tokens, meta=dict(a.meta or []))
    return 0


def _outcomes(a) -> int:
    from . import outcomes
    now = datetime.now(timezone.utc)
    records, bad = outcomes.read()
    got = outcomes.stats(outcomes.attempts(records, now), now)
    if a.json:
        json.dump([{"provider": p, "model": m, **s} for (p, m), s in sorted(got.items())], sys.stdout)
    else:
        for (p, m), s in sorted(got.items()):
            fail = f"{s['t_fail'] / 60:5.1f}m" if s["t_fail"] is not None else "    -"
            print(f"{p:<9} {m:<42} fail {s['p']:4.0%}  ok {s['t_ok'] / 60:5.1f}m  failed {fail}"
                  f"  (ok {s['ok']:.1f}, fail {s['fail']:.1f})")
    if bad:
        print(f"unlimited: skipped {bad} unreadable line(s) in {outcomes.path()}", file=sys.stderr)
    return 0


def _cards(a) -> int:
    """Each route's card: what its vendor publishes (expected) beside what its runs here show
    (observed)."""
    from . import catalog, outcomes
    now = datetime.now(timezone.utc)
    try:
        cat = catalog.load()
    except catalog.CatalogError as e:
        print(f"unlimited: catalog: {e}", file=sys.stderr)
        return 2
    records, _ = outcomes.read()
    seen = outcomes.stats(outcomes.attempts(records, now), now)
    routes: dict[tuple[str, str], list[str]] = {}
    for t in [a.tier] if a.tier else cat.tiers:
        for c in cat.candidates(t, now):
            routes.setdefault((c.provider, c.model), []).append(t + (" promotion" if c.promoted else ""))
    out = []
    for (p, m), tiers in routes.items():
        card, own = cat.card(p, m)
        s = seen.get((p, m))
        cost = None
        if card and s and card.get("price") and s["tokens"]["out"] is not None:
            price, t = card["price"], s["tokens"]
            cost = sum((t.get(x) or 0) * price.get(k, 0) for x, k in (("in", "input"), ("out", "output"),
                                                                        ("cache", "cache_read"))) / 1e6
        out.append({"provider": p, "model": m, "tiers": tiers,
                    "expected": dict(card, as_of=card["as_of"].isoformat(), own=own) if card else None,
                    "observed": dict(s, cost_per_run=cost) if s else None})
    if a.json:
        json.dump(out, sys.stdout)
        return 0
    for r in out:
        print(f"{r['provider']} {r['model']} ({', '.join(r['tiers'])})")
        e = r["expected"]
        if e is None:
            print("  expected  nothing published on file")
        else:
            price = e.get("price") or {}
            figures = [f"intelligence {e['intelligence']}" if "intelligence" in e else "not scored",
                       f"{e['tok_s']:g} tok/s" if "tok_s" in e else None,
                       "$" + "/".join(f"{price[k]:g}" for k in ("input", "output", "cache_read") if k in price)
                       + " per M in/out/cache" if price else None]
            whose = f"{e['vendor']}{' ' + e['plan'] if e.get('plan') else ''}, {e['as_of']}"
            print(f"  expected  {' · '.join(x for x in figures if x)}  ({whose}"
                  f"{'' if e['own'] else '; another vendor, a guideline only'})")
        o = r["observed"]
        if o is None:
            print("  observed  no runs here yet")
        else:
            fail = f", failed in {o['t_fail'] / 60:.1f}m" if o["t_fail"] is not None else ""
            tok = (f", {o['tokens']['out'] / 1000:.1f}k out ({o['tok_s']:.0f} tok/s over the run)"
                   if o["tokens"]["out"] is not None else "")
            cost = f", ${o['cost_per_run']:.3f}/run at that price" if o["cost_per_run"] is not None else ""
            print(f"  observed  {o['runs']} run(s), fail {o['p']:.0%}, ok in {o['t_ok'] / 60:.1f}m{fail}{tok}{cost}")
    return 0


def _choose(a) -> int:
    from . import catalog, choice, outcomes
    now = datetime.now(timezone.utc)
    try:
        cat = catalog.load()
        quota = {}
        for x in (x for x in a.quota.split(",") if x):
            p, eq, r = x.partition("=")
            if not eq:
                raise ValueError(f"--quota {x!r}: expected <provider>=<projected use>")
            quota[p] = float(r)
    except (catalog.CatalogError, ValueError) as e:
        print(f"unlimited: {e}", file=sys.stderr)
        return 2
    outcomes.compact(now)
    if a.tier not in cat.tiers:
        print(f"unlimited: {a.tier}: not a tier of the catalog ({', '.join(cat.tiers)})", file=sys.stderr)
        return 2
    try:
        got = choice.choose(cat, tier=a.tier, providers=list(dict.fromkeys(p for p in a.candidates.split(",") if p)),
                            quota=quota, deadline=a.deadline, now=now, temperature=a.temperature,
                            quota_weight=a.quota_weight, task=a.task, meta=dict(a.meta or []),
                            exclude=[x for x in a.exclude.split(",") if x])
    except ValueError as e:
        print(f"unlimited: {e}", file=sys.stderr)
        return 2
    if got is None:
        print(f"unlimited: no candidate has a model at {a.tier}", file=sys.stderr)
        return 1
    json.dump(got, sys.stdout)
    return 0


def _meta(text: str) -> tuple[str, str]:
    key, eq, value = text.partition("=")
    if not eq or not key:
        raise argparse.ArgumentTypeError(f"{text!r}: expected KEY=VALUE")
    return key, value


META_HELP = "KEY=VALUE, repeatable: the caller's own metadata, recorded and never read"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="unlimited")
    p.add_argument("--version", action="store_true", help="print the installed release")
    sub = p.add_subparsers(dest="cmd")
    st = sub.add_parser("status", help="usage per account, for people (the default)")
    for q in (p, st):
        q.add_argument("--vendor", action="append", choices=sorted(REGISTRY))
        q.add_argument("--max-age", type=float, default=300.0)
        q.add_argument("--all", action="store_true", help="every limit the vendor reports")
    r = sub.add_parser("read", help="print readings")
    r.add_argument("--vendor", action="append", choices=sorted(REGISTRY))
    r.add_argument("--max-age", type=float, default=300.0)
    r.add_argument("--json", action="store_true", help="JSON output (the only format for now)")
    m = sub.add_parser("models", help="what each provider runs at a tier, promotions first")
    m.add_argument("--tier", default="standard", help="one of the catalog's tiers")
    m.add_argument("--provider", help="print only this provider's model at the tier")
    m.add_argument("--json", action="store_true")
    m.add_argument("--catalog", action="store_true", help="the whole merged catalog, as JSON")
    of = sub.add_parser("off", help="switch a provider, model or provider:model off on this machine; "
                                    "with no target, list what is off")
    of.add_argument("target", nargs="?")
    of.add_argument("--for", dest="for_", type=_duration, help="lapse after this long (90m, 12h, 1d, 1w)")
    of.add_argument("--why", help="a note, shown by `unlimited off`")
    on = sub.add_parser("on", help="undo `unlimited off TARGET`")
    on.add_argument("target")
    at = sub.add_parser("attempt", help="record the start or end of one use of a model on this machine")
    ats = at.add_subparsers(dest="phase", required=True)
    st_ = ats.add_parser("start", help="prints the attempt id")
    st_.add_argument("--provider", required=True)
    st_.add_argument("--model", required=True)
    st_.add_argument("--offering", help="the catalog offering id launched, when it is not --model")
    st_.add_argument("--effort")
    st_.add_argument("--task", help="the caller's label for what the model is used for (recorded, never read)")
    st_.add_argument("--account")
    st_.add_argument("--decision")
    st_.add_argument("--deadline", type=float, required=True, help="seconds; past it with no end is a timeout")
    st_.add_argument("--meta", action="append", type=_meta, help=META_HELP)
    en = ats.add_parser("end")
    en.add_argument("id")
    en.add_argument("--outcome", required=True, choices=["ok", "timeout", "error", "unavailable"])
    en.add_argument("--tokens-in", type=int)
    en.add_argument("--tokens-out", type=int)
    en.add_argument("--tokens-cache", type=int)
    en.add_argument("--meta", action="append", type=_meta, help=META_HELP)
    oc = sub.add_parser("outcomes", help="each model's recent failure rate and durations on this machine")
    oc.add_argument("--json", action="store_true")
    cd = sub.add_parser("cards", help="each route's model card: its vendor's figures beside its runs here")
    cd.add_argument("--tier", help="one of the catalog's tiers")
    cd.add_argument("--json", action="store_true")
    ch = sub.add_parser("choose", help="which candidate to use for a task, by expected cost; logged")
    ch.add_argument("--tier", required=True, help="one of the catalog's tiers")
    ch.add_argument("--candidates", required=True, help="providers the caller allows, comma-separated")
    ch.add_argument("--quota", default="", help="<offering id or provider>=<projected use at reset>,...: an offering id prices that route; "
                         "a provider, its routes on its usual vendor")
    ch.add_argument("--deadline", type=float, required=True, help="seconds after which a use counts as failed")
    ch.add_argument("--temperature", type=float, default=0.0,
                    help="minutes: 0 takes the lowest expected cost; above it, a candidate that many "
                         "minutes worse is e times less likely to be sampled")
    ch.add_argument("--quota-weight", type=float, default=20.0,
                    help="minutes one unit of quota price is worth (default 20)")
    ch.add_argument("--exclude", default="", help="offering ids the caller rules out, comma-separated")
    ch.add_argument("--task", help="the caller's label for the task (recorded, never read)")
    ch.add_argument("--meta", action="append", type=_meta, help=META_HELP)
    ch.add_argument("--json", action="store_true", help="JSON output (the only format)")
    vd = sub.add_parser("verdict", help="whether each account can take a unit of work, as JSON")
    vd.add_argument("--vendor", action="append", choices=sorted(REGISTRY))
    vd.add_argument("--model-scope", default=None, help="the model family the work runs (e.g. Opus)")
    vd.add_argument("--work", type=float, required=True, help="expected duration, seconds")
    vd.add_argument("--max-age", type=float, default=300.0)
    vd.add_argument("--json", action="store_true", help="JSON output (the only format for now)")
    c = sub.add_parser("capture", help="save a tool's own usage report (never prints)")
    c.add_argument("source", choices=["claude-statusline"])
    a = p.parse_args(argv)
    if a.version:
        # Looked up only when asked: `capture` must never fail on a broken install's metadata.
        print(f"unlimited {_version()}")
        return 0
    if a.cmd == "capture":
        # Runs inside the statusline chain: it must never print, block or fail the line.
        try:
            from .adapters import anthropic
            anthropic.capture(json.load(sys.stdin), datetime.now(timezone.utc))
        except Exception:
            pass
        return 0
    if a.cmd == "models":
        return _models(a)
    if a.cmd in ("off", "on"):
        return _switch(a)
    if a.cmd == "attempt":
        return _attempt(a)
    if a.cmd == "outcomes":
        return _outcomes(a)
    if a.cmd == "choose":
        return _choose(a)
    if a.cmd == "cards":
        return _cards(a)
    out = []
    for v in getattr(a, "vendor", None) or sorted(REGISTRY):
        out += cache.through(REGISTRY[v], max_age=a.max_age,
                             clock=lambda: datetime.now(timezone.utc), get=transport.get)
    if a.cmd == "verdict":
        from .verdict import verdict
        now = datetime.now(timezone.utc)
        json.dump([{"vendor": r.get("vendor"), "account": r.get("account"), "names": r.get("names", []),
                    "verdict": verdict(r, model_scope=a.model_scope, now=now, work=timedelta(seconds=a.work),
                                       max_age=timedelta(seconds=a.max_age))} for r in out], sys.stdout)
        return 0
    if a.cmd in (None, "status"):
        from .show import render
        sys.stdout.write(render(out, datetime.now(timezone.utc), color=sys.stdout.isatty(),
                                all_limits=a.all))
        return 0
    json.dump(out, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
