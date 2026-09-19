"""Read AI-subscription quota usage, per window, as facts."""

from __future__ import annotations

from datetime import datetime, timezone

from . import cache, transport
from .adapters import REGISTRY

VENDORS = tuple(sorted(REGISTRY))


def read(vendors: list[str] | None = None, *, max_age: float = 300.0) -> list[dict]:
    """Readings for each vendor's accounts on this machine, through the shared cache."""
    out = []
    for v in vendors or VENDORS:
        out += cache.through(REGISTRY[v], max_age=max_age,
                             clock=lambda: datetime.now(timezone.utc), get=transport.get)
    return out


def accounts(vendor: str) -> list[str]:
    """The account ids this machine can read for one vendor, without asking the vendor."""
    return [c.account for c in REGISTRY[vendor].discover() if c.account]
