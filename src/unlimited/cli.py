"""`unlimited` (a table for people), `unlimited read [--vendor V]... [--max-age S] --json`,
`unlimited models [--tier T] [--provider P] [--json]`,
`unlimited off [TARGET [--for D] [--why W]]`, `unlimited on TARGET`, `unlimited verdict --work S [--model-scope M] --json`,
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
    pairs = {(n, m) for n, p in cat.providers.items() for t, m in p.items() if t in catalog.TIERS}
    pairs |= {(p["provider"], p["model"]) for p in cat.promotions}
    known = ((provider, model) in pairs if model
             else provider in cat.providers or any(m == provider for _, m in pairs))
    if not known:
        print(f"unlimited: {a.target}: not a provider, a model or a provider:model pair in the catalog",
              file=sys.stderr)
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
    m.add_argument("--tier", choices=["standard", "heavy"], default="standard")
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
    vd = sub.add_parser("verdict", help="whether each account can take a unit of work, as JSON")
    vd.add_argument("--vendor", action="append", choices=sorted(REGISTRY))
    vd.add_argument("--model-scope", default=None, help="the model family the work runs (e.g. Opus)")
    vd.add_argument("--work", type=float, required=True, help="expected duration, seconds")
    vd.add_argument("--max-age", type=float, default=300.0)
    vd.add_argument("--json", action="store_true", help="JSON output (the only format for now)")
    c = sub.add_parser("capture", help="save a harness's own usage report (never prints)")
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
