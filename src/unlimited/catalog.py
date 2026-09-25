"""What each provider runs at each tier, and the promotions tried first (docs/model-catalog).
The shipped `catalog.toml` is overridden by $XDG_CONFIG_HOME/unlimited/catalog.toml."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from importlib import resources
from pathlib import Path

SCHEMA = 1
TIERS = ("standard", "heavy")


class CatalogError(Exception):
    """The catalog cannot be read. A routing call that meets this must not start its run."""


@dataclass(frozen=True)
class Candidate:
    provider: str
    model: str
    promoted: bool


def local_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "unlimited" / "catalog.toml"


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
    if not all(isinstance(got.get(k, []), list) for k in ("promotions", "banned", "tie_preference")):
        raise CatalogError(f"{where}: promotions, banned and tie_preference must be lists")
    return got


def _merge(shipped: dict, local: dict) -> dict:
    out = dict(shipped)
    providers = {k: dict(v) for k, v in shipped.get("providers", {}).items()}
    for k, v in local.get("providers", {}).items():
        providers.setdefault(k, {}).update(v)
    out["providers"] = providers
    for whole in ("promotions", "tie_preference"):
        if whole in local:
            out[whole] = local[whole]
    out["banned"] = list(shipped.get("banned", [])) + list(local.get("banned", []))
    return out


def _check(c: dict) -> None:
    providers = c.get("providers")
    if not isinstance(providers, dict):
        raise CatalogError("no providers")
    for name, p in providers.items():
        if not isinstance(p, dict) or not isinstance(p.get("harness"), str) or not isinstance(p.get("usage"), str):
            raise CatalogError(f"provider {name}: needs harness and usage")
        for t in TIERS:
            if t in p and not isinstance(p[t], str):
                raise CatalogError(f"provider {name}: {t} is not a model id")
    for i, promo in enumerate(c.get("promotions", [])):
        ok = (isinstance(promo, dict) and isinstance(promo.get("provider"), str) and promo["provider"] in providers
              and isinstance(promo.get("model"), str)
              and isinstance(promo.get("tiers"), list) and all(t in TIERS for t in promo["tiers"])
              and isinstance(promo.get("until", date.max), date) and not isinstance(promo.get("until"), datetime))
        if not ok:
            raise CatalogError(f"promotion {i + 1}: needs a known provider, a model, tiers and an optional date")
    if not all(isinstance(m, str) for m in c.get("banned", [])):
        raise CatalogError("banned: model ids only")
    if not all(p in providers for p in c.get("tie_preference", [])):
        raise CatalogError("tie_preference: known providers only")
    owners: dict[str, set] = {}
    for name, p in providers.items():
        for t in TIERS:
            if t in p:
                owners.setdefault(p[t], set()).add(name)
    for promo in c.get("promotions", []):
        owners.setdefault(promo["model"], set()).add(promo["provider"])
    shared = sorted(m for m, who in owners.items() if len(who) > 1)
    if shared:
        raise CatalogError(f"listed under more than one provider: {', '.join(shared)}")


class Catalog:
    def __init__(self, data: dict):
        self.providers: dict[str, dict] = data["providers"]
        self.promotions: list[dict] = data.get("promotions", [])
        self.banned: frozenset[str] = frozenset(data.get("banned", []))
        self.tie_preference: list[str] = data.get("tie_preference", [])

    def _live(self, promo: dict, now: datetime) -> bool:
        return promo["model"] not in self.banned and now.astimezone(timezone.utc).date() <= promo.get("until", date.max)

    def candidates(self, tier: str, now: datetime) -> list[Candidate]:
        """Live promotions at `tier` first, in file order, then each provider's model at it."""
        out = [Candidate(p["provider"], p["model"], True) for p in self.promotions
               if tier in p["tiers"] and self._live(p, now)]
        out += [Candidate(name, p[tier], False) for name, p in self.providers.items()
                if tier in p and p[tier] not in self.banned]
        return out

    def to_json(self, now: datetime) -> dict:
        """The merged catalog as it stands at `now`: live promotions only, their dates as ISO text."""
        return {"schema": SCHEMA, "providers": self.providers, "banned": sorted(self.banned),
                "tie_preference": self.tie_preference,
                "promotions": [dict(p, until=p["until"].isoformat()) if "until" in p else dict(p)
                               for p in self.promotions if self._live(p, now)]}

    def model(self, provider: str, tier: str) -> str | None:
        m = self.providers.get(provider, {}).get(tier)
        return m if m not in self.banned else None

    def provider_of(self, model: str) -> str | None:
        """The provider that lists `model`, at a tier or in a promotion, or None."""
        for name, p in self.providers.items():
            if model in (p.get(t) for t in TIERS):
                return name
        return next((p["provider"] for p in self.promotions if p["model"] == model), None)


def load(path: Path | None = None) -> Catalog:
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
    return Catalog(data)
