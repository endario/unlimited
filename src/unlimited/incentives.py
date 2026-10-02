"""Persistent, expiring multipliers for a catalog route's expected time cost."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .catalog import Catalog, CatalogError, target_known
from .schema import moment
from .identity import names as _identity
from .state import flock, write_json

ROLES = frozenset({"session", "weekly", "weekly_model", "month", "extra"})


@dataclass(frozen=True)
class Resolved:
    factors: dict[str, float]
    winners: dict[str, dict]
    unresolved: list[str]


def incentives_path(local: Path | None = None) -> Path:
    from .catalog import local_path
    return (local or local_path()).with_name("incentives.json")


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _factor(value: object) -> float:
    if not (_number(value) and value > 0):
        raise ValueError("multiplier must be a positive finite number")
    return float(value)


def parse_multiplier(text: str) -> float:
    """Parse the CLI's human-facing multiplier spelling."""
    if text.endswith("x"):
        text = text[:-1]
    try:
        return _factor(float(text))
    except (TypeError, ValueError):
        raise ValueError("FACTOR must be a positive finite number or off") from None


def _reading_identity(reading: dict) -> set[str]:
    return _identity({"account": reading.get("account"), "names": reading.get("names")})


def _valid(group: object) -> bool:
    if not isinstance(group, dict) or not isinstance(group.get("target"), str) or not group["target"]:
        return False
    if not _number(group.get("multiplier")) or group["multiplier"] <= 0:
        return False
    if moment(group.get("activated_at")) is None:
        return False
    if group.get("account") is not None and not (isinstance(group["account"], str) and group["account"].strip()):
        return False
    until = group.get("until")
    bindings = group.get("bindings")
    if (until is None) == (bindings is None):
        return False
    if until is not None:
        return moment(until) is not None and (group.get("account") is None or
                                              isinstance(group.get("account"), str) and group["account"].strip())
    return isinstance(bindings, list) and bool(bindings) and all(
        isinstance(b, dict) and isinstance(b.get("vendor"), str) and bool(b["vendor"])
        and isinstance(b.get("account"), str) and bool(b["account"])
        and isinstance(b.get("names", []), list) and all(isinstance(name, str) and name for name in b.get("names", []))
        and moment(b.get("until")) is not None for b in bindings)


def _load(path: Path) -> list[dict]:
    try:
        body = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        raise CatalogError(f"{path}: {e}") from None
    groups = body.get("incentives") if isinstance(body, dict) else None
    if not isinstance(groups, list) or not all(_valid(x) for x in groups):
        raise CatalogError(f"{path}: expected {{\"incentives\": [{{\"target\": ..., \"multiplier\": ...}}]}}")
    return groups


def _live(group: dict, now: datetime) -> bool:
    until = group.get("until")
    if until is not None:
        return moment(until) > now
    return any(moment(binding["until"]) > now for binding in group["bindings"])


def _current(groups: list[dict], now: datetime) -> list[dict]:
    out = []
    for group in groups:
        if not _live(group, now):
            continue
        if group.get("bindings") is not None:
            group = dict(group, bindings=[b for b in group["bindings"] if moment(b["until"]) > now])
        out.append(group)
    return out


def read(path: Path | None = None, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now().astimezone()
    return _current(_load(path or incentives_path()), now)


def write(groups: list[dict], path: Path | None = None) -> None:
    path = path or incentives_path()
    if not all(_valid(group) for group in groups):
        raise ValueError("invalid incentive state")
    with flock(path):
        write_json({"incentives": groups}, path, indent=1)


def _specificity(cat: Catalog, route: dict, target: str) -> int | None:
    offering = cat.route(target)
    if offering is None and ":" in target:
        provider, _, name = target.partition(":")
        candidate = cat.route(name)
        if candidate is not None and candidate["provider"] == provider:
            offering = candidate
    if offering is not None:
        return 4 if route["id"] == offering["id"] else None
    if target not in cat.route_names(route):
        return None
    if target in (route["id"], f"{route['provider']}:{route['id']}"):
        return 4
    if target in (route["model"], f"{route['provider']}:{route['model']}"):
        return 3
    if target == route["provider"]:
        return 2
    return 1  # vendor


def valid_target(cat: Catalog, target: str) -> bool:
    return target_known(cat, target)


def _group_identity(group: dict, route: dict, bound: set[str], now: datetime,
                    canonical: str | None = None) -> tuple[set[str] | None, datetime | None]:
    if group.get("until") is not None:
        until = moment(group["until"])
        return (_identity(group.get("account")) if group.get("account") is not None else set(), until)
    for binding in group["bindings"]:
        if binding["vendor"] != route["vendor"]:
            continue
        names = {binding["account"], *[x for x in binding.get("names", []) if isinstance(x, str)]}
        if group.get("account") is not None and group["account"] not in names:
            continue
        matched = canonical == binding["account"] if canonical is not None else bool(bound & names)
        if matched:
            return names, moment(binding["until"])
    return None, None


def resolve(cat: Catalog, groups: list[dict] | None, accounts: dict[str, object] | None, now: datetime,
            route_ids: set[str] | None = None) -> Resolved:
    """Resolve live groups once for ranking and readings; missing account context remains neutral."""
    groups = groups or []
    accounts = accounts or {}
    route_ids = route_ids if route_ids is not None else set(accounts) if accounts else None
    if not all(_valid(group) for group in groups):
        raise ValueError("invalid incentive policy")
    factors, winners, unresolved = {}, {}, []
    for offering in cat.offerings:
        route = cat._route(offering)
        if route_ids is not None and route["id"] not in route_ids:
            continue
        bound = _identity(accounts.get(route["id"]))
        candidates = []
        for position, group in enumerate(groups):
            if not _valid(group) or not _live(group, now):
                continue
            specificity = _specificity(cat, route, group["target"])
            if specificity is None:
                continue
            identity = accounts.get(route["id"])
            canonical = identity.get("account") if isinstance(identity, dict) else None
            needed, until = _group_identity(group, route, bound, now, canonical)
            if until is None or until <= now:
                if not bound and group.get("bindings") and any(b["vendor"] == route["vendor"] and moment(b["until"]) > now
                                                          for b in group["bindings"]):
                    label = f"{group['target']}/{group.get('account') or ''}".rstrip("/")
                    if label not in unresolved:
                        unresolved.append(label)
                continue
            if needed and not (bound & needed):
                if not bound:
                    label = f"{group['target']}/{group.get('account') or ''}".rstrip("/")
                    if label not in unresolved:
                        unresolved.append(label)
                continue
            # An explicit selector outranks target scope; reset bindings only supply identity and expiry.
            candidates.append((specificity + (10 if group.get("account") is not None else 0),
                               moment(group["activated_at"]), position, group, until))
        if candidates:
            _, _, _, group, until = max(candidates, key=lambda x: (x[0], x[1], x[2]))
            factors[route["id"]] = float(group["multiplier"])
            winners[route["id"]] = {**group, "until": until.isoformat(), "bindings": None}
        else:
            factors[route["id"]] = 1.0
    return Resolved(factors, winners, unresolved)


def _reset(reading: dict, now: datetime) -> datetime | None:
    resets = []
    for limit in reading.get("limits", []):
        if not isinstance(limit, dict) or limit.get("role") not in ROLES or limit.get("kind") == "TIME_LIMIT":
            continue
        minutes = limit.get("window_minutes")
        reset = moment(limit.get("resets_at"))
        if not (_number(minutes) and minutes > 0):
            continue
        if reset is None:
            if limit.get("used_at_least") == 0:
                continue
            return None
        if reset <= now:
            return None
        resets.append(reset)
    return max(resets) if resets else None


def _matches_reading(reading: dict, account: str | None) -> bool:
    return account is None or account in _reading_identity(reading)


def _scope_key(cat: Catalog, target: str) -> tuple[int, tuple[str, ...] | str]:
    matches = [(cat._route(offering), _specificity(cat, cat._route(offering), target)) for offering in cat.offerings]
    best = max((specificity for _, specificity in matches if specificity is not None), default=None)
    if best is None:
        return 0, target
    routes = [route for route, specificity in matches if specificity == best]
    if best == 4:
        return best, tuple(sorted(route["id"] for route in routes))
    if best == 3:
        return best, tuple(sorted(route["model"] for route in routes))
    return best, target


def _same_scope(cat: Catalog, a: dict, target: str, account: str | None) -> bool:
    selected = a.get("account")
    same_account = selected == account or (selected is not None and account is not None and any(
        b["account"] == selected and account in _identity(b) for b in a.get("bindings", [])))
    return same_account and _scope_key(cat, a["target"]) == _scope_key(cat, target)


def account_context(cat: Catalog, target: str, account: str | None) -> str | None:
    """Resolve a locally known identity alias without reading a vendor."""
    if account is None:
        return None
    from .adapters import REGISTRY
    vendors = {cat._route(offering)["vendor"] for offering in cat.offerings
               if _specificity(cat, cat._route(offering), target) is not None}
    if target in REGISTRY:
        vendors.add(target)
    matches = set()
    for vendor in vendors:
        try:
            known = REGISTRY[vendor].names()
        except Exception:
            continue
        if account in known:
            matches.add(account)
        else:
            matches.update(canonical for canonical, aliases in known.items() if account in aliases)
    if len(matches) > 1:
        raise ValueError(f"{account}: ambiguous account identity; use its account id")
    return next(iter(matches), account)


def account_binding(cat: Catalog, offering: str, account: str) -> dict:
    """Canonical account plus locally discovered aliases for one explicit launch binding."""
    from .adapters import REGISTRY
    route = cat.route(offering)
    if route is None:
        return {"account": account, "names": []}
    try:
        known = REGISTRY[route["vendor"]].names()
    except Exception:
        known = {}
    matches = [account] if account in known else [canonical for canonical, aliases in known.items() if account in aliases]
    if len(matches) > 1:
        raise ValueError(f"{account}: ambiguous account identity; use its account id")
    if matches:
        canonical = matches[0]
        return {"account": canonical, "names": known[canonical]}
    return {"account": account, "names": []}


def set_incentive(cat: Catalog, target: str, multiplier: object, *, now: datetime, duration: timedelta | None = None,
                  account: str | None = None, readings: list[dict] | None = None, path: Path | None = None) -> dict:
    """Set one scope atomically, freezing per-account reset expiry when no duration was given."""
    multiplier = _factor(multiplier)
    if not valid_target(cat, target):
        raise ValueError(f"{target}: not a catalog target")
    if account is not None and not (isinstance(account, str) and account.strip()):
        raise ValueError("--account needs an account id or name")
    if duration is not None and duration <= timedelta(0):
        raise ValueError("duration must be positive")
    path = path or incentives_path(cat.local)
    route_vendors = {cat._route(o)["vendor"] for o in cat.offerings if _specificity(cat, cat._route(o), target) is not None}
    from .adapters import REGISTRY
    if target in REGISTRY:
        route_vendors.add(target)
    if duration is not None:
        group = {"target": target, **({"account": account} if account else {}), "multiplier": multiplier,
                 "activated_at": now.isoformat(), "until": (now + duration).isoformat()}
    else:
        affected = [r for r in readings or [] if r.get("vendor") in route_vendors and _matches_reading(r, account)]
        bad = next((r for r in affected if r.get("status") != "ok"), None)
        if bad is not None:
            raise ValueError(f"{bad.get('vendor')}/{bad.get('account')}: unread; use --for")
        found = affected
        bindings = []
        for reading in found:
            reset = _reset(reading, now)
            if reset is None:
                raise ValueError(f"{reading.get('vendor')}/{reading.get('account')}: no usable reset; use --for")
            names = sorted(_reading_identity(reading) - {reading.get("account")})
            bindings.append({"vendor": reading["vendor"], "account": reading["account"], "names": names,
                             "until": reset.isoformat()})
        if not bindings:
            raise ValueError("no affected account has a usable reset; use --for")
        if account is not None:
            if len(bindings) != 1:
                raise ValueError("account identity is ambiguous")
            account = bindings[0]["account"]
        group = {"target": target, **({"account": account} if account else {}), "multiplier": multiplier,
                 "activated_at": now.isoformat(), "bindings": bindings}
    if not _valid(group):
        raise ValueError("invalid incentive state")
    with flock(path):
        old = _current(_load(path), now)
        groups = [x for x in old if not _same_scope(cat, x, target, account)] + [group]
        write_json({"incentives": groups}, path, indent=1)
    return group


def clear_incentive(cat: Catalog, target: str, *, account: str | None, now: datetime, path: Path | None = None) -> list[dict]:
    path = path or incentives_path(cat.local)
    with flock(path):
        old = _current(_load(path), now)
        groups = [x for x in old if not _same_scope(cat, x, target, account)]
        if len(groups) == len(old):
            raise ValueError(f"{target}: not steered here")
        write_json({"incentives": groups}, path, indent=1)
    return groups


def overlay(readings: list[dict], cat: Catalog, groups: list[dict], now: datetime) -> list[dict]:
    """Attach schema-1 steering after cache reads, never into cached vendor history."""
    out = []
    for reading in readings:
        account = {route["id"]: {"account": reading.get("account"), "names": reading.get("names", [])}
                   for route in (cat._route(offering) for offering in cat.offerings)
                   if route["vendor"] == reading.get("vendor")}
        resolved = resolve(cat, groups, account, now)
        routes = [{"id": oid, "target": winner["target"], "account": winner.get("account"),
                   "multiplier": winner["multiplier"], "until": winner["until"]}
                  for oid, winner in resolved.winners.items() if oid in account]
        settings = []
        for group in groups:
            if not _valid(group) or not _live(group, now):
                continue
            contexts = [route for route in (cat._route(offering) for offering in cat.offerings) if route["id"] in account]
            if not contexts and group["target"] == reading.get("vendor"):
                contexts = [{"vendor": reading["vendor"]}]
            for route in contexts:
                if "id" in route and _specificity(cat, route, group["target"]) is None:
                    continue
                bound = _reading_identity(reading)
                needed, until = _group_identity(group, route, bound, now, reading.get("account"))
                if until is not None and until > now and (not needed or needed & bound):
                    setting = {"target": group["target"], "account": group.get("account"),
                               "multiplier": group["multiplier"], "until": until.isoformat()}
                    if setting not in settings:
                        settings.append(setting)
                    break
        out.append(dict(reading, steering={"settings": settings, "routes": routes}))
    return out
