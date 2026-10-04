"""Usage-reading failure descriptions for human-facing consumers."""

from __future__ import annotations

import json
from functools import cache
from importlib import resources


@cache
def _catalog() -> dict:
    return json.loads(resources.files(__package__).joinpath("usage_errors.json").read_text(encoding="utf-8"))


def describe(why: str | None) -> dict:
    catalog = _catalog()
    entry = catalog["exact"].get(why)
    if entry is None:
        entry = next((catalog["prefixes"][p] for p in sorted(catalog["prefixes"], key=len, reverse=True)
                      if why and why.startswith(p)), catalog["fallback"])
    return dict(entry, message=entry["message"].replace("{reason}", why or "not reported"))
