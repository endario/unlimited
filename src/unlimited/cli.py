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
        prefer = {}
        for x in (x for x in a.prefer.split(",") if x):
            name, eq, m = x.partition("=")
            if not eq:
                raise ValueError(f"--prefer {x!r}: expected <name>=<minutes>")
            prefer[name] = float(m)
    except (catalog.CatalogError, ValueError) as e:
        print(f"unlimited: {e}", file=sys.stderr)
        return 2
    outcomes.compact(now)
    if a.tier not in cat.tiers:
        print(f"unlimited: {a.tier}: not a tier of the catalog ({', '.join(cat.tiers)})", file=sys.stderr)
        return 2
    try:
        got = choice.choose(cat, tier=a.tier, candidates=list(dict.fromkeys(p for p in a.candidates.split(",") if p)),
                            quota=quota, deadline=a.deadline, now=now, temperature=a.temperature, preset=a.preset,
                            quota_weight=a.quota_weight, task=a.task, meta=dict(a.meta or []),
                            exclude=dict(x.partition("=")[::2] for x in a.exclude.split(",") if x),
                            prefer=prefer,
                            vendors=(None if a.vendors == "any" else choice.vendors_here(cat) if a.vendors is None
                                     else {v for v in a.vendors.split(",") if v}))
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


META_HELP = ("KEY=VALUE, repeatable: the caller's own metadata (a ticket, a job id), recorded with the "
             "record and never read by unlimited")
READ_HELP = ("seconds a cached reading may be before this reads the vendor again (default 300); "
             "0 always reads")
VENDOR_HELP = "only this vendor, repeatable (default: every vendor)"
FILES = """files:
  ~/.config/unlimited/catalog.toml   this machine's catalog additions (docs/catalog.md); optional
  ~/.config/unlimited/switches.json  what `unlimited off` switched off here
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
reading), `excluded` (the vendor stopped it, a window is used up, or one runs out before the work
would finish; with which window and when it lifts) or `ranked`, with a `tier` (0: no window
projected past its limit; 1: one is, but after the work) and a `score` to order accounts by.
Ordering, tie rules and fallback are the caller's. Details: {DOCS}/choice.md#1-verdict""", """\
example:
  unlimited verdict --vendor anthropic --work 900 --model-scope Opus --json""")
    vd.add_argument("--vendor", action="append", choices=sorted(REGISTRY), metavar="VENDOR",
                    help=VENDOR_HELP + f": {', '.join(sorted(REGISTRY))}")
    vd.add_argument("--work", type=float, required=True, metavar="SECONDS",
                    help="how long the unit of work is expected to take")
    vd.add_argument("--model-scope", default=None, metavar="MODEL",
                    help="the model family the work runs, for limits scoped to one (e.g. Opus); "
                         "without it, model-scoped limits are left out")
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
                        "one-vendor-per-provider view (providers, promotions), and presets")
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
until --for lapses. TARGET is a provider, a model, a vendor, an offering id, or PROVIDER:MODEL.
The shipped catalog is untouched; the switch lives in ~/.config/unlimited/switches.json.
With no TARGET, lists what is off and until when.""", """\
examples:
  unlimited off                                         # what is off
  unlimited off commandcode --for 12h --why "overloaded"
  unlimited off codex:gpt-6-sol
  unlimited on commandcode""")
    of.add_argument("target", nargs="?", metavar="TARGET", help="what to switch off (omit to list)")
    of.add_argument("--for", dest="for_", type=_duration, metavar="DURATION",
                    help="lapse after this long: a number and m, h, d or w (90m, 12h, 1d, 1w); default never")
    of.add_argument("--why", metavar="NOTE", help="a note, shown when listing")
    on = add("on", "undo `unlimited off TARGET`", "Switches TARGET back on; exit 1 if it was not off.",
             "example:\n  unlimited on commandcode")
    on.add_argument("target", metavar="TARGET", help="exactly as it was switched off")
    ch = add("choose", "rank the candidates for a task by expected cost; logged", f"""\
Of the candidates the caller allows, which to use now, and in what order to fall back.

Each candidate route is scored by its expected cost in minutes, from this machine's attempt log:
  E = (1 − p)·T_ok + p·(T_fail + T_next) + quota_weight·debit·π(ρ) − preference
p is its recent failure rate, T_ok and T_fail how long it takes to succeed or fail (a hang costs
--deadline), T_next what a retry elsewhere costs, π(ρ) = exp(5(ρ − 1)) the price of spending an
account projected to reach ρ of its limit by reset (1 at the limit; unknown counts as 1; a live
promotion costs 0), debit how much of its account one run uses (catalog, default 1), and
preference up to a minute for the catalog's tie_preference plus the caller's --prefer. Recent history weighs most (12 h
half-life), so a route that just failed twice is avoided and recovers on its own.

Prints the decision as JSON and appends it to the log with the whole request:
  decision (id), request, seed, candidates (each with provider, model = its offering id,
  vendor, rho, pi, debit, p, t_ok, t_fail, preference, e and prob, its odds of coming first),
  order (candidate indices, the order to try), pick (the first of order), effective (the
  temperature and quota weight used), prefer_unmatched and attempts_unknown.
Pass the decision id to `attempt start --decision` so the log ties the use to the choice.
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

  # a named way of choosing: near-equal candidates take turns
  unlimited choose --tier standard --candidates codex,glm,deepseek --deadline 900 --preset spread --json

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
    ch.add_argument("--exclude", default="", metavar="ID[=REASON],...",
                    help="offering ids the caller rules out; a reason is recorded, never read")
    ch.add_argument("--prefer", default="", metavar="NAME=MINUTES,...",
                    help="lean the choice without ruling anything out: minutes taken off a candidate's "
                         "expected cost (negative adds them), for a provider, model or offering id; a route "
                         "takes its most specific name's value, so codex=-5,gpt-6-luna=0 handicaps every "
                         "Codex route but Luna. The minutes applied show as each candidate's `preference`; "
                         "names matching no candidate are listed in `prefer_unmatched`")
    ch.add_argument("--vendors", metavar="VENDOR,...|any",
                    help="the vendors a use may spend; a route on any other is not a candidate. Default: "
                         "those with an account on this machine; `any` for every vendor, when the caller "
                         "launches elsewhere")
    ch.add_argument("--preset", metavar="NAME",
                    help="a named way of choosing from the catalog (`unlimited models --catalog` lists them "
                         "under presets), setting --temperature and --quota-weight wherever this call does "
                         "not; e.g. steady (the lowest expected cost) or spread (near-equal candidates take "
                         "turns)")
    ch.add_argument("--temperature", type=float, metavar="MINUTES",
                    help="0 (default, unless --preset sets it): cheapest first; above it the order is sampled, a candidate this many "
                         "minutes worse being e times less likely at each draw")
    ch.add_argument("--quota-weight", type=float, metavar="MINUTES",
                    help="minutes one unit of quota price is worth against time (default 20, unless --preset sets it); 0 ignores quota")
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
