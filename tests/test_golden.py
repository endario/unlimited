"""What a person sees for one realistic reading per vendor, pinned byte for byte. A change to
which windows are shown must change this file on purpose."""

from __future__ import annotations

import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from unlimited import show
from unlimited.adapters import anthropic, kimi, opencode, openai, xai, zai
from unlimited.schema import reading

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
GOLDEN = Path(__file__).parent / "fixtures" / "show_golden.txt"
iso = lambda **d: (NOW + timedelta(**d)).isoformat()
ms = lambda **d: int((NOW + timedelta(**d)).timestamp() * 1000)
secs = lambda **d: int((NOW + timedelta(**d)).timestamp())


def readings() -> list[dict]:
    claude = {"five_hour": {"utilization": 9.0, "resets_at": iso(hours=2)},
              "seven_day": {"utilization": 40.0, "resets_at": iso(days=3)},
              "seven_day_opus": {"utilization": 12.0, "resets_at": iso(days=3)},
              "nimbus_quill": {"utilization": 0},
              "limits": [{"kind": "session", "group": "session", "percent": 9, "resets_at": iso(hours=2),
                          "severity": "normal", "is_active": False},
                         {"kind": "weekly_all", "group": "weekly", "percent": 40, "resets_at": iso(days=3),
                          "severity": "normal", "is_active": True},
                         {"kind": "weekly_scoped", "group": "weekly", "percent": 3, "resets_at": iso(days=3),
                          "scope": {"model": {"display_name": "Fable"}}, "severity": "normal", "is_active": False}]}
    codex = {"rate_limit": {"primary_window": {"used_percent": 91, "limit_window_seconds": 604800,
                                               "reset_at": secs(days=1)}},
             "additional_rate_limits": [{"limit_name": "gpt-reserve", "rate_limit": {
                 "primary_window": {"used_percent": 0, "limit_window_seconds": 604800, "reset_at": secs(days=1)}}}]}
    glm = {"data": {"limits": [
        {"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "percentage": 26, "nextResetTime": ms(hours=3)},
        {"type": "TOKENS_LIMIT", "unit": 6, "number": 1, "percentage": 5, "nextResetTime": ms(days=5)},
        {"type": "TIME_LIMIT", "unit": 5, "number": 1, "usage": 100, "currentValue": 7, "nextResetTime": ms(days=20)}]}}
    kimi_body = {"limits": [{"window": {"duration": 300, "timeUnit": "MINUTE"},
                             "detail": {"used": "10", "limit": "100", "resetTime": iso(hours=1)}}],
                 "usage": {"used": "81", "limit": "100", "resetTime": iso(days=4)}}
    go = {"usage": {"rolling": {"status": "ok", "percent": 2, "resetsAt": iso(hours=1)},
                    "weekly": {"status": "ok", "percent": 78, "resetsAt": iso(days=3)},
                    "monthly": {"status": "ok", "percent": 40, "resetsAt": iso(days=20)}}}
    grok = {"config": {"currentPeriod": {"type": "USAGE_PERIOD_TYPE_WEEKLY", "start": iso(days=-2),
                                         "end": iso(days=5)}, "creditUsagePercent": 33}}
    return [
        reading("anthropic", "a" * 8, NOW, "ok", plan="default_claude_max_20x",
                limits=anthropic.limits(claude, NOW)),
        reading("openai", "o" * 8, NOW, "ok", plan="pro", limits=openai.limits(codex, NOW)),
        reading("zai", "z" * 8, NOW, "ok", plan="max", limits=zai.limits(glm, NOW)),
        reading("kimi", "k" * 8, NOW, "ok", limits=kimi.limits(kimi_body, NOW)),
        reading("opencode", "g" * 8, NOW, "ok", limits=opencode.limits(go, NOW)),
        reading("xai", "x" * 8, NOW, "ok", plan="SuperGrok", limits=xai.limits(grok, NOW)),
    ]


def rendered() -> str:
    # Reset times print in local time; pin it so the file reads the same on every machine.
    try:
        with mock.patch.dict(os.environ, {"TZ": "UTC"}):
            time.tzset()
            return _rendered()
    finally:
        time.tzset()


def _rendered() -> str:
    return show.render(readings(), NOW)


class Golden(unittest.TestCase):
    def test_every_vendor_renders_as_pinned(self):
        self.assertEqual(rendered(), GOLDEN.read_text())


if __name__ == "__main__":
    GOLDEN.write_text(rendered())
