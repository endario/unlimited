"""Which models each provider offers at each tier, and what else the catalog says (docs/catalog.md).
The shipped `catalog.toml` is overridden by $XDG_CONFIG_HOME/unlimited/catalog.toml."""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from importlib import resources
from pathlib import Path

SCHEMA = 1
# A card's prices, USD per million tokens.
PRICES = ("input", "output", "cache_read", "cache_write")


def _number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0


class CatalogError(Exception):
    """The catalog cannot be read. A routing call that meets this must not start its run."""


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


def read_switches(path: Path | None = None) -> list[dict]:
    """This machine's switched-off targets: `{"target", "until", "why"}`, `until` an ISO time or
    None. Written by `unlimited models off/on`; a file that does not parse is an error, as the
    catalog's is, since a switch that silently stops applying is the failure it exists to prevent."""
    path = path or switches_path()
    try:
        got = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        raise CatalogError(f"{path}: {e}") from None
    off = got.get("off") if isinstance(got, dict) else None
    if not isinstance(off, list) or not all(isinstance(x, dict) and isinstance(x.get("target"), str) for x in off):
        raise CatalogError(f"{path}: expected {{\"off\": [{{\"target\": ...}}]}}")
    for x in off:
        if x.get("until") is not None and moment_utc(x["until"]) is None:
            raise CatalogError(f"{path}: {x['target']}: until {x['until']!r} is not an ISO time")
    return off


def write_switches(off: list[dict], path: Path | None = None) -> None:
    path = path or switches_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"off": off}, indent=1) + "\n")
    os.replace(tmp, path)


def _parse(text: str, where: str) -> dict:
    try:
        got = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise CatalogError(f"{where}: {e}") from None
    if got.get("schema") != SCHEMA:
        raise CatalogError(f"{where}: schema {got.get('schema')!r}, expected {SCHEMA}")
    providers = got.get("providers", {})
    if not isinstance(providers, dict) or not all(isinstance(p, dict) for p in providers.values()):
        raise CatalogError(f"{where}: providers must be tables")
    if not all(isinstance(got.get(k, []), list) for k in ("tiers", "promotions", "banned", "tie_preference", "cards")):
        raise CatalogError(f"{where}: tiers, promotions, banned, tie_preference and cards must be lists")
    return got


def _merge(shipped: dict, local: dict) -> dict:
    out = dict(shipped)
    providers = {k: dict(v) for k, v in shipped.get("providers", {}).items()}
    for k, v in local.get("providers", {}).items():
        providers.setdefault(k, {}).update(v)
    out["providers"] = providers
    for whole in ("tiers", "promotions", "tie_preference"):
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


def _check(c: dict) -> None:
    providers = c.get("providers")
    if not isinstance(providers, dict):
        raise CatalogError("no providers")
    tiers = c.get("tiers")
    if not (isinstance(tiers, list) and tiers and all(isinstance(t, str) for t in tiers)):
        raise CatalogError("tiers: a list of names")
    for name, p in providers.items():
        # Any other key is the reader's own (a caller's launch details, say): kept, never read.
        if not isinstance(p, dict) or not isinstance(p.get("usage"), str):
            raise CatalogError(f"provider {name}: needs usage, the vendor whose account a run spends")
        for t in tiers:
            if t in p and not isinstance(p[t], str):
                raise CatalogError(f"provider {name}: {t} is not a model id")
    for i, promo in enumerate(c.get("promotions", [])):
        ok = (isinstance(promo, dict) and isinstance(promo.get("provider"), str) and promo["provider"] in providers
              and isinstance(promo.get("model"), str)
              and isinstance(promo.get("tiers"), list) and all(t in tiers for t in promo["tiers"])
              and isinstance(promo.get("until", date.max), date) and not isinstance(promo.get("until"), datetime))
        if not ok:
            raise CatalogError(f"promotion {i + 1}: needs a known provider, a model, tiers and an optional date")
    for i, card in enumerate(c.get("cards", [])):
        ok = (isinstance(card, dict) and all(isinstance(card.get(k), str) for k in ("vendor", "name", "source"))
              and isinstance(card.get("models", []), list) and all(isinstance(m, str) for m in card.get("models", []))
              and isinstance(card.get("plan", ""), str) and isinstance(card.get("price", {}), dict)
              and all(_number(card[k]) for k in ("intelligence", "tok_s") if k in card)
              and all(k in PRICES and _number(v) for k, v in card.get("price", {}).items())
              and isinstance(card.get("as_of"), date) and not isinstance(card.get("as_of"), datetime))
        if not ok:
            raise CatalogError(f"card {i + 1}: needs vendor, name, source, as_of; models, plan, "
                               f"intelligence, tok_s and price ({', '.join(PRICES)}) are optional")
    if not all(isinstance(m, str) for m in c.get("banned", [])):
        raise CatalogError("banned: model ids only")
    if not all(p in providers for p in c.get("tie_preference", [])):
        raise CatalogError("tie_preference: known providers only")
    owners: dict[str, set] = {}
    for name, p in providers.items():
        for t in tiers:
            if t in p:
                owners.setdefault(p[t], set()).add(name)
    for promo in c.get("promotions", []):
        owners.setdefault(promo["model"], set()).add(promo["provider"])
    shared = sorted(m for m, who in owners.items() if len(who) > 1)
    if shared:
        raise CatalogError(f"listed under more than one provider: {', '.join(shared)}")


class Catalog:
    def __init__(self, data: dict, off: list[dict] | None = None):
        self.providers: dict[str, dict] = data["providers"]
        self.tiers: list[str] = data["tiers"]
        self.promotions: list[dict] = data.get("promotions", [])
        self.banned: frozenset[str] = frozenset(data.get("banned", []))
        self.tie_preference: list[str] = data.get("tie_preference", [])
        self.cards: list[dict] = data.get("cards", [])
        # Switched off on this machine: a provider, a model id, or `provider:model`.
        self.off: list[dict] = off or []

    def blocked(self, provider: str, model: str, now: datetime) -> bool:
        """Banned, or switched off here until a time not yet reached (or with no end)."""
        if model in self.banned:
            return True
        for x in self.off:
            until = moment_utc(x.get("until"))
            if until is not None and until <= now:
                continue
            if x["target"] in (provider, model, f"{provider}:{model}"):
                return True
        return False

    def _live(self, promo: dict, now: datetime) -> bool:
        return (not self.blocked(promo["provider"], promo["model"], now)
                and now.astimezone(timezone.utc).date() <= promo.get("until", date.max))

    def candidates(self, tier: str, now: datetime) -> list[Candidate]:
        """Live promotions at `tier` first, in file order, then each provider's model at it."""
        out = [Candidate(p["provider"], p["model"], True) for p in self.promotions
               if tier in p["tiers"] and self._live(p, now)]
        out += [Candidate(name, p[tier], False) for name, p in self.providers.items()
                if tier in p and not self.blocked(name, p[tier], now)]
        return out

    def to_json(self, now: datetime) -> dict:
        """The merged catalog as it stands at `now`: live promotions only, their dates as ISO text,
        and a switched-off model dropped from its provider's tiers."""
        providers = {name: {k: v for k, v in p.items() if not (k in self.tiers and self.blocked(name, v, now))}
                     for name, p in self.providers.items()}
        return {"schema": SCHEMA, "tiers": self.tiers, "providers": providers, "banned": sorted(self.banned),
                "tie_preference": self.tie_preference,
                "promotions": [dict(p, until=p["until"].isoformat()) if "until" in p else dict(p)
                               for p in self.promotions if self._live(p, now)],
                "cards": [dict(c, as_of=c["as_of"].isoformat()) for c in self.cards]}

    def model(self, provider: str, tier: str, now: datetime | None = None) -> str | None:
        m = self.providers.get(provider, {}).get(tier)
        return m if m is not None and not self.blocked(provider, m, now or datetime.now(timezone.utc)) else None

    def card(self, provider: str, model: str) -> tuple[dict | None, bool]:
        """What is published about `model` as `provider` runs it, and whether it is that route's own
        vendor's figures. A model another vendor also sells has only that vendor's card as a
        guideline until its own is added: price and speed are the vendor's, not the model's."""
        vendor = self.providers.get(provider, {}).get("usage")
        mine = [c for c in self.cards if model in c.get("models", [])]
        own = next((c for c in mine if c["vendor"] == vendor), None)
        return (own, True) if own else (mine[0] if mine else None, False)

    def provider_of(self, model: str) -> str | None:
        """The provider that lists `model`, at a tier or in a promotion, or None."""
        for name, p in self.providers.items():
            if model in (p.get(t) for t in self.tiers):
                return name
        return next((p["provider"] for p in self.promotions if p["model"] == model), None)


def moment_utc(v: object) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        t = datetime.fromisoformat(v)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def load(path: Path | None = None, switches: Path | None = None) -> Catalog:
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
    _check(data)
    return Catalog(data, read_switches(switches or switches_path(path)))
