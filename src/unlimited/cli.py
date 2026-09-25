"""`unlimited` (a table for people), `unlimited read [--vendor V]... [--max-age S] --json`,
`unlimited models [--tier T] [--provider P] [--json]`, `unlimited capture claude-statusline`."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

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
    out = []
    for v in getattr(a, "vendor", None) or sorted(REGISTRY):
        out += cache.through(REGISTRY[v], max_age=a.max_age,
                             clock=lambda: datetime.now(timezone.utc), get=transport.get)
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
