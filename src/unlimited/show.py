"""Readings for a person to read: one block per account, one row per usage window."""

from __future__ import annotations

import unicodedata
from collections import Counter

from datetime import datetime

from . import errors
from .projection import MIN_PAST
from .schema import moment

VENDOR_NAMES = {"anthropic": "Claude", "openai": "Codex", "zai": "Z.ai GLM", "opencode": "OpenCode", "xai": "Grok",
                "kimi": "Kimi Code", "neuralwatt": "Neuralwatt", "commandcode": "Command Code", "woyo": "Woyo",
                "mimo": "MiMo"}
WINDOW_NAMES = {"five_hour": "5-hour", "seven_day": "weekly", "month": "monthly", "codex": "weekly",
                "gpt-reserve": "weekly reserve", "seven_day_opus": "weekly Opus",
                "seven_day_sonnet": "weekly Sonnet", "period": "billing period",
                "api_credit": "API credit"}
BAR = 20
# Vendor plan words a person would not recognise, in the words they would.
PLANS = {"default_claude_max_20x": "Max 20x", "default_claude_max_5x": "Max 5x", "default_claude_ai": "Pro",
         "pro": "Pro", "plus": "Plus", "prolite": "Pro Lite", "max": "Max", "lite": "Lite",
         "individual-goat": "GOAT", "individual-go": "Go", "individual-pro": "Pro", "individual-pro-v1": "Pro",
         "individual-provider": "Provider", "individual-max": "Max", "individual-ultra": "Ultra", "teams-pro": "Teams Pro"}



def _label(l: dict, twins: Counter) -> str:
    """A window's row label; windows sharing a name are told apart by their scope."""
    name = (f"weekly {l['scope']}" if l.get("role") == "weekly_model" and l.get("scope")
            else WINDOW_NAMES.get(l.get("name"), WINDOW_NAMES.get(str(l.get("name")).split(":")[0], l.get("name"))))
    if l.get("scope") and l.get("role") != "weekly_model" and twins[str(l.get("name")).split(":")[0]] > 1:
        name = f"{name} {l['scope']}"
    return name


def _cols(text: str) -> int:
    """Terminal columns: a wide (CJK) character takes two."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _pad(text: str, width: int, wide: int = 0) -> str:
    return text + " " * max(max(width, wide) - _cols(text), 0)

def _plan(r: dict) -> str | None:
    p = r.get("plan")
    return PLANS.get(p, p) if p else ("Go" if r.get("vendor") == "opencode" and r.get("status") == "ok" else None)


def _until(t: datetime, now: datetime) -> str:
    seconds = (t - now).total_seconds()
    if seconds <= 0:
        return "now"
    if seconds < 60:
        return "under a minute"
    s = int(seconds)
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    return f"{d}d {h}h" if d else f"{h}h {m:02d}m" if h else f"{m}m"


def _paint(text: str, used: float | None, held: bool | None, color: bool) -> str:
    if not color:
        return text
    # Green to 85%, orange to 95%, red past it or when held.
    code = "2" if used is None and not held else "31" if held or used > 0.95 else \
        "38;5;208" if used > 0.85 else "32"
    return f"\033[{code}m{text}\033[0m"


ITALIC = "\033[3m"


def _forecast(p: dict, now: datetime, color: bool) -> str:
    """One line under a window: where it is heading and when it runs out. In italics while no
    past windows back it: this window's paces alone are a guess."""
    lo, hi = (round(x * 100) for x in p["at_reset"])
    text = "→ " + _paint(f"{lo}%" if lo == hi else f"{lo}–{hi}%", hi / 100, None, color) + " at reset"
    ends = moment(p.get("exhausts_at"))
    if ends:
        text += (f" · runs out in {_until(ends, now)}" if ends > now
                 else " · runs out now")
    if p.get("run_out") is not None:
        text += f" · {round(p['run_out'] * 100)}% chance of running out"
    guess = (p.get("past_windows") or 0) < MIN_PAST
    # _paint ends in a reset, so italics are reapplied after each painted run.
    return ITALIC + text.replace("\033[0m", "\033[0m" + ITALIC) + "\033[0m" if color and guess else text


def _expiry(t: datetime | None, now: datetime) -> str | None:
    return None if t is None else "expired" if t <= now else f"expires in {_until(t, now)}"


def _token_count(value: object) -> str:
    if not isinstance(value, int):
        return "?"
    if value < 1_000:
        return str(value)
    if value < 1_000_000:
        return f"{value / 1_000:.2f}K"
    return f"{value / 1_000_000:.2f}M"


def _local_pacing(period: dict) -> str | None:
    budget = period.get("local_budget")
    if not isinstance(budget, dict):
        return None
    profiles = budget.get("profiles")
    labels = [profile for profile in profiles if isinstance(profile, str)] if isinstance(profiles, list) else []
    suffix = f" · profiles {', '.join(labels)}" if labels else ""
    status = budget.get("status")
    if status == "invalid":
        return "    local pacing: configuration invalid" + suffix
    if status == "conflict":
        return "    local pacing: configuration conflict" + suffix
    total, limit = period.get("total_tokens"), budget.get("token_limit")
    if status != "ok" or not isinstance(total, int) or isinstance(total, bool) \
            or not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        return None
    text = f"    local pacing: {_token_count(total)} of {_token_count(limit)} ({total / limit * 100:.1f}%)"
    if period.get("coverage") == "partial":
        text += " · partial history"
    return text + suffix


def _estimated_cost(period: dict) -> str | None:
    cost = period.get("estimated_cost")
    if not isinstance(cost, dict):
        return None
    status, usd, budget = cost.get("status"), cost.get("usd"), cost.get("budget_usd")
    if status in {"invalid", "conflict"}:
        return f"    estimated cost: configuration {status}"
    if not isinstance(usd, (int, float)) or isinstance(usd, bool):
        return None
    text = f"    estimated cost: ${usd:,.2f}"
    if isinstance(budget, (int, float)) and not isinstance(budget, bool) and budget > 0:
        text += f" of ${budget:,.2f} ({usd / budget * 100:.1f}%)"
    unpriced = cost.get("unpriced_requests")
    if isinstance(unpriced, int) and not isinstance(unpriced, bool) and unpriced:
        text += f" · {unpriced} unpriced"
    return text


def _measured_usage(usage: object, now: datetime) -> list[str]:
    if not isinstance(usage, dict) or usage.get("source") != "gateway-log":
        return []
    lines = []
    for period in usage.get("periods", []):
        if not isinstance(period, dict) or period.get("window_minutes") not in (10080, 43200):
            continue
        label = "7d" if period["window_minutes"] == 10080 else "30d"
        coverage = period.get("coverage") if isinstance(period.get("coverage"), str) else "unknown"
        reasons = period.get("reasons") if isinstance(period.get("reasons"), list) else []
        note = coverage + (f" ({', '.join(r for r in reasons if isinstance(r, str))})" if reasons else "")
        total = period.get("total_tokens")
        details = [f"input {_token_count(period.get('input_tokens'))}", f"output {_token_count(period.get('output_tokens'))}"]
        for key, label_ in (("cache_read_tokens", "cache read"), ("cache_creation_tokens", "cache creation")):
            value = period.get(key)
            if isinstance(value, int) and value:
                details.append(f"{label_} {_token_count(value)}")
        lines.append(f"  rolling {label}: {_token_count(total)} logged tokens · {' · '.join(details)} · {note}")
        pacing = _local_pacing(period)
        if pacing is not None:
            lines.append(pacing)
        estimated_cost = _estimated_cost(period)
        if estimated_cost is not None:
            lines.append(estimated_cost)
    collected = moment(usage.get("collected_at"))
    if lines and collected:
        lines.append(f"    collected {_until(now, collected)} ago")
    return lines


def _credits(c: dict, taken: datetime | None, now: datetime, color: bool) -> str:
    """One row: what the account may spend once its windows are used, and whether that is on."""
    used, limit, bal, cur = c.get("used"), c.get("limit"), c.get("balance"), c.get("currency") or ""
    money = lambda v, unit=cur: f"{unit} {v:,.2f}".strip()
    on = bool(c.get("enabled"))
    # The vendor's reason code (e.g. org_level_disabled_until) means nothing to a person; it stays in the JSON.
    state = "on" if on else "off"
    if limit is None and not used and bal is None:
        return f"  {'credits':<15} " + _paint(state, None, None, color)
    frac = used / limit if limit and used is not None else None
    bar = ""
    if frac is not None:
        filled = min(round(frac * BAR), BAR)
        bar = _paint("█" * filled + "░" * (BAR - filled) + f" {frac * 100:5.1f}%", frac, not on, color) + "  "
    parts = [f"{money(used)} of {money(limit, '')}" if limit is not None and used is not None
             else f"{money(used)} used" if used is not None else None,
             f"{money(bal)} balance" if bal is not None else None, state,
             _expiry(moment(c.get("expires_at")), now)]
    age = moment(c.get("taken_at"))
    stale = f"  (read {_until(now, age)} ago)" if age and taken and (taken - age).total_seconds() >= 60 else ""
    return f"  {'credits':<15} {bar}" + " · ".join(p for p in parts if p) + stale


def _steering(reading: dict, color: bool, now: datetime | None = None) -> str:
    """The strongest live encouragement and discouragement, without inventing a net factor."""
    factors = [r.get("multiplier") for r in reading.get("steering", {}).get("routes", [])
               if now is None or (moment(r.get("until")) is not None and moment(r["until"]) > now)]
    up = max((x for x in factors if isinstance(x, (int, float)) and x > 1), default=None)
    down = min((x for x in factors if isinstance(x, (int, float)) and 0 < x < 1), default=None)
    marks = []
    for factor, glyph, code in ((up, "▲", "32"), (down, "▼", "31")):
        if factor is None:
            continue
        strength = max(factor, 1 / factor)
        text = glyph * (3 if strength >= 10 else 2 if strength >= 5 else 1)
        marks.append(f"\033[{code}m{text}\033[0m" if color else text)
    return "".join(marks)


def render(readings: list[dict], now: datetime, *, color: bool = False, all_limits: bool = False) -> str:
    label = lambda r: ", ".join(r.get("names") or [])
    lines = []
    for r in sorted(readings, key=lambda r: (VENDOR_NAMES.get(r.get("vendor"), r.get("vendor") or ""), label(r))):
        vendor = VENDOR_NAMES.get(r.get("vendor"), r.get("vendor"))
        who = label(r) or (r.get("account") or "?")[:8]
        taken = moment(r.get("taken_at"))
        notes = [f"read {_until(now, taken)} ago"] if taken and (now - taken).total_seconds() >= 60 else []
        if r.get("source") not in (None, "api"):
            notes.append(r["source"])
        wait = moment(r.get("retry_until"))
        if r.get("status") == "ok" and wait and wait > now:
            notes.append(f"refresh paused: next read in {_until(wait, now)}")
        plan = _plan(r)
        indicator = _steering(r, color, now)
        lines.append(f"{vendor}" + (f" {plan}" if plan else "") + f" · {who}" + (f" {indicator}" if indicator else "")
                     + (f"  ({', '.join(notes)})" if notes else ""))
        measured = _measured_usage(r.get("usage"), now)
        lines.extend(measured)
        if r.get("status") != "ok":
            description = errors.describe(r.get("why"))
            lines.extend([f"  ? {description['title']}", f"    {description['message']}"])
            if wait and wait > now:
                lines.append(f"    Retry in {_until(wait, now)}.")
            lines.append("")
            continue
        shown = [l for l in r.get("limits", []) if all_limits or
                 (l.get("role") is not None
                  and l.get("used_at_least") is not None or l.get("held"))]
        twins = Counter(str(l.get("name")).split(":")[0] for l in shown)
        labels = [_label(l, twins) for l in shown]
        wide = max(map(_cols, labels), default=0)
        for l, name in zip(shown, labels):
            used, held = l.get("used_at_least"), l.get("held")
            filled = round((used or 0) * BAR) if used is not None else 0
            bar = "█" * min(filled, BAR) + "░" * (BAR - min(filled, BAR))
            pct = f"{used * 100:5.1f}%" if used is not None else "    ?"
            resets = moment(l.get("resets_at"))
            when = (f"resets in {_until(resets, now):>7}  ({resets.astimezone():%a %H:%M})"
                    if resets else "no window open")
            flag = f"  HELD: {l.get('held_why') or 'yes'}" if held else ""
            lines.append(f"  {_pad(name, 15, wide)} " + _paint(f"{bar} {pct}", used, held, color) + f"  {when}{flag}")
            if l.get("projection") and not held and (used or 0) < 1:
                lines.append(" " * 18 + _forecast(l["projection"], now, color))
        if not shown and not r.get("credits") and not measured:
            lines.append("  no usage windows reported")
        if r.get("credits"):
            lines.append(_credits(r["credits"], taken, now, color))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
