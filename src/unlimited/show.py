"""Readings for a person to read: one block per account, one row per usage window."""

from __future__ import annotations

from datetime import datetime

from .projection import MIN_PAST
from .schema import moment

VENDOR_NAMES = {"anthropic": "Claude", "openai": "Codex", "zai": "Z.ai GLM", "opencode": "OpenCode", "xai": "Grok"}
WINDOW_NAMES = {"five_hour": "5-hour", "seven_day": "weekly", "month": "monthly", "codex": "weekly",
                "gpt-reserve": "weekly reserve", "seven_day_opus": "weekly Opus",
                "seven_day_sonnet": "weekly Sonnet", "period": "billing period"}
BAR = 20
# Vendor plan words a person would not recognise, in the words they would.
PLANS = {"default_claude_max_20x": "Max 20x", "default_claude_max_5x": "Max 5x", "default_claude_ai": "Pro",
         "pro": "Pro", "plus": "Plus", "prolite": "Pro Lite", "max": "Max", "lite": "Lite"}


def _plan(r: dict) -> str | None:
    p = r.get("plan")
    return PLANS.get(p, p) if p else ("Go" if r.get("vendor") == "opencode" and r.get("status") == "ok" else None)


def _claude_dirs() -> dict[str, str]:
    """Claude account id → the config directories signed in to it, by their short name."""
    from .adapters import anthropic
    out: dict[str, list[str]] = {}
    for d in anthropic.config_dirs():
        who = anthropic.account_of(d)
        if who:
            name = d.name.removeprefix(".claude").lstrip("-") or "default"
            out.setdefault(who, []).append(name)
    return {k: ", ".join(v) for k, v in out.items()}


def _until(t: datetime, now: datetime) -> str:
    s = int((t - now).total_seconds())
    if s <= 0:
        return "now"
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    return f"{d}d {h}h" if d else f"{h}h {m:02d}m" if h else f"{m}m"


def _paint(text: str, used: float | None, held: bool | None, color: bool) -> str:
    if not color:
        return text
    # Green to 85%, orange to 95%, red past it or when held.
    code = "2" if used is None and not held else "31" if held or used > 0.95 else \
        "38;5;208" if used > 0.85 else "32"
    return f"\033[{code}m{text}\033[0m"


def _forecast(p: dict, now: datetime, color: bool) -> str:
    """One line under a window: where it is heading, when it runs out, and what that rests on."""
    lo, hi = (round(x * 100) for x in p["at_reset"])
    text = "→ " + _paint(f"{lo}%" if lo == hi else f"{lo}–{hi}%", hi / 100, None, color) + " at reset"
    ends = moment(p.get("exhausts_at"))
    if ends:
        text += (f" · runs out {ends.astimezone():%a %H:%M} (in {_until(ends, now)})" if ends > now
                 else " · runs out now")
    if p.get("run_out") is not None:
        text += f" · {round(p['run_out'] * 100)}% chance of running out"
    n = p.get("past_windows") or 0
    if n >= MIN_PAST:
        text += f" · from {n} past windows"
    return text


def render(readings: list[dict], now: datetime, *, color: bool = False, all_limits: bool = False) -> str:
    labels = _claude_dirs() if any(r.get("vendor") == "anthropic" for r in readings) else {}
    lines = []
    for r in sorted(readings, key=lambda r: (r.get("vendor") or "", labels.get(r.get("account"), ""))):
        vendor = VENDOR_NAMES.get(r.get("vendor"), r.get("vendor"))
        who = labels.get(r.get("account")) or (r.get("account") or "?")[:8]
        taken = moment(r.get("taken_at"))
        notes = [f"read {_until(now, taken)} ago"] if taken and (now - taken).total_seconds() >= 60 else []
        if r.get("source") not in (None, "api"):
            notes.append(r["source"])
        wait = moment(r.get("retry_until"))
        if r.get("status") == "ok" and wait and wait > now:
            notes.append(f"throttled: next read in {_until(wait, now)}")
        plan = _plan(r)
        lines.append(f"{vendor}" + (f" {plan}" if plan else "") + f" · {who}" + (f"  ({', '.join(notes)})" if notes else ""))
        if r.get("status") != "ok":
            until = moment(r.get("retry_until"))
            lines.append(_paint(f"  not read: {r.get('why') or r.get('status')}"
                                + (f", retry in {_until(until, now)}" if until and until > now else ""),
                                None, None, color))
            lines.append("")
            continue
        shown = [l for l in r.get("limits", []) if all_limits or
                 (l.get("window_minutes") and not str(l.get("name", "")).startswith("limits:")
                  and l.get("used_at_least") is not None or l.get("held"))]
        for l in shown:
            used, held = l.get("used_at_least"), l.get("held")
            name = WINDOW_NAMES.get(l.get("name"), l.get("name"))
            filled = round((used or 0) * BAR) if used is not None else 0
            bar = "█" * min(filled, BAR) + "░" * (BAR - min(filled, BAR))
            pct = f"{used * 100:5.1f}%" if used is not None else "    ?"
            resets = moment(l.get("resets_at"))
            when = (f"resets in {_until(resets, now):>7}  ({resets.astimezone():%a %H:%M})"
                    if resets else "no window open")
            flag = f"  HELD: {l.get('held_why') or 'yes'}" if held else ""
            lines.append(f"  {name:<15} " + _paint(f"{bar} {pct}", used, held, color) + f"  {when}{flag}")
            if l.get("projection") and not held and (used or 0) < 1:
                lines.append(" " * 18 + _forecast(l["projection"], now, color))
        if not shown:
            lines.append("  no usage windows reported")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
