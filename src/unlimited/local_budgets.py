"""Optional, local-only pacing and cost settings for measured usage."""

from __future__ import annotations

import math
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .credential import EnvKeys, account_of, env_value

_LIMITS = {10080: "UNLIMITED_WEEKLY_TOKEN_LIMIT", 43200: "UNLIMITED_MONTHLY_TOKEN_LIMIT"}
_USD_BUDGET = "UNLIMITED_WEEKLY_USD_LIMIT"
_MAX_TOKEN_LIMIT = 2**63 - 1
_PRICE_BUCKETS = ("INPUT", "OUTPUT", "CACHE_READ", "CACHE_WRITE")
_PRICE_MODELS = {
    "gpt-6-astra": "UNLIMITED_GPT_6_ASTRA",
    "gpt-6.1-sol": "UNLIMITED_GPT_6_1_SOL",
    "gpt-6-luna": "UNLIMITED_GPT_6_LUNA",
    "glm-5.3": "UNLIMITED_GLM_5_3",
    "glm-5.3-flash": "UNLIMITED_GLM_5_3_FLASH",
}


def attach(r: dict, keys: EnvKeys) -> dict:
    """Add current local pacing and cost facts without retaining settings in provider cache."""
    usage = r.get("usage")
    if not isinstance(usage, dict) or usage.get("source") != "gateway-log":
        return r
    periods = usage.get("periods")
    if not isinstance(periods, list):
        return r
    sources = _sources(r.get("account"), keys)
    config = _cost_config(sources)
    events = _events(r, usage)
    out_periods = []
    for period in periods:
        if not isinstance(period, dict):
            out_periods.append(period)
            continue
        decorated = dict(period)
        decorated.pop("local_budget", None)
        decorated.pop("estimated_cost", None)
        var = _LIMITS.get(period.get("window_minutes"))
        budget = _budget(sources, var) if var else None
        if budget is not None:
            decorated["local_budget"] = budget
        decorated["estimated_cost"] = _estimated_cost(period, events, config)
        out_periods.append(decorated)
    out = dict(r, usage=dict(usage, periods=out_periods))
    out.pop("budget", None)
    progress = budget_progress(out)
    if progress is not None:
        out["budget"] = progress
    return out


def budget_progress(reading: object) -> dict | None:
    """Normalize already-measured local USD progress without evaluating provider capacity."""
    from .schema import number

    usage = reading.get("usage") if isinstance(reading, dict) else None
    periods = usage.get("periods") if isinstance(usage, dict) else None
    if not isinstance(periods, list):
        return None
    period = next((p for p in periods if isinstance(p, dict) and p.get("window_minutes") == 10080), None)
    cost = period.get("estimated_cost") if period is not None else None
    if not isinstance(cost, dict):
        return None
    status = cost.get("status")
    if cost.get("budget_usd") is None and status not in ("invalid", "conflict"):
        return None
    try:
        usd, budget = number(cost.get("usd")), number(cost.get("budget_usd"))
    except OverflowError:
        usd = budget = None
    invalid = (status not in ("ok", "partial", "invalid", "conflict") or
               (cost.get("usd") is not None and (usd is None or usd < 0)) or
               budget is None or budget <= 0)
    if usd is not None and usd < 0:
        usd = None
    if budget is not None and budget <= 0:
        budget = None
    unpriced = cost.get("unpriced_requests")
    if type(unpriced) is not int or not 0 <= unpriced <= _MAX_TOKEN_LIMIT:
        unpriced, invalid = 0, True
    coverage = period.get("coverage")
    if coverage not in ("observed", "partial", "unknown"):
        coverage = "unknown"
    used = usd / budget if usd is not None and budget is not None and not invalid else None
    if used is not None and not math.isfinite(used):
        used, invalid = None, True
    if status in ("invalid", "conflict"):
        state, used = status, None
    elif invalid:
        state, used = "invalid", None
    elif used is not None and used >= 1:
        state = "exceeded"
    elif used is not None and status == "ok" and coverage == "observed" and unpriced == 0:
        state = "ok"
    else:
        state = "partial"
    return {"source": "local-market-value", "window_type": "rolling", "window_minutes": 10080,
            "state": state, "used": used, "usd": usd, "budget_usd": budget,
            "coverage": coverage, "unpriced_requests": unpriced}


def _events(r: dict, usage: dict) -> list[dict] | None:
    if usage.get("collected_at") is None:
        return None
    from . import usage as ledger
    return ledger.events(r)


def _sources(account: object, keys: EnvKeys) -> list[tuple[str, Path | None]]:
    if not isinstance(account, str):
        return []
    files = [(path.stem, path) for path in keys.files()
             if (key := env_value(path, keys.var)) is not None and account_of(key) == account]
    if files:
        return files
    key = os.environ.get(keys.var)
    return [("environment", None)] if key and account_of(key) == account else []


def _budget(sources: list[tuple[str, Path | None]], var: str) -> dict | None:
    configured = [(profile, _value(path, var)) for profile, path in sources]
    configured = [(profile, value) for profile, value in configured if value is not None]
    if not configured:
        return None
    profiles = list(dict.fromkeys(profile for profile, _ in configured))
    parsed = [_positive_integer(value) for _, value in configured]
    if any(value is None for value in parsed):
        return {"status": "invalid", "token_limit": None, "profiles": profiles}
    limits = set(parsed)
    if len(limits) != 1:
        return {"status": "conflict", "token_limit": None, "profiles": profiles}
    return {"status": "ok", "token_limit": limits.pop(), "profiles": profiles}


def _cost_config(sources: list[tuple[str, Path | None]]) -> tuple[str, float | None, dict[str, dict[str, Decimal | None]]]:
    budget_status, budget = _decimal_setting(sources, _USD_BUDGET, positive=True)
    prices: dict[str, dict[str, Decimal | None]] = {}
    states = [budget_status]
    for model, prefix in _PRICE_MODELS.items():
        rates = {}
        for bucket in _PRICE_BUCKETS:
            status, value = _decimal_setting(sources, f"{prefix}_{bucket}_USD_PER_MILLION", positive=False)
            states.append(status)
            rates[bucket] = value
        prices[model] = rates
    if "invalid" in states:
        return "invalid", None, prices
    if "conflict" in states:
        return "conflict", None, prices
    return "ok", _native_number(budget), prices


def _decimal_setting(sources: list[tuple[str, Path | None]], var: str, *, positive: bool) -> tuple[str, Decimal | None]:
    values = [_value(path, var) for _, path in sources]
    values = [value for value in values if value is not None]
    if not values:
        return "unset", None
    parsed = [_decimal(value, positive=positive) for value in values]
    if any(value is None for value in parsed):
        return "invalid", None
    unique = set(parsed)
    return ("ok", unique.pop()) if len(unique) == 1 else ("conflict", None)


def _estimated_cost(period: dict, events: list[dict] | None,
                    config: tuple[str, float | None, dict[str, dict[str, Decimal | None]]]) -> dict:
    status, weekly_budget, prices = config
    budget = weekly_budget if period.get("window_minutes") == 10080 else None
    if status != "ok":
        return {"usd": None, "budget_usd": None, "unpriced_requests": 0, "status": status}
    if events is None:
        return {"usd": None, "budget_usd": budget, "unpriced_requests": 0, "status": "partial"}
    starts, ends = _moment(period.get("starts_at")), _moment(period.get("ends_at"))
    if starts is None or ends is None:
        return {"usd": None, "budget_usd": budget, "unpriced_requests": 0, "status": "partial"}
    total = Decimal(0)
    priced = 0
    unpriced = 0
    for event in events:
        at = _moment(event.get("at"))
        if at is None or not starts <= at < ends:
            continue
        cost = _request_cost(event, prices)
        if cost is None:
            unpriced += 1
        else:
            priced += 1
            total += cost
    usd = _native_number(total) if priced or not unpriced else None
    if usd is None:
        return {"usd": None, "budget_usd": budget, "unpriced_requests": unpriced, "status": "partial"}
    return {"usd": usd, "budget_usd": budget, "unpriced_requests": unpriced,
            "status": "partial" if unpriced else "ok"}


def _request_cost(event: dict, prices: dict[str, dict[str, Decimal | None]]) -> Decimal | None:
    model = _model(event.get("model"))
    rates = prices.get(model) if model else None
    if rates is None or any(rates[bucket] is None for bucket in _PRICE_BUCKETS):
        return None
    values = [event.get(key) for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")]
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
        return None
    input_tokens, output_tokens, cache_read, cache_write = values
    normalized = input_tokens - cache_read - cache_write
    if normalized < 0:
        return None
    multiplier = Decimal(2) if model in {"gpt-6-astra", "gpt-6.1-sol", "gpt-6-luna"} and input_tokens > 272_000 else Decimal(1)
    output_multiplier = Decimal("1.5") if multiplier == 2 else Decimal(1)
    return (Decimal(normalized) * rates["INPUT"] * multiplier
            + Decimal(cache_read) * rates["CACHE_READ"] * multiplier
            + Decimal(cache_write) * rates["CACHE_WRITE"] * multiplier
            + Decimal(output_tokens) * rates["OUTPUT"] * output_multiplier) / Decimal(1_000_000)


def _model(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip().casefold().rsplit("/", 1)[-1] or None


def _moment(value: object):
    from .schema import moment
    return moment(value)


def _native_number(value: Decimal | None) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (OverflowError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _value(path: Path | None, var: str) -> str | None:
    if path is not None:
        return env_value(path, var)
    value = os.environ.get(var)
    if value is None:
        return None
    return value.strip() or None


def _positive_integer(value: str) -> int | None:
    if not value or not value.isascii() or not value.isdecimal():
        return None
    significant = value.lstrip("0")
    if not significant or len(significant) > len(str(_MAX_TOKEN_LIMIT)):
        return None
    parsed = int(significant)
    return parsed if parsed <= _MAX_TOKEN_LIMIT else None


def _decimal(value: str, *, positive: bool) -> Decimal | None:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or (parsed <= 0 if positive else parsed < 0):
        return None
    native = _native_number(parsed)
    return parsed if native is not None and (not positive or native > 0) else None
