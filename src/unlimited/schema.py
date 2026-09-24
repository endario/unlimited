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


ROLES = {"five_hour": "session", "seven_day": "weekly", "month": "month"}
_DERIVED = object()


def role_of(name: str, window_minutes: int | None) -> str | None:
    """What kind of window a limit is, from the names every adapter shares. A window of known
    length that no name here covers is `extra`, never None: None hides it from every consumer.
    `limits:*` are Anthropic's severity entries, which repeat windows reported under other names."""
    if window_minutes is None or name.startswith("limits:"):
        return None
    return ROLES.get(name, "extra")


def limit(name: str, *, window_minutes: int | None, used_at_least: float | None,
          resets_at: datetime | None, held: bool | None, held_why: str | None = None,
          severity: str | None = None, active: bool | None = None, kind: str | None = None,
          role: str | None = _DERIVED, scope: str | None = None) -> dict:
    if used_at_least is not None and not (math.isfinite(used_at_least) and 0 <= used_at_least):
        used_at_least = None
    return {"name": name, "window_minutes": window_minutes, "used_at_least": used_at_least,
            "resets_at": iso(resets_at), "held": held, "held_why": held_why,
            # The vendor's own word for how close the limit is, verbatim (Anthropic only).
            "severity": severity,
            # Whether the vendor says it is applying this limit now (Anthropic only).
            "active": active,
            # The vendor's own type word for the limit, verbatim, where it gives one.
            "kind": kind,
            # session | weekly | weekly_model | month | extra | None (see `role_of`), and what a
            # weekly_model or extra limit covers, in the vendor's word.
            "role": role_of(name, window_minutes) if role is _DERIVED else role, "scope": scope}


def credits(taken_at: datetime, *, enabled: bool, used: float | None, limit: float | None,
            balance: float | None, currency: str | None, severity: str | None = None,
            disabled_reason: str | None = None, can_purchase: bool | None = None) -> dict:
    """What an account can spend past its plan's windows. Amounts are in `currency`'s major units.
    `enabled` is the vendor's word for whether that spending is on now; `disabled_reason` is its own,
    verbatim. `taken_at` is when the vendor said so, which outlives the reading that carries it."""
    return {"taken_at": iso(taken_at), "enabled": enabled, "used": used, "limit": limit,
            "balance": balance, "currency": currency, "severity": severity,
            "disabled_reason": disabled_reason, "can_purchase": can_purchase}


def reading(vendor: str, account: str | None, taken_at: datetime, status: str, *,
            why: str | None = None, retry_until: datetime | None = None,
            limits: list[dict] | None = None, source: str = "api", plan: str | None = None,
            credits: dict | None = None) -> dict:
    # `plan` is the vendor's own word for the subscription, verbatim (e.g. `default_claude_max_5x`).
    return {"schema": SCHEMA, "vendor": vendor, "account": account, "taken_at": iso(taken_at),
            "source": source, "plan": plan, "status": status, "why": why, "retry_until": iso(retry_until), "limits": limits or [],
            # Spend past the windows (see `credits()`); None where the vendor has no such thing or did not say.
            "credits": credits,
            # This machine's names for the account (config dirs, wrappers); set by `cache.through`.
            "names": []}


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
