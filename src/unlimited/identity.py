"""Account identity matching shared by local policy consumers."""

from __future__ import annotations


POLICY_PROJECTION = "unlimited-policy-keys-v1"


def policy_key(target: str, account: str | None = None) -> str:
    return target if account is None else f"{target}/{account}"


def policy_keys(target: str, value: object) -> list[str]:
    ordered = ordered_names(value) if isinstance(value, dict) else list(names(value))
    return [policy_key(target)] + [policy_key(target, account) for account in ordered]


def project_reading(reading: dict) -> dict:
    vendor = reading.get("vendor")
    keys = policy_keys(vendor, reading) if isinstance(vendor, str) and vendor.strip() else []
    return dict(reading, policy_projection=POLICY_PROJECTION, policy_keys=keys)


def names(value: object) -> set[str]:
    if isinstance(value, str) and value.strip():
        return {value}
    if isinstance(value, dict):
        return {x for x in [value.get("account")] + list(value.get("names") or []) if isinstance(x, str) and x}
    return set()


def ordered_names(reading: dict) -> list[str]:
    return [x for x in [reading.get("account")] + list(reading.get("names") or []) if isinstance(x, str) and x]


def reading_names(reading: dict) -> set[str]:
    return set(ordered_names(reading))


def matches(value: object, selector: str) -> bool:
    return selector in names(value)
