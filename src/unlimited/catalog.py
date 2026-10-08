"""Which models each provider offers at each tier, and what else the catalog says (docs/catalog.md).
The shipped `catalog.toml` is overridden by $XDG_CONFIG_HOME/unlimited/catalog.toml."""

from __future__ import annotations

import json
import math
import os
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from importlib import resources
from pathlib import Path

from .identity import POLICY_PROJECTION, policy_key
from .state import flock as _flock
from .state import write_json

SCHEMA = 2
# A card's prices, USD per million tokens.
PRICES = ("input", "output", "cache_read", "cache_write")


def _number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0


class CatalogError(Exception):
    """The catalog cannot be read. A routing call that meets this must not start its run."""


class NotOff(Exception):
    """`switch` was asked to switch a target on that is not switched off."""


@dataclass(frozen=True)
class Candidate:
    provider: str
    model: str
    promoted: bool


def local_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "unlimited" / "catalog.toml"


def switches_path(local: Path | None = None) -> Path:
    """Beside the local catalog: the machine's own state, never shipped."""
    return (local or local_path()).with_name("switches.json")


def discovered_path(local: Path | None = None) -> Path:
    """Also beside the local catalog: this machine's live-discovered route ids per vendor,
    written by `unlimited routes`."""
    return (local or local_path()).with_name("discovered.json")


def read_discovered(path: Path | None = None) -> dict[str, list[str]]:
    """The discovered route ids per vendor (`{"opencode": ["minimax-m3", ...]}`). A file that
    does not parse is an error, as the switches' is, since discovery that silently stops
    applying is the failure it exists to prevent."""
    path = path or discovered_path()
    try:
        got = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise CatalogError(f"{path}: {e}") from None
    if not isinstance(got, dict) or not all(isinstance(v, list) and all(isinstance(i, str) for i in v)
                                            for v in got.values()):
        raise CatalogError(f'{path}: expected {{"vendor": ["route id", ...]}}')
    return got


def write_discovered(vendors: dict[str, list[str]], path: Path | None = None) -> None:
    path = path or discovered_path()
    with _flock(path):
        write_json(vendors, path, indent=1)


def read_switches(path: Path | None = None) -> list[dict]:
    """This machine's switched-off targets: `{"target", "until", "why"}` and, for a switch on one
    account of a vendor, `"account"` (its account id or an identity name), `until` an ISO time or
    None. Written by `unlimited off/on`; a file that does not parse is an error, as the catalog's
    is, since a switch that silently stops applying is the failure it exists to prevent."""
    path = path or switches_path()
    try:
        got = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        raise CatalogError(f"{path}: {e}") from None
    # An older release left the file at the umask; it holds free-text --why. A mode that cannot
    # be repaired is not a reason to refuse the command.
    try:
        if path.stat().st_mode & 0o777 != 0o600:
            os.chmod(path, 0o600)
    except OSError:
        pass
    off = got.get("off") if isinstance(got, dict) else None
    if not isinstance(off, list) or not all(isinstance(x, dict) and isinstance(x.get("target"), str)
                                            and (x.get("account") is None or isinstance(x["account"], str))
                                            for x in off):
        raise CatalogError(f"{path}: expected {{\"off\": [{{\"target\": ..., \"account\": ...}}]}}")
    for x in off:
        if x.get("until") is not None and moment_utc(x["until"]) is None:
            raise CatalogError(f"{path}: {x['target']}: until {x['until']!r} is not an ISO time")
        if x.get("account") is not None and not x["account"].strip():
            raise CatalogError(f"{path}: {x['target']}: account {x['account']!r} is blank")
    return off


def _write(off: list[dict], path: Path) -> None:
    """Keep the established switches shape through the generic state writer."""
    write_json({"off": off}, path, indent=1)


def write_switches(off: list[dict], path: Path | None = None) -> None:
    path = path or switches_path()
    with _flock(path):
        _write(off, path)


def switch(target: str, *, on: bool = False, until: datetime | None = None, why: str | None = None,
           account: str | None = None, now: datetime, path: Path | None = None) -> list[dict]:
    """Flip one switch — of a whole target, or with `account`, of one account of a usage vendor —
    the whole read-mutate-write under the switches' lock; lapsed entries drop as they are passed.
    `on` of a switch that is not off raises NotOff and writes nothing. Returns exactly what it
    wrote, so a caller can echo the state it itself persisted."""
    if account is not None and not account.strip():
        raise CatalogError(f"{target}: --account needs an account id or a name")
    account = account or None
    path = path or switches_path()
    with _flock(path):
        was = [x for x in read_switches(path) if live(x, now)]
        same = [x for x in was if x["target"] == target and x.get("account") == account]
        if on and not same:
            raise NotOff(target if account is None else f"{target}/{account}")
        kept = [{"target": x["target"], "until": x.get("until"), "why": x.get("why"),
                 **({"account": x["account"]} if x.get("account") is not None else {})}
                for x in was if x not in same]
        if not on:
            kept.append({"target": target, "until": until.isoformat() if until else None, "why": why,
                         **({"account": account} if account is not None else {})})
        _write(kept, path)
        return kept


def live(x: dict, now: datetime) -> bool:
    """Whether a switch holds at `now`: no end, or one not yet reached. One rule, read by the
    catalog's blocking, `off`'s listing and `off_policy` alike."""
    until = moment_utc(x.get("until"))
    return until is None or until > now


def switch_policy_keys(switch: dict) -> list[str]:
    from .adapters import REGISTRY
    return [policy_key(switch["target"], switch.get("account"))] if switch["target"] in REGISTRY else []


def project_switch(switch: dict) -> dict:
    return dict(switch, policy_projection=POLICY_PROJECTION, policy_keys=switch_policy_keys(switch))


def off_policy(now: datetime, path: Path | None = None) -> dict[str, str | None]:
    """The machine's live switches over usage vendors as a policy (`verdict`'s `off`): keyed by
    the vendor (`zai`) for a whole-vendor switch, or `vendor/account` (`zai/claude-glm-2`) for one
    account's, each to its switch's `until` (ISO text, None when it has no end)."""
    return {key: x.get("until") for x in read_switches(path) if live(x, now)
            for key in switch_policy_keys(x)}


# What a schema-2 file may hold at its top level.
TOP_LEVEL = frozenset({"schema", "tiers", "models", "offerings", "banned", "tie_preference", "cards"})


def _parse(text: str, where: str) -> dict:
    try:
        got = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise CatalogError(f"{where}: {e}") from None
    if got.get("schema") == 1:
        got = _from_schema_1(got, where)
    elif got.get("schema") != SCHEMA:
        raise CatalogError(f"{where}: schema {got.get('schema')!r}, expected {SCHEMA}")
    elif unknown := sorted(set(got) - TOP_LEVEL - {k for k in got if k.startswith("_") or k == "provider_keys"}):
        raise CatalogError(f"{where}: unknown {', '.join(unknown)} (expected {', '.join(sorted(TOP_LEVEL))})")
    elif any(k.startswith("_") or k == "provider_keys" for k in got) or any(
            k.startswith("_") for o in got.get("offerings", []) if isinstance(o, dict) for k in o):
        raise CatalogError(f"{where}: keys starting with _, and provider_keys, are unlimited's own")
    if not isinstance(got.get("models", {}), dict) or not all(isinstance(m, dict) for m in got.get("models", {}).values()):
        raise CatalogError(f"{where}: models must be tables")
    if not all(isinstance(got.get(k, []), list) for k in ("tiers", "offerings", "banned", "tie_preference", "cards")):
        raise CatalogError(f"{where}: tiers, offerings, banned, tie_preference and cards must be lists")
    if not all(isinstance(o, dict) for k in ("offerings", "cards") for o in got.get(k, [])):
        raise CatalogError(f"{where}: each offering and card is a table")
    return got


def _from_schema_1(old: dict, where: str) -> dict:
    """A schema-1 file as schema 2: each provider's model at a tier becomes a model of that provider,
    named by its id, with one offering of that id on the provider's `usage` vendor; a promotion
    becomes a free offering."""
    providers = old.get("providers", {})
    if not isinstance(providers, dict) or not all(isinstance(p, dict) for p in providers.values()):
        raise CatalogError(f"{where}: providers must be tables")
    tiers = old.get("tiers", ["standard", "heavy"])
    models: dict[str, dict] = {}
    offerings: list[dict] = []

    def add(provider: object, mid: object, tier_list: object, vendor: object, extra: dict) -> None:
        # A provider this file names without `usage` is the shipped one's: its vendor is resolved
        # when the files merge.
        if not (isinstance(provider, str) and isinstance(mid, str) and isinstance(tier_list, list)
                and (vendor is None or isinstance(vendor, str))):
            raise CatalogError(f"{where}: provider {provider!r}: a model id, its tiers and its usage vendor")
        m = models.setdefault(mid, {"provider": provider, "tiers": []})
        m["tiers"] += [t for t in tier_list if t not in m["tiers"]]
        if m["provider"] != provider:
            raise CatalogError(f"{where}: {mid}: listed under more than one provider")
        if not any(o["id"] == mid for o in offerings):
            offerings.append({"id": mid, "model": mid, **({"vendor": vendor} if vendor else {"_of": provider}),
                              **extra})

    replaces = []  # (provider, tier): schema 1's provider key replaced the shipped model there
    # A provider's `usage` alone moved its shipped models to that vendor's account.
    if any("usage" in p and not isinstance(p["usage"], str) for p in providers.values()):
        raise CatalogError(f"{where}: a provider's usage is a vendor name")
    usage = {name: p["usage"] for name, p in providers.items() if isinstance(p.get("usage"), str)}
    # Keys of the reader's own on a provider table, kept for the `providers` view.
    extras = {name: {k: v for k, v in p.items() if k != "usage" and k not in (tiers if isinstance(tiers, list) else [])}
              for name, p in providers.items()}
    for name, p in providers.items():
        for t in tiers if isinstance(tiers, list) else []:
            if t in p:
                add(name, p[t], [t], p.get("usage"), {})
                replaces.append((name, t))
    for promo in old.get("promotions", []):
        if not isinstance(promo, dict):
            raise CatalogError(f"{where}: a promotion is a table")
        add(promo.get("provider"), promo.get("model"), promo.get("tiers"),
            providers.get(promo.get("provider"), {}).get("usage") if isinstance(promo.get("provider"), str) else None,
            {"free": True, **({"until": promo["until"]} if "until" in promo else {})})
    out = {k: v for k, v in old.items() if k not in ("providers", "promotions")}
    return {**out, "schema": SCHEMA, "models": models, "offerings": offerings,
            "_replaces": replaces, "_replaces_free": "promotions" in old, "_usage": usage,
            "provider_keys": {k: v for k, v in extras.items() if v}}


def _merge(shipped: dict, local: dict) -> dict:
    out = dict(shipped)
    models = {k: dict(v) for k, v in shipped.get("models", {}).items()}
    if "tiers" in local:
        # A tier the local list drops is no longer served by any shipped model.
        for m in models.values():
            m["tiers"] = [t for t in m.get("tiers", []) if t in local["tiers"]]
    # A schema-1 local file said "this provider's model at this tier is X" and "these are the
    # promotions": the shipped model it displaces no longer serves that tier, and its promotions
    # replace the shipped free offerings.
    shipped_owner = {o["id"]: shipped["models"][o["model"]]["provider"] for o in shipped.get("offerings", [])
                     if o.get("model") in shipped.get("models", {})}
    for name, m in local.get("models", {}).items() if "_replaces" in local else []:
        if shipped_owner.get(name, m.get("provider")) != m.get("provider"):
            raise CatalogError(f"{name}: listed under more than one provider")
    for provider, tier in local.get("_replaces", []):
        for m in models.values():
            if m["provider"] == provider and tier in m["tiers"]:
                m["tiers"] = [t for t in m["tiers"] if t != tier]
    shipped_offerings = [dict(o, vendor=local["_usage"][models[o["model"]]["provider"]])
                         if models.get(o.get("model"), {}).get("provider") in local.get("_usage", {}) else o
                         for o in shipped.get("offerings", [])]
    if local.get("_replaces_free"):
        shipped_offerings = [o for o in shipped_offerings if not o.get("free")]
    for k, v in local.get("models", {}).items():
        models.setdefault(k, {}).update(v)
    out["models"] = models
    # A local offering replaces the shipped one with its id, or is added.
    local_offerings = []
    for o in local.get("offerings", []):
        if isinstance(o, dict) and "_of" in o:
            of = o.pop("_of")
            vendor = next((x["vendor"] for x in shipped.get("offerings", [])
                           if shipped.get("models", {}).get(x.get("model"), {}).get("provider") == of), None)
            if vendor is None:
                raise CatalogError(f"provider {of}: unknown, and no usage vendor given")
            o["vendor"] = vendor
        local_offerings.append(o)
    ids = {o.get("id") for o in local_offerings}
    out["offerings"] = [o for o in shipped_offerings if o.get("id") not in ids] + local_offerings
    keys = {k: dict(v) for k, v in shipped.get("provider_keys", {}).items()}
    for k, v in local.get("provider_keys", {}).items():
        keys.setdefault(k, {}).update(v)
    out["provider_keys"] = keys
    for whole in ("tiers", "tie_preference"):
        if whole in local:
            out[whole] = local[whole]
    out["banned"] = list(shipped.get("banned", [])) + list(local.get("banned", []))
    # A local card replaces the shipped one for the same vendor, name and plan; the rest stand.
    local_cards = local.get("cards", [])
    keys = {_card_key(c) for c in local_cards if isinstance(c, dict)}
    out["cards"] = [c for c in shipped.get("cards", []) if _card_key(c) not in keys] + list(local_cards)
    return out


def _card_key(c: dict) -> tuple:
    return c.get("vendor"), c.get("name"), c.get("plan")


def _date(v: object) -> bool:
    return isinstance(v, date) and not isinstance(v, datetime)


def _check(c: dict) -> None:
    tiers = c.get("tiers")
    if not (isinstance(tiers, list) and tiers and all(isinstance(t, str) for t in tiers)):
        raise CatalogError("tiers: a list of names")
    models = c.get("models", {})
    for name, m in models.items():
        # Any other key is the reader's own: kept, never read.
        if not (isinstance(m.get("provider"), str) and isinstance(m.get("tiers"), list)
                and all(t in tiers for t in m["tiers"])):
            raise CatalogError(f"model {name}: needs its provider (maker) and the tiers it serves")
    seen: set[str] = set()
    for i, o in enumerate(c.get("offerings", [])):
        ok = (isinstance(o.get("id"), str) and o.get("model") in models and isinstance(o.get("vendor"), str)
              and isinstance(o.get("free", False), bool) and _date(o.get("until", date.max))
              and _number(o.get("debit", 1)) and 0 < o.get("debit", 1) < math.inf)
        if not ok:
            raise CatalogError(f"offering {i + 1}: needs an id, a known model and a vendor; free, until and "
                               f"debit (a positive number) optional")
        if o["id"] in seen:
            raise CatalogError(f"offering {o['id']}: listed twice")
        seen.add(o["id"])
    for i, card in enumerate(c.get("cards", [])):
        ok = (isinstance(card, dict) and all(isinstance(card.get(k), str) for k in ("vendor", "name", "source"))
              and isinstance(card.get("models", []), list) and all(isinstance(m, str) for m in card.get("models", []))
              and isinstance(card.get("plan", ""), str) and isinstance(card.get("price", {}), dict)
              and all(_number(card[k]) for k in ("intelligence", "tok_s") if k in card)
              and all(k in PRICES and _number(v) for k, v in card.get("price", {}).items())
              and _date(card.get("as_of")))
        if not ok:
            raise CatalogError(f"card {i + 1}: needs vendor, name, source, as_of; models, plan, "
                               f"intelligence, tok_s and price ({', '.join(PRICES)}) are optional")
    if not all(isinstance(m, str) for m in c.get("banned", [])):
        raise CatalogError("banned: names only (providers, models, vendors, offering ids)")
    makers = {m["provider"] for m in models.values()}
    if not all(p in makers for p in c.get("tie_preference", [])):
        raise CatalogError("tie_preference: known providers only")


def target_known(cat: "Catalog", target: str, *, account: str | None = None) -> bool:
    """Whether the CLI may name this catalog target; account scope is usage-vendor-only."""
    from .adapters import REGISTRY
    provider, separator, model = target.partition(":")
    routes = [cat._route(offering) for offering in cat.offerings]
    if account is not None:
        return provider in REGISTRY and not separator
    if cat.route(target) is not None:
        return True
    pairs = {(route["provider"], name) for route in routes for name in (route["id"], route["model"])}
    names = {name for route in routes for name in (route["provider"], route["model"], route["vendor"], route["id"])}
    return (provider, model) in pairs if separator else provider in names or provider in REGISTRY


class Catalog:
    def __init__(self, data: dict, off: list[dict] | None = None, local: Path | None = None):
        self.local = local
        self.tiers: list[str] = data["tiers"]
        self.models: dict[str, dict] = data.get("models", {})
        self.offerings: list[dict] = data.get("offerings", [])
        self.banned: frozenset[str] = frozenset(data.get("banned", []))
        self.tie_preference: list[str] = data.get("tie_preference", [])
        self.cards: list[dict] = data.get("cards", [])
        self.provider_keys: dict[str, dict] = data.get("provider_keys", {})
        # Switched off on this machine: a provider, model, vendor, offering id or `provider:model`.
        self.off: list[dict] = off or []

    def _route(self, o: dict) -> dict:
        m = self.models[o["model"]]
        return {"id": o["id"], "provider": m["provider"], "model": o["model"], "vendor": o["vendor"],
                "tiers": m["tiers"], "free": o.get("free", False), "debit": o.get("debit", 1)}

    def route_names(self, r: dict) -> set[str]:
        """Every catalog target spelling for a route, shared by route policy consumers."""
        return {r["provider"], r["model"], r["vendor"], r["id"], f"{r['provider']}:{r['model']}",
                f"{r['provider']}:{r['id']}"}

    def _blocked_route(self, r: dict, now: datetime) -> bool:
        names = self.route_names(r)
        if names & self.banned:
            return True
        for x in self.off:
            # An account switch binds verdict alone: the vendor's other accounts still serve.
            if live(x, now) and x.get("account") is None and x["target"] in names:
                return True
        return False

    def blocked(self, provider: str, model: str, now: datetime) -> bool:
        """Banned, or switched off here until a time not yet reached (or with no end). `model` is a
        model name or an offering id."""
        o = next((o for o in self.offerings if o["id"] == model), None)
        if o is not None:
            return self._blocked_route(self._route(o), now)
        return self._blocked_route({"provider": provider, "model": model, "vendor": "", "id": model}, now)

    def routes(self, now: datetime, tier: str | None = None) -> list[dict]:
        """Every live offering (at `tier` when given): `id` (what a caller launches), `provider`
        (the maker), `model`, `vendor` (whose account a use spends), `tiers` and `free`. Free
        offerings first, then in file order."""
        today = now.astimezone(timezone.utc).date()
        out = []
        for o in self.offerings:
            r = self._route(o)
            if (tier is None or tier in r["tiers"]) and today <= o.get("until", date.max) \
                    and not self._blocked_route(r, now):
                out.append(r)
        return [r for r in out if r["free"]] + [r for r in out if not r["free"]]

    def candidates(self, tier: str, now: datetime) -> list[Candidate]:
        """`routes(now, tier)` as candidates: each live offering, `model` its id."""
        return [Candidate(r["provider"], r["id"], r["free"]) for r in self.routes(now, tier)]

    def to_json(self, now: datetime) -> dict:
        """The merged catalog as it stands at `now`: live offerings only, dates as ISO text. It also
        carries `providers` and `promotions`, a schema-1 view (each provider's first live offering at
        a tier, and the free offerings), for readers that launch one offering per provider."""
        live = self.routes(now)
        iso = lambda o: dict(o, until=o["until"].isoformat()) if "until" in o else dict(o)
        offerings = [iso(o) for o in self.offerings if any(r["id"] == o["id"] for r in live)]
        providers = {name: {**self.provider_keys.get(name, {}), **v} for name, v in self._view(live).items()}
        promotions = [{"provider": r["provider"], "model": r["id"], "tiers": r["tiers"],
                       **({"until": o["until"].isoformat()} if "until" in o else {})}
                      for r in live if r["free"] for o in self.offerings if o["id"] == r["id"]]
        return {"schema": SCHEMA, "tiers": self.tiers, "models": self.models, "offerings": offerings,
                "banned": sorted(self.banned), "tie_preference": self.tie_preference,
                "providers": providers, "promotions": promotions,
                "cards": [dict(c, as_of=c["as_of"].isoformat()) for c in self.cards]}

    def _view(self, live: list[dict]) -> dict[str, dict]:
        """One vendor per provider, for readers that launch one offering per provider: the vendor of
        its first live offering, and at each tier its first live offering on that vendor. A tier
        served only on another vendor is left out rather than paired with the wrong account."""
        view: dict[str, dict] = {}
        for r in live:
            if r["free"]:
                continue
            p = view.setdefault(r["provider"], {"usage": r["vendor"]})
            for t in r["tiers"]:
                if t not in p and r["vendor"] == p["usage"]:
                    p[t] = r["id"]
        for name in {m["provider"] for m in self.models.values()}:
            view.setdefault(name, {"usage": next((o["vendor"] for o in self.offerings
                                                   if self.models[o["model"]]["provider"] == name), "")})
        return view

    def model(self, provider: str, tier: str, now: datetime | None = None) -> str | None:
        """The provider's model at `tier` in the one-vendor view (`--catalog`'s `providers`).
        `routes` lists every route."""
        return self._view(self.routes(now or datetime.now(timezone.utc))).get(provider, {}).get(tier)

    def route(self, oid: str) -> dict | None:
        """The offering with this id, as `routes` gives it, whether live or not."""
        return next((self._route(o) for o in self.offerings if o["id"] == oid), None)

    def card(self, provider: str, model: str) -> tuple[dict | None, bool]:
        """What is published about the offering `model` (an id), and whether it is its own vendor's
        figures. Another vendor's card for the same id is a guideline only: price and speed are the
        vendor's, not the model's."""
        r = self.route(model)
        vendor = r["vendor"] if r else None
        mine = [c for c in self.cards if model in c.get("models", [])]
        own = next((c for c in mine if c["vendor"] == vendor), None)
        return (own, True) if own else (mine[0] if mine else None, False)

    def provider_of(self, model: str) -> str | None:
        """The maker of an offering id or a model name, or None."""
        r = self.route(model)
        if r is not None:
            return r["provider"]
        return self.models[model]["provider"] if model in self.models else None


def moment_utc(v: object) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        t = datetime.fromisoformat(v)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _fold_discovered(data: dict, local: Path | None) -> dict:
    """Discovery overlays the merged catalog: a live-discovered dispatchable route the catalog
    does not ship becomes a model at the `unproven` tier (provider `stealth` unless the maker is
    already known) with one offering on its vendor, id-prefixed as that vendor's shipped
    offerings are. Discovery never demotes or removes — a route the catalog ships stands as
    shipped, and whether a route still answers is outcomes' to say, not existence's."""
    if "unproven" not in data.get("tiers", []):
        return data
    discovered = read_discovered(discovered_path(local))
    if not discovered:
        return data
    prefix: dict[str, str] = {}
    for o in data.get("offerings", []):
        v, oid = o.get("vendor"), o.get("id")
        if isinstance(v, str) and v not in prefix and isinstance(oid, str):
            head, sep, _ = oid.partition("/")
            # Only a slashed id lends its head: a vendor whose shipped ids carry no prefix has
            # no discovery spelling, and discovery names none of its routes.
            if sep:
                prefix[v] = head
    models = {k: dict(v) for k, v in data.get("models", {}).items()}
    offerings = [dict(o) for o in data.get("offerings", [])]
    ids = {o["id"] for o in offerings if isinstance(o.get("id"), str)}
    for vendor, found in discovered.items():
        p = prefix.get(vendor)
        if p is None:
            continue
        for rid in found:
            oid = f"{p}/{rid}"
            if oid in ids:
                continue
            name = rid.replace(".", "-")
            m = models.get(name)
            tiers = m.get("tiers") if isinstance(m, dict) else None
            if m is None:
                models[name] = {"provider": "stealth", "tiers": ["unproven"]}
            elif isinstance(tiers, list) and "unproven" not in tiers:
                models[name] = dict(m, tiers=tiers + ["unproven"])
            offerings.append({"id": oid, "model": name, "vendor": vendor})
            ids.add(oid)
    return {**data, "models": models, "offerings": offerings}


def load_metadata(path: Path | None = None) -> Catalog:
    shipped = _parse(resources.files(__package__).joinpath("catalog.toml").read_text(), "shipped catalog")
    path = path or local_path()
    try:
        text = path.read_text()
    except FileNotFoundError:
        data = shipped
    except (OSError, UnicodeDecodeError) as e:
        raise CatalogError(f"{path}: {e}") from None
    else:
        data = _merge(shipped, _parse(text, str(path)))
    data = _fold_discovered(data, path)
    _check(data)
    return Catalog(data, local=path)


def load(path: Path | None = None, switches: Path | None = None) -> Catalog:
    cat = load_metadata(path)
    cat.off = read_switches(switches or switches_path(cat.local))
    return cat
