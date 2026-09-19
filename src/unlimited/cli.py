"""`unlimited` (a table for people), `unlimited read [--vendor V]... [--max-age S] --json`,
`unlimited capture claude-statusline`."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from . import cache, transport
from .adapters import REGISTRY


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="unlimited")
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
    c = sub.add_parser("capture", help="save a harness's own usage report (never prints)")
    c.add_argument("source", choices=["claude-statusline"])
    a = p.parse_args(argv)
    if a.cmd == "capture":
        # Runs inside the statusline chain: it must never print, block or fail the line.
        try:
            from .adapters import anthropic
            anthropic.capture(json.load(sys.stdin), datetime.now(timezone.utc))
        except Exception:
            pass
        return 0
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
