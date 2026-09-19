"""The reading shape (schema 1). Facts only: no band, no advice, no free-form detail. Each limit
also carries a `projection` (see projection.py): arithmetic on past readings, never advice."""

from __future__ import annotations

import math
from datetime import datetime, timezone

SCHEMA = 1
OK, UNREAD, REFUSED = "ok", "unread", "refused"


def iso(t: datetime | None) -> str | None:
    return t.astimezone(timezone.utc).isoformat() if t is not None else None


def moment(value: object) -> datetime | None:
    if isinstance(value, str):
        try:
            t = datetime.fromisoformat(value)
        except ValueError:
            return None
        return t if t.tzinfo else None
    return None


def limit(name: str, *, window_minutes: int | None, used_at_least: float | None,
          resets_at: datetime | None, held: bool | None, held_why: str | None = None,
          severity: str | None = None, active: bool | None = None, kind: str | None = None) -> dict:
    if used_at_least is not None and not (math.isfinite(used_at_least) and 0 <= used_at_least):
        used_at_least = None
    return {"name": name, "window_minutes": window_minutes, "used_at_least": used_at_least,
            "resets_at": iso(resets_at), "held": held, "held_why": held_why,
            # The vendor's own word for how close the limit is, verbatim (Anthropic only).
            "severity": severity,
            # Whether the vendor says it is applying this limit now (Anthropic only).
            "active": active,
            # The vendor's own type word for the limit, verbatim, where it gives one.
            "kind": kind}


def reading(vendor: str, account: str | None, taken_at: datetime, status: str, *,
            why: str | None = None, retry_until: datetime | None = None,
            limits: list[dict] | None = None, source: str = "api", plan: str | None = None) -> dict:
    # `plan` is the vendor's own word for the subscription, verbatim (e.g. `default_claude_max_5x`).
    return {"schema": SCHEMA, "vendor": vendor, "account": account, "taken_at": iso(taken_at),
            "source": source, "plan": plan, "status": status, "why": why, "retry_until": iso(retry_until), "limits": limits or []}


def settled(r: dict, now: datetime) -> dict:
    """The reading as of `now`: a window whose reset has passed no longer has a used figure,
    whatever it had when taken. A window with no reset at all is one the vendor has not opened;
    its figure (zero) stands until the next reading."""
    out = dict(r, limits=[dict(l) for l in r.get("limits", [])])
    for l in out["limits"]:
        resets = moment(l.get("resets_at"))
        if resets is not None and resets <= now:
            l["used_at_least"] = None
    return out


def failed(vendor: str, account: str | None, now: datetime, answer) -> dict:
    """A transport.Answer that carried no body, as a reading. 4xx/5xx is a refusal; no response
    or an unparsable one is unread."""
    status = REFUSED if answer.status is not None and answer.status >= 400 else UNREAD
    return reading(vendor, account, now, status, why=answer.why, retry_until=answer.retry_until)
