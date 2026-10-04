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
MIN_MULTIPLIER = 1e-6  # smaller factors can overflow ordinary time costs
MAX_MULTIPLIER = 1e6
CONTEXT_VERSION = 1
_LOAD_CATALOG = object()


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
    if not (_number(value) and value >= MIN_MULTIPLIER):
        raise ValueError("multiplier must be finite and at least 1e-6")
    return float(value)


def parse_multiplier(text: str) -> float:
    """Parse the CLI's human-facing multiplier spelling."""
    if text.endswith("x"):
        text = text[:-1]
    try:
        return _factor(float(text))
    except (TypeError, ValueError):
        raise ValueError("FACTOR must be finite and at least 1e-6, or off") from None


def _reading_identity(reading: dict) -> set[str]:
    return _identity({"account": reading.get("account"), "names": reading.get("names")})


def _valid(group: object) -> bool:
    if not isinstance(group, dict) or not isinstance(group.get("target"), str) or not group["target"]:
        return False
    if not _number(group.get("multiplier")) or group["multiplier"] < MIN_MULTIPLIER:
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
            until = moment(binding["until"])
            if until > now:
                return names, until
    return None, None


def _resolved_winner(group: dict, until: datetime) -> dict:
    requested = float(group["multiplier"])
    effective = min(requested, MAX_MULTIPLIER)
    winner = {**group, "multiplier": effective, "until": until.isoformat(), "bindings": None}
    winner.pop("requested_multiplier", None)
    winner.pop("clamped", None)
    if effective != requested:
        winner.update(requested_multiplier=requested, clamped=True)
    return winner


def _resolve_route(cat: Catalog, groups: list[dict], route: dict, identity: object,
                   now: datetime, *, vendor_only: bool = False) -> tuple[dict | None, list[str]]:
    bound = _identity(identity)
    canonical = identity.get("account") if isinstance(identity, dict) else None
    # Expired declarations still distinguish canonical IDs from another account's aliases.
    if isinstance(identity, str) and any(
            b["vendor"] == route["vendor"] and b["account"] == identity
            for g in groups for b in g.get("bindings") or []):
        canonical = identity
    candidates, unresolved = [], []
    for position, group in enumerate(groups):
        if not _valid(group) or not _live(group, now):
            continue
        specificity = (1 if group["target"] == route["vendor"] and valid_target(cat, group["target"])
                       else None) if vendor_only else _specificity(cat, route, group["target"])
        if specificity is None:
            continue
        needed, until = _group_identity(group, route, bound, now, canonical)
        if until is None or until <= now:
            if not bound and group.get("bindings") and any(
                    b["vendor"] == route["vendor"] and moment(b["until"]) > now for b in group["bindings"]):
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
    if not candidates:
        return None, unresolved
    _, _, _, group, until = max(candidates, key=lambda x: x[:3])
    return _resolved_winner(group, until), unresolved


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
        winner, labels = _resolve_route(cat, groups, route, accounts.get(route["id"]), now)
        unresolved.extend(label for label in labels if label not in unresolved)
        factors[route["id"]] = winner["multiplier"] if winner is not None else 1.0
        if winner is not None:
            winners[route["id"]] = winner
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


def _context_identity(context: object) -> tuple[str, str | None] | None:
    if not isinstance(context, dict) or "account" not in context:
        return None
    vendor, account = context.get("vendor"), context["account"]
    if not isinstance(vendor, str) or not vendor.strip():
        return None
    if account is not None and (not isinstance(account, str) or not account.strip()):
        return None
    return vendor, account


def _policy_result(winner: dict | None, scope: str | None, reason: str | None,
                   unresolved: list[str], status: str) -> dict:
    multiplier = float(winner["multiplier"]) if winner is not None else 1.0
    requested = float(winner.get("requested_multiplier", winner["multiplier"])) if winner is not None else None
    clamped = requested is not None and requested != multiplier
    return {"status": status, "reason": "clamped" if clamped else reason,
            "multiplier": multiplier, "requested_multiplier": requested,
            "target": winner["target"] if winner is not None else None,
            "scope": scope, "until": winner["until"] if winner is not None else None,
            "account_scoped": winner is not None and winner.get("account") is not None,
            "clamped": clamped, "unresolved": list(unresolved)}


def evaluate_context(cat: Catalog | None, context: dict | None, *, vendor: str, account: str | None,
                     offering: str | None, now: datetime) -> dict:
    """Evaluate source-local settings at a supplied time, without reading policy or quota."""
    identity = _context_identity(context)
    if identity is not None:
        cv, ca = identity
        if cv != vendor or ca is not None and account is not None and ca != account:
            raise ValueError("context vendor/account conflict")
    route = None
    if offering is not None and cat is not None:
        route = cat.route(offering)
        if route is None or route["vendor"] != vendor:
            raise ValueError("offering/vendor conflict")
    if context is None:
        return _policy_result(None, None, "missing-context", [], "unavailable")
    if not isinstance(context, dict) or identity is None or type(context.get("v")) is not int:
        return _policy_result(None, None, "invalid-context", [], "unavailable")
    if context["v"] != CONTEXT_VERSION:
        return _policy_result(None, None, "unsupported-version", [], "unavailable")
    if context.get("error"):
        return _policy_result(None, None, "policy-unavailable", [], "unavailable")
    groups = context.get("settings")
    context_account = identity[1]
    try:
        valid = isinstance(groups, list) and all(
            _valid(g) and "bindings" not in g and g.get("until") is not None
            and (g.get("account") is None or context_account is not None and g["account"] == context_account)
            for g in groups)
    except OverflowError:
        valid = False
    if not valid:
        return _policy_result(None, None, "invalid-context", [], "unavailable")
    if not groups and offering is None:
        return _policy_result(None, None, "no-live-rule", [], "neutral")
    if cat is None:
        return _policy_result(None, None, "catalog-unavailable", [], "unavailable")
    given = context.get("unresolved")
    unresolved = [x for x in given if isinstance(x, str)] if isinstance(given, list) else []
    for group in groups:
        if not valid_target(cat, group["target"]) and group["target"] not in unresolved:
            unresolved.append(group["target"])
    binding = {"account": context_account if context_account is not None else account, "names": []}
    if offering is not None:
        resolved = resolve(cat, groups, {offering: binding}, now, {offering})
        winner = resolved.winners.get(offering)
        unresolved.extend(x for x in resolved.unresolved if x not in unresolved)
        specificity = _specificity(cat, route, winner["target"]) if winner is not None else None
        scope = {1: "vendor", 2: "provider", 3: "model", 4: "offering"}.get(specificity)
    else:
        winner, labels = _resolve_route(cat, groups, {"vendor": vendor}, binding, now, vendor_only=True)
        unresolved.extend(x for x in labels if x not in unresolved)
        scope = "vendor" if winner is not None else None
    if winner is None:
        return _policy_result(None, None, "no-live-rule", unresolved, "neutral")
    neutral = winner["multiplier"] == 1
    return _policy_result(winner, scope, "explicit-neutral" if neutral else None,
                          unresolved, "neutral" if neutral else "applied")


def _context_header(reading: dict, now: datetime) -> dict:
    account = reading.get("account")
    return {"v": CONTEXT_VERSION, "vendor": reading.get("vendor"),
            "account": account if isinstance(account, str) and account.strip() else None,
            "observed_at": now.isoformat(), "settings": [], "routes": [], "unresolved": []}


def overlay(readings: list[dict], cat: Catalog, groups: list[dict], now: datetime) -> list[dict]:
    """Attach canonical policy after cache reads, never into cached vendor history."""
    if not all(_valid(group) for group in groups):
        raise ValueError("invalid incentive policy")
    current = _current(groups, now)
    out = []
    for reading in readings:
        context = _context_header(reading, now)
        canonical = context["account"]
        bound = _reading_identity(reading) if canonical is not None else set()
        routes = [cat._route(offering) for offering in cat.offerings
                  if offering["vendor"] == reading.get("vendor")]
        for group in current:
            if canonical is None and (group.get("account") is not None or group.get("bindings") is not None):
                continue
            needed, until = _group_identity(group, {"vendor": reading.get("vendor")}, bound, now, canonical)
            if until is None or until <= now or needed and not needed & bound:
                continue
            if not valid_target(cat, group["target"]):
                if group["target"] not in context["unresolved"]:
                    context["unresolved"].append(group["target"])
                continue
            if not any(_specificity(cat, route, group["target"]) is not None for route in routes) and not (
                    not routes and group["target"] == reading.get("vendor")):
                continue
            if group.get("bindings") is not None:
                # Keep the original selector: canonicalising it before matching would widen its horizon.
                expiries = [_group_identity(dict(group, bindings=[b]), {"vendor": reading.get("vendor")},
                            bound, now, canonical)[1] for b in group["bindings"]]
                until = max(expiry for expiry in expiries if expiry is not None)
            context["settings"].append({"target": group["target"],
                                        "account": canonical if group.get("account") is not None else None,
                                        "multiplier": group["multiplier"], "activated_at": group["activated_at"],
                                        "until": until.isoformat()})
        accounts = {route["id"]: {"account": canonical, "names": []} for route in routes}
        resolved = resolve(cat, context["settings"], accounts, now, set(accounts))
        context["routes"] = []
        for oid, winner in resolved.winners.items():
            display = {"id": oid, "target": winner["target"], "account": winner.get("account"),
                       "multiplier": winner["multiplier"], "until": winner["until"]}
            if "requested_multiplier" in winner:
                display.update(requested_multiplier=winner["requested_multiplier"], clamped=True)
            context["routes"].append(display)
        out.append(dict(reading, steering=context))
    return out


def _annotate(readings: list[dict], *, now: datetime,
              cat: Catalog | None = _LOAD_CATALOG) -> tuple[list[dict], list[str]]:
    """Shared annotation and loader diagnostics, including when no reading was found."""
    from .catalog import load_metadata

    def failed(code):
        return [dict(r, steering={**_context_header(r, now), "error": code}) for r in readings], [code]

    try:
        groups = read(now=now)
    except (CatalogError, ValueError, TypeError, OverflowError):
        return failed("policy-unavailable")
    if not groups:
        return [dict(r, steering=_context_header(r, now)) for r in readings], []
    if cat is _LOAD_CATALOG:
        try:
            cat = load_metadata()
        except (CatalogError, OSError, ValueError, TypeError, OverflowError):
            return failed("catalog-unavailable")
    if cat is None:
        return failed("catalog-unavailable")
    return overlay(readings, cat, groups, now), []


def annotate(readings: list[dict], *, now: datetime) -> list[dict]:
    """Load local advisory policy onto factual readings without changing their evidence."""
    out, _ = _annotate(readings, now=now)
    return out
