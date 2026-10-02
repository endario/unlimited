"""Account identity matching shared by local policy consumers."""

from __future__ import annotations


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
