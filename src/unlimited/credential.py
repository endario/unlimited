from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Credential:
    account: str | None
    # Secret material. `repr=False` keeps it out of any traceback or debug print.
    secret: dict = field(repr=False)
