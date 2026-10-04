"""`unlimited` (a table for people), `unlimited read [--vendor V]... [--max-age S] --json`,
`unlimited models [--tier T] [--provider P] [--json]`,
`unlimited off [TARGET [--for D] [--why W]]`, `unlimited on TARGET`,
`unlimited attempt start|end ...`, `unlimited outcomes [--json]`, `unlimited choose ... --json`,
`unlimited cards [--tier T] [--json]`,
`unlimited verdict --work S [--offering ID] [--model-scope M] --json`,
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
    """Switch a provider, a model, a `provider:model` pair, or (with --account) one account of a
    usage vendor off (or back on) on this machine."""
    from . import catalog
    try:
        cat = catalog.load()
        now = datetime.now(timezone.utc)
    except catalog.CatalogError as e:
        print(f"unlimited: catalog: {e}", file=sys.stderr)
        return 2
    if a.cmd == "off" and a.target is None:
        off = [x for x in cat.off if catalog.live(x, now)]
        if a.json:
            json.dump(off, sys.stdout)
            return 0
        for x in off:
            what = x["target"] if x.get("account") is None else f"{x['target']}/{x['account']}"
            print(f"{what:<48} {'until ' + x['until'] if x.get('until') else 'until switched on'}"
                  + (f"  ({x['why']})" if x.get("why") else ""))
        return 0
    account = getattr(a, "account", None)
    known = catalog.target_known(cat, a.target, account=account)
    if not known:
        why = (f"unlimited: --account applies only to a usage vendor ({', '.join(sorted(REGISTRY))})"
               if account is not None else
               f"unlimited: {a.target}: not a provider, model, offering, usage vendor or "
               f"provider:model pair in the catalog")
        print(why, file=sys.stderr)
        return 1
    try:
        wrote = catalog.switch(a.target, on=a.cmd == "on",
                               until=(now + a.for_) if getattr(a, "for_", None) else None,
                               why=getattr(a, "why", None), account=account, now=now)
    except catalog.NotOff as e:
        print(f"unlimited: {e}: not switched off here", file=sys.stderr)
        return 1
    except catalog.CatalogError as e:
        print(f"unlimited: catalog: {e}", file=sys.stderr)
        return 2
    if a.json:
        json.dump(wrote, sys.stdout)
    return 0


def _warn_unrouted(provider: str, route: str) -> None:
    """An attempt naming no route of the catalog teaches the choice nothing; it is still recorded,
    as asked, but the caller hears it now rather than as a count in later decisions."""
    from . import catalog
    try:
        cat = catalog.load()
    except catalog.CatalogError:
        return
    ids = sorted(o["id"] for o in cat.offerings if cat.models[o["model"]]["provider"] == provider)
    if route not in ids:
        print(f"unlimited: {provider} {route}: no catalog route, so no choice learns from this attempt; "
              f"{provider}'s routes: {', '.join(ids) or 'none'}", file=sys.stderr)


def _attempt(a) -> int:
    from . import outcomes
    now = datetime.now(timezone.utc)
    if a.phase == "start":
        _warn_unrouted(a.provider, a.offering or a.model)
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


def _incentive(a) -> int:
    from . import catalog, incentives
    now = datetime.now(timezone.utc)
    try:
        cat = catalog.load()
        if a.target is None:
            if a.factor is not None:
                raise ValueError("FACTOR needs TARGET")
            groups = incentives.read(now=now)
            if a.json:
                json.dump(groups, sys.stdout)
            else:
                for group in groups:
                    target = group["target"] + (f"/{group['account']}" if group.get("account") else "")
                    expiry = (group.get("until") or ", ".join(
                        f"{b['vendor']}/{b['account']} until {b['until']}" for b in group.get("bindings", [])))
                    print(f"{target:<48} {group['multiplier']:g}x  {expiry}")
            return 0
        if a.factor is None:
            raise ValueError("TARGET needs FACTOR")
        account = incentives.account_context(cat, a.target, a.account)
        if a.factor == "off":
            groups = incentives.clear_incentive(cat, a.target, account=account, now=now)
        else:
            if not incentives.valid_target(cat, a.target):
                raise ValueError(f"{a.target}: not a provider, model, offering, usage vendor or provider:model pair in the catalog")
            factor = incentives.parse_multiplier(a.factor)
            readings = None
            if a.for_ is None:
                # Reset expiry needs live account windows; an explicit duration never reads a vendor.
                vendors = {cat._route(o)["vendor"] for o in cat.offerings
                           if a.target in cat.route_names(cat._route(o))}
                if a.target in REGISTRY:
                    vendors.add(a.target)
                readings = []
                for vendor in sorted(vendors):
                    if vendor in REGISTRY:
                        readings += cache.through(REGISTRY[vendor], max_age=300,
                                                  clock=lambda: datetime.now(timezone.utc), get=transport.get)
            saved = incentives.set_incentive(cat, a.target, factor, now=now, duration=a.for_, account=account,
                                            readings=readings)
            if saved.get("bindings") is not None or saved.get("account") is not None:
                print("unlimited: account-bound steering needs choose --account OFFERING=ACCOUNT "
                      "(or rank accounts=); this scope is not applied without a binding", file=sys.stderr)
            groups = incentives.read(now=now)
    except (catalog.CatalogError, ValueError) as e:
        print(f"unlimited: {e}", file=sys.stderr)
        return 2 if isinstance(e, catalog.CatalogError) else 1
    if a.json:
        json.dump(groups, sys.stdout)
    return 0


def _choose(a) -> int:
    from . import catalog, choice, incentives, outcomes
    now = datetime.now(timezone.utc)
    try:
        cat = catalog.load()
        quota = {}
        for x in (x for x in a.quota.split(",") if x):
            p, eq, r = x.partition("=")
            if not eq:
                raise ValueError(f"--quota {x!r}: expected <provider>=<projected use>")
            quota[p] = float(r)
        prefer = {}
        for x in (x for x in a.prefer.split(",") if x):
            name, eq, m = x.partition("=")
            if not eq:
                raise ValueError(f"--prefer {x!r}: expected <name>=<minutes>")
            prefer[name] = float(m)
        accounts = {}
        for x in a.account or []:
            offering, eq, identity = x.partition("=")
            if not eq or not offering or not identity:
                raise ValueError(f"--account {x!r}: expected OFFERING=ACCOUNT")
            if cat.route(offering) is None:
                raise ValueError(f"--account {offering!r}: not an offering id in the catalog")
            accounts[offering] = incentives.account_binding(cat, offering, identity)
        groups = incentives.read(now=now)
    except (catalog.CatalogError, ValueError) as e:
        print(f"unlimited: {e}", file=sys.stderr)
        return 2
    outcomes.compact(now)
    if a.tier not in cat.tiers:
        print(f"unlimited: {a.tier}: not a tier of the catalog ({', '.join(cat.tiers)})", file=sys.stderr)
        return 2
    try:
        got = choice.choose(cat, tier=a.tier, candidates=list(dict.fromkeys(p for p in a.candidates.split(",") if p)),
                            quota=quota, deadline=a.deadline, now=now, temperature=a.temperature,
                            quota_weight=a.quota_weight, task=a.task, meta=dict(a.meta or []),
                            exclude=dict(x.partition("=")[::2] for x in a.exclude.split(",") if x),
                            prefer=prefer, incentives=groups, accounts=accounts,
                            vendors=(None if a.vendors == "any" else choice.vendors_here(cat) if a.vendors is None
                                     else {v for v in a.vendors.split(",") if v}))
    except ValueError as e:
        print(f"unlimited: {e}", file=sys.stderr)
        return 2
    if got is None:
        print(f"unlimited: no candidate has a model at {a.tier}", file=sys.stderr)
        return 1
    if got["incentives_unresolved"]:
        print("unlimited: incentives need account bindings: " + ", ".join(got["incentives_unresolved"]), file=sys.stderr)
    json.dump(got, sys.stdout)
    return 0


def _meta(text: str) -> tuple[str, str]:
    key, eq, value = text.partition("=")
    if not eq or not key:
        raise argparse.ArgumentTypeError(f"{text!r}: expected KEY=VALUE")
    return key, value


META_HELP = ("KEY=VALUE, repeatable: the caller's own metadata (a ticket, a job id), recorded with the "
             "record and never read by unlimited")
READ_HELP = ("seconds a cached reading may be before this reads the vendor again (default 300); "
             "0 always reads")
VENDOR_HELP = "only this vendor, repeatable (default: every vendor)"
FILES = """files:
  ~/.config/unlimited/catalog.toml   this machine's catalog additions (docs/catalog.md); optional
  ~/.config/unlimited/switches.json  what `unlimited off` switched off here
  ~/.config/unlimited/incentives.json
                                     operator multipliers and their expiry
  ~/.local/state/unlimited/decisions.jsonl
                                     the attempt and decision log (docs/choice.md)
  ~/.cache/unlimited/                readings, their history, and refreshed Grok tokens
  ($XDG_CONFIG_HOME, $XDG_STATE_HOME and $XDG_CACHE_HOME move them.)"""
DOCS = "https://github.com/endario/unlimited/blob/main/docs"


class _Help(argparse.RawDescriptionHelpFormatter):
    """Descriptions and examples as written; wider option columns."""

    def __init__(self, prog: str):
        super().__init__(prog, max_help_position=34, width=100)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="unlimited", formatter_class=_Help,
        description="""\
How much of each AI subscription is used, per window and account, and where each window is
heading; a catalog of the models each vendor sells; and a chooser that says which of the
candidates a caller allows is best to use now, from how each has done on this machine.

unlimited never knows what a caller uses a model for: a caller describes a task only through
generic parameters (a deadline, how much to explore, what quota is worth) and may attach its own
label and metadata, which are recorded and never read.

With no command, `unlimited` prints `status`.""",
        epilog=f"""\
`unlimited COMMAND --help` explains each. Python callers: unlimited.choice.rank / choose,
unlimited.verdict.verdict, unlimited.outcomes.start / end ({DOCS}/choice.md).

{FILES}

exit status: 0 done; 1 nothing to answer (no candidate, unknown target); 2 bad input or an
unreadable catalog.""")
    p.add_argument("--version", action="store_true", help="print the installed release and exit")
    sub = p.add_subparsers(dest="cmd", metavar="COMMAND", title="commands")

    def add(name: str, summary: str, description: str, epilog: str | None = None) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=summary, description=description, epilog=epilog, formatter_class=_Help)

    st = add("status", "usage per account, for people (the default)", """\
A block per account: a bar per usage window, how much of it is used, when it resets, and a
forecast line under it (where the window is heading at today's pace and past windows' shape).
Colour only when printing to a terminal.""", """\
examples:
  unlimited
  unlimited status --vendor anthropic --vendor openai --all""")
    for q in (p, st):
        q.add_argument("--vendor", action="append", choices=sorted(REGISTRY), metavar="VENDOR",
                       help=VENDOR_HELP + f": {', '.join(sorted(REGISTRY))}")
        q.add_argument("--max-age", type=float, default=300.0, metavar="SECONDS", help=READ_HELP)
        q.add_argument("--all", action="store_true", help="every limit the vendor reports, also those with no usage figure")
    r = add("read", "every account's reading, as JSON", f"""\
Prints a JSON array, one reading per account found on this machine (schema 1): its vendor,
account id, names, plan, status (`ok`, or why it could not be read), and each limit's window,
fraction used, reset, whether the vendor holds it, and a projection to the reset. unlimited
reports what the vendor says and never picks an account; that is the caller's. Credentials are
read where each tool keeps them and never written back (README: Sources).

Readings are cached per vendor; concurrent callers share one upstream read.
Format: https://github.com/endario/unlimited#readme""", """\
examples:
  unlimited read --json
  unlimited read --vendor opencode --max-age 0 --json""")
    r.add_argument("--vendor", action="append", choices=sorted(REGISTRY), metavar="VENDOR",
                   help=VENDOR_HELP + f": {', '.join(sorted(REGISTRY))}")
    r.add_argument("--max-age", type=float, default=300.0, metavar="SECONDS", help=READ_HELP)
    r.add_argument("--json", action="store_true", help="JSON output (the only format; accepted for clarity)")
    vd = add("verdict", "whether each account can take a unit of work, as JSON", f"""\
For each account, whether it can take a unit of work of --work seconds now: `unread` (no fresh
reading), `excluded` (the vendor stopped it, a window is used up, one runs out before the work
would finish, or the vendor is switched off here; with which window and when it lifts) or
`ranked`, with a `tier` (0: no window
projected past its limit; 1: one is, but after the work) and a raw quota `score`. Ranked results
also include source-local `preference`; compare tiers before its effective score. Each row carries
its annotated `steering` context. Ordering, tie rules and fallback are the caller's.
Details: {DOCS}/choice.md#1-verdict""", """\
examples:
  unlimited verdict --vendor anthropic --work 900 --model-scope Opus --json
  unlimited verdict --offering gpt-6.1-sol --work 900 --json""")
    vd.add_argument("--vendor", action="append", choices=sorted(REGISTRY), metavar="VENDOR",
                    help=VENDOR_HELP + f": {', '.join(sorted(REGISTRY))}")
    vd.add_argument("--work", type=float, required=True, metavar="SECONDS",
                    help="how long the unit of work is expected to take")
    vd.add_argument("--model-scope", default=None, metavar="MODEL",
                    help="the model family the work runs, for limits scoped to one (e.g. Opus); "
                         "without it, model-scoped limits are left out")
    vd.add_argument("--offering", default=None, metavar="ID",
                    help="actual catalog offering id for local steering; without --vendor, reads its "
                         "usage vendor only; distinct from the model-family limit scope")
    vd.add_argument("--max-age", type=float, default=300.0, metavar="SECONDS", help=READ_HELP)
    vd.add_argument("--json", action="store_true", help="JSON output (the only format; accepted for clarity)")
    m = add("models", "what each provider runs at a tier; the whole catalog with --catalog", f"""\
The model catalog: which models exist, which maker (provider) makes each, which tiers each
serves, and which vendors sell it (offerings, each with the id a caller launches). It ships
with unlimited; ~/.config/unlimited/catalog.toml adds to or replaces entries on this machine,
and `unlimited off` switches entries off. Format: {DOCS}/catalog.md

Without --catalog, prints each provider's live routes at --tier, promotions first.""", """\
examples:
  unlimited models                         # standard tier, one line per route
  unlimited models --tier heavy --json
  unlimited models --provider deepseek     # just its model id at the tier; exit 1 if none
  unlimited models --catalog               # the merged catalog as JSON""")
    m.add_argument("--tier", default="standard", metavar="TIER", help="one of the catalog's tiers (default standard)")
    m.add_argument("--provider", metavar="PROVIDER", help="print only this provider's model id at the tier")
    m.add_argument("--json", action="store_true", help="a JSON array of {provider, model, promoted}")
    m.add_argument("--catalog", action="store_true",
                   help="the whole merged catalog as JSON: tiers, models, live offerings, cards, and a "
                        "one-vendor-per-provider view (providers, promotions)")
    cd = add("cards", "each route's published figures beside its runs here", """\
For each live route: what its vendor publishes (intelligence score, tokens per second, price per
million tokens, from the catalog's cards; another vendor's card for the same model is shown as a
guideline only) beside what its runs on this machine show (runs, failure rate, time to succeed or
fail, tokens, pace, and the cost of a typical run at the card's price).""", """\
examples:
  unlimited cards --tier standard
  unlimited cards --json""")
    cd.add_argument("--tier", metavar="TIER", help="only the routes at this tier (default: every tier)")
    cd.add_argument("--json", action="store_true", help="a JSON array of {provider, model, tiers, expected, observed}")
    of = add("off", "switch a provider, model, vendor or route off here; list what is off", """\
Switches a catalog entry off on this machine, so it is never a candidate, until switched on or
until --for lapses. TARGET is a provider, a model, a vendor (by its usage name, also one with no
catalog route here), an offering id, or PROVIDER:MODEL. The shipped catalog is untouched; the
switch lives in ~/.config/unlimited/switches.json. With no TARGET, lists what is off and until
when.""", """\
examples:
  unlimited off                                         # what is off
  unlimited off commandcode --for 12h --why "overloaded"
  unlimited off zai --account claude-glm-2              # one account, not the vendor
  unlimited off claude:opus
  unlimited on commandcode""")
    of.add_argument("target", nargs="?", metavar="TARGET", help="what to switch off (omit to list)")
    of.add_argument("--account", metavar="NAME",
                    help="switch off one account of a usage vendor TARGET, not the vendor: its "
                         "account id (`unlimited read`) or an identity name (claude-glm-2); a "
                         "name no account here answers to is accepted and matches nothing")
    of.add_argument("--json", action="store_true", help="a JSON array of what is off (after any change)")
    of.add_argument("--for", dest="for_", type=_duration, metavar="DURATION",
                    help="lapse after this long: a number and m, h, d or w (90m, 12h, 1d, 1w); default never")
    of.add_argument("--why", metavar="NOTE", help="a note, shown when listing")
    on = add("on", "undo `unlimited off TARGET`", "Switches TARGET back on; exit 1 if it was not off.",
             "example:\n  unlimited on commandcode")
    on.add_argument("target", metavar="TARGET", help="exactly as it was switched off")
    on.add_argument("--account", metavar="NAME", help="exactly as it was switched off")
    on.add_argument("--json", action="store_true", help="a JSON array of what is off (after any change)")
    inc = add("incentive", "encourage or discourage a catalog target here", """\
A positive multiplier divides a route's expected time cost: 10 encourages it and 0.1 discourages
it without making it unavailable. Without --for, expiry is the latest relevant reset on each
account affected at activation. `off` removes this exact target/account scope; omit TARGET to list.""")
    inc.add_argument("target", nargs="?", metavar="TARGET", help="provider, model, offering, usage vendor or PROVIDER:MODEL")
    inc.add_argument("factor", nargs="?", metavar="FACTOR", help="positive multiplier, or off to clear TARGET")
    inc.add_argument("--account", metavar="NAME", help="one account id or identity name")
    inc.add_argument("--for", dest="for_", type=_duration, metavar="DURATION", help="explicit expiry")
    inc.add_argument("--json", action="store_true", help="JSON activation groups")
    ch = add("choose", "rank the candidates for a task by expected cost; logged", f"""\
Of the candidates the caller allows, which to use now, and in what order to fall back. A caller
usually passes --tier, --candidates, --deadline and --quota; the rest is rarely needed. Launch
candidates[pick]; if it cannot run, the next index in order; record each use with
`attempt start --decision ID` and `attempt end`, which is what the next choice learns from.

Each candidate route is scored by its expected cost in minutes, from this machine's attempt log:
  E = ((1 − p)·T_ok + p·(T_fail + T_next)) / multiplier + quota_weight·debit·π(ρ) − preference
p is its recent failure rate, T_ok and T_fail how long it takes to succeed or fail (a hang costs
--deadline), T_next what a retry elsewhere costs, π(ρ) = 1 / (1 + exp(−5(ρ − 1))) the price of
spending an account projected to reach ρ of its limit by reset (0.5 at the limit, never past 1;
unknown counts as at the limit; a live promotion costs 0), debit how much of its account one run
uses (catalog, default 1), and
preference up to a minute for the catalog's tie_preference plus the caller's --prefer. Recent
history weighs most (12 h half-life), so a route that just failed twice is avoided and recovers on
its own.

The order explores by itself: p and T_ok are drawn from what each record supports (Thompson
sampling), so a route with little history is sometimes tried and one that cannot win is not, and
exploration fades as records fill. Nothing needs tuning.

Prints the decision as JSON and appends it to the log with the whole request:
  decision (id), request, seed, candidates (each with provider, model = its offering id,
  vendor, rho, pi, debit, p, t_ok, t_fail, t_next, preference, e, e_drawn, the evidence ok,
  fail, mu and var, and prob, its odds of coming first), policy (thompson, best or softmax),
  order (candidate indices, the order to try), pick (the first of order), prefer_unmatched,
  attempts_unknown and routes_unknown.
Without the log (your own history): unlimited.choice.rank. Details: {DOCS}/choice.md#3-choice""", """\
examples:
  # which of three providers' standard models, for a 15-minute job
  unlimited choose --tier standard --candidates codex,glm,deepseek --deadline 900 --json

  # two routes of one model on two accounts the caller projects, the dearer one ruled out
  unlimited choose --tier standard --candidates deepseek-v4-1-flash --deadline 900 \\
      --quota opencode-go/deepseek-v4.1-flash=0.6 \\
      --exclude commandcode/deepseek/deepseek-v4.1-flash="account used up" --json

  # variety: providers already used this series cost 5 minutes more, but still compete
  unlimited choose --tier standard --candidates codex,glm,deepseek --deadline 900 \\
      --prefer codex=-5,glm=-5 --json

  # explore: near-equal candidates are each tried now and then
  unlimited choose --tier heavy --candidates codex,claude --deadline 1800 --temperature 2 \\
      --task summarise --meta ticket=42 --json

exit status: 0 decided; 1 no named candidate is live at the tier; 2 bad input.""")
    ch.add_argument("--tier", required=True, metavar="TIER", help="one of the catalog's tiers; what a provider name means")
    ch.add_argument("--candidates", required=True, metavar="NAME,...",
                    help="what the caller allows, comma-separated: a provider (its live routes at --tier), a "
                         "model (each of its live routes) or an offering id (that route)")
    ch.add_argument("--deadline", type=float, required=True, metavar="SECONDS",
                    help="how long the caller will wait before giving up on a use: what a hang costs")
    ch.add_argument("--quota", default="", metavar="ID=RHO,...",
                    help="the caller's projection of each account's use at its reset (0.8 = 80%% of the limit; "
                         "above 1 = runs out), keyed by offering id (that route), or by provider (its routes "
                         "on its usual vendor); each limit's projection.at_reset in `unlimited read` is one; a "
                         "candidate without one is priced as at the limit")
    ch.add_argument("--account", action="append", metavar="OFFERING=ACCOUNT",
                    help="account identity the caller will launch for this offering; repeatable")
    ch.add_argument("--exclude", default="", metavar="ID[=REASON],...",
                    help="optional: offering ids the caller rules out; a reason is recorded, never "
                         "read")
    ch.add_argument("--prefer", default="", metavar="NAME=MINUTES,...",
                    help="optional: lean the choice without ruling anything out: minutes taken off a "
                         "candidate's expected cost (negative adds them), for a provider, model or "
                         "offering id; a route takes its most specific name's value, so "
                         "codex=-5,gpt-6-luna=0 handicaps every Codex route but Luna. The minutes "
                         "applied show as each candidate's `preference`; names matching no candidate "
                         "are listed in `prefer_unmatched`")
    ch.add_argument("--vendors", metavar="VENDOR,...|any",
                    help="optional: the vendors a use may spend; a route on any other is not a "
                         "candidate. Default: those with an account on this machine; `any` for every "
                         "vendor, when the caller launches elsewhere")
    ch.add_argument("--temperature", type=float, metavar="MINUTES",
                    help="advanced; omit it. Without it, each candidate is tried about as often as its record "
                         "says it could be the best (Thompson sampling), which fades as records fill. 0: "
                         "always the lowest expected cost, never exploring. Above 0: sampled, a candidate "
                         "this many minutes worse being e times less likely at each draw")
    ch.add_argument("--quota-weight", type=float, default=20.0, metavar="MINUTES",
                    help="advanced: minutes one unit of quota price is worth against time (default 20); 0 "
                         "ignores quota")
    ch.add_argument("--task", metavar="LABEL", help="the caller's label for the task: recorded, never read")
    ch.add_argument("--meta", action="append", type=_meta, metavar="KEY=VALUE", help=META_HELP)
    ch.add_argument("--json", action="store_true", help="JSON output (the only format; accepted for clarity)")
    at = add("attempt", "record the start and the end of one use of a model", f"""\
The attempt log is what `choose`, `outcomes` and `cards` learn from: record every use of a model,
chosen by `choose` or not. `attempt start` prints an id; `attempt end ID` closes it. A start
whose --deadline passes with no end counts as a timeout, so a caller that dies still reports the
hang; a caller that lost an attempt for its own reasons (it restarted) ends it `abandoned`, which
counts against no route. Details: {DOCS}/choice.md#2-attempt-log""", """\
example:
  id=$(unlimited attempt start --provider deepseek --model opencode-go/deepseek-v4.1-flash \\
         --deadline 900 --task summarise --decision "$decision")
  ... run the model ...
  unlimited attempt end "$id" --outcome ok --tokens-in 18000 --tokens-out 900""")
    ats = at.add_subparsers(dest="phase", required=True, metavar="PHASE", title="phases")
    st_ = ats.add_parser("start", help="record a use starting; prints its id", formatter_class=_Help,
                         description="Records one use of a model starting now, and prints the attempt id to pass to "
                                     "`attempt end`.")
    st_.add_argument("--provider", required=True, metavar="PROVIDER", help="the model's maker, as the catalog names it")
    st_.add_argument("--model", required=True, metavar="MODEL",
                     help="the id launched (an offering id, or the model name the tool was given)")
    st_.add_argument("--offering", metavar="ID", help="the catalog offering id, when --model is not it")
    st_.add_argument("--deadline", type=float, required=True, metavar="SECONDS",
                     help="how long the caller will wait; past it with no end, the attempt is a timeout")
    st_.add_argument("--effort", metavar="EFFORT", help="the reasoning effort asked for, if any (recorded)")
    st_.add_argument("--account", metavar="ID", help="the account used (an id from `read`), if known (recorded)")
    st_.add_argument("--decision", metavar="ID", help="the `choose` decision this use carries out, if any")
    st_.add_argument("--task", metavar="LABEL", help="the caller's label for the task: recorded, never read")
    st_.add_argument("--meta", action="append", type=_meta, metavar="KEY=VALUE", help=META_HELP)
    en = ats.add_parser("end", help="record how a use came out", formatter_class=_Help,
                        description="Records how the attempt ID came out. Only the first end of an attempt counts.")
    en.add_argument("id", metavar="ID", help="what `attempt start` printed")
    en.add_argument("--outcome", required=True, choices=["ok", "timeout", "error", "unavailable", "abandoned"],
                    metavar="OUTCOME",
                    help="ok; timeout (gave up at the deadline); error (it ran and failed); unavailable (the "
                         "route could not be reached or refused, a failure of that route only); abandoned (the "
                         "caller lost it; counts against no route)")
    en.add_argument("--tokens-in", type=int, metavar="N", help="input tokens the use spent (all of its calls)")
    en.add_argument("--tokens-out", type=int, metavar="N", help="output tokens, reasoning included")
    en.add_argument("--tokens-cache", type=int, metavar="N", help="input tokens read from cache")
    en.add_argument("--meta", action="append", type=_meta, metavar="KEY=VALUE", help=META_HELP)
    oc = add("outcomes", "each route's recent failure rate and durations here", """\
What `choose` sees, per route (provider and offering id): failure rate p (a Beta estimate that
starts at 10% and moves with evidence), expected minutes to succeed and to fail, and the decayed
weight of the successes and failures behind them (12 h half-life).""", """\
examples:
  unlimited outcomes
  unlimited outcomes --json   # adds runs, last, tokens (median per run) and tok_s""")
    oc.add_argument("--json", action="store_true", help="a JSON array, one object per route")
    c = add("capture", "save a tool's own usage report (for a Claude Code statusline)", """\
Reads the JSON Claude Code hands its statusline command on stdin and keeps its rate limits as the
Anthropic reading, with no network call. Never prints and never fails, so it cannot break the
statusline; pipe the same input on to the rest of your statusline script.""", """\
example (in the statusline script):
  input=$(cat); printf '%s' "$input" | unlimited capture claude-statusline; ...""")
    c.add_argument("source", choices=["claude-statusline"], help="whose report stdin carries")
    return p


def main(argv: list[str] | None = None) -> int:
    p = _parser()
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
    if a.cmd == "incentive":
        return _incentive(a)
    if a.cmd == "attempt":
        return _attempt(a)
    if a.cmd == "outcomes":
        return _outcomes(a)
    if a.cmd == "choose":
        return _choose(a)
    if a.cmd == "cards":
        return _cards(a)
    off = None
    verdict_cat = None
    vendors = getattr(a, "vendor", None) or sorted(REGISTRY)
    if a.cmd == "verdict":
        try:
            if a.work < 0 or a.max_age < 0:
                raise ValueError
            work = timedelta(seconds=a.work)
            max_age = timedelta(seconds=a.max_age)
            datetime.now(timezone.utc) + work
        except (ValueError, OverflowError):
            print("unlimited: --work and --max-age must be non-negative, finite durations "
                  "within the supported time range", file=sys.stderr)
            return 2
        # Before the reads: a broken switches file fails the command without waiting on a vendor.
        from . import catalog
        try:
            off = catalog.off_policy(datetime.now(timezone.utc))
        except catalog.CatalogError as e:
            print(f"unlimited: catalog: {e}", file=sys.stderr)
            return 2
        if a.offering is not None:
            try:
                verdict_cat = catalog.load_metadata()
            except (catalog.CatalogError, OSError, ValueError, TypeError, OverflowError):
                print("unlimited: --offering: catalog-unavailable", file=sys.stderr)
                return 2
            route = verdict_cat.route(a.offering)
            if route is None:
                print(f"unlimited: --offering {a.offering!r}: not an offering id in the catalog", file=sys.stderr)
                return 2
            if route["vendor"] not in REGISTRY:
                print(f"unlimited: --offering {a.offering!r}: usage vendor is not readable here", file=sys.stderr)
                return 2
            if a.vendor and any(v != route["vendor"] for v in a.vendor):
                print(f"unlimited: --offering {a.offering!r}: --vendor must name {route['vendor']}", file=sys.stderr)
                return 2
            vendors = a.vendor or [route["vendor"]]
    out = []
    for v in vendors:
        out += cache.through(REGISTRY[v], max_age=a.max_age,
                             clock=lambda: datetime.now(timezone.utc), get=transport.get)
    if a.cmd in (None, "status", "read"):
        from . import incentives
        out, errors = incentives._annotate(out, now=datetime.now(timezone.utc))
        if errors:
            print("unlimited: steering: " + ", ".join(errors), file=sys.stderr)
    if a.cmd == "verdict":
        from . import catalog, incentives
        from .verdict import verdict
        now = datetime.now(timezone.utc)
        try:
            off = catalog.off_policy(now)
        except catalog.CatalogError as e:
            print(f"unlimited: catalog: {e}", file=sys.stderr)
            return 2
        errors = []
        if verdict_cat is None:
            try:
                verdict_cat = catalog.load_metadata()
            except (catalog.CatalogError, OSError, ValueError, TypeError, OverflowError):
                errors.append("catalog-unavailable")
        out, annotation_errors = incentives._annotate(out, now=now, cat=verdict_cat)
        errors.extend(e for e in annotation_errors if e not in errors)
        if errors:
            print("unlimited: steering: " + ", ".join(errors), file=sys.stderr)
        try:
            rows = []
            for r in out:
                vendor = r.get("vendor")
                if not isinstance(vendor, str) or not vendor.strip():
                    raise ValueError("reading vendor missing or invalid")
                policy = incentives.evaluate_context(
                    verdict_cat, r.get("steering"), vendor=vendor,
                    account=r.get("account"), offering=a.offering, now=now)
                rows.append({
                    "vendor": r.get("vendor"), "account": r.get("account"),
                    "names": r.get("names", []), "steering": r["steering"],
                    "verdict": verdict(
                        r, model_scope=a.model_scope, now=now,
                        work=work, max_age=max_age,
                        off=off, policy=policy),
                })
        except ValueError as e:
            print(f"unlimited: {e}", file=sys.stderr)
            return 2
        json.dump(rows, sys.stdout)
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
