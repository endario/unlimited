"""Each limit says what kind of window it is (`role`) and, where the vendor scopes it, to what
(`scope`), so a consumer never guesses from vendor names."""

from __future__ import annotations

import unittest

from test_golden import NOW, readings, iso, secs
from unlimited.adapters import anthropic, kimi, openai, opencode, xai, zai
from unlimited.schema import limit


def roles(r: dict) -> dict:
    return {l["name"]: (l["role"], l["scope"]) for l in r["limits"]}


class Roles(unittest.TestCase):
    def setUp(self):
        self.by = {r["vendor"]: roles(r) for r in readings()}

    def test_claude_names_its_weekly_its_model_weeklies_and_hides_its_duplicates(self):
        self.assertEqual(self.by["anthropic"], {
            "five_hour": ("session", None), "seven_day": ("weekly", None),
            "seven_day_opus": ("weekly_model", "Opus"), "nimbus_quill": (None, None),
            "limits:session": (None, None), "limits:weekly_all": (None, None),
            # Fable is reported only here; it is the one severity entry that is not a duplicate.
            "limits:weekly_scoped:Fable": ("weekly_model", "Fable")})

    def test_a_scoped_severity_entry_that_duplicates_a_model_window_stays_hidden(self):
        body = {"seven_day_opus": {"utilization": 12.0, "resets_at": iso(days=3)},
                "limits": [{"kind": "weekly_scoped", "group": "weekly", "percent": 12, "resets_at": iso(days=3),
                            "scope": {"model": {"display_name": "Opus"}}}]}
        got = {l["name"]: l["role"] for l in anthropic.limits(body, NOW)}
        self.assertEqual(got, {"seven_day_opus": "weekly_model", "limits:weekly_scoped:Opus": None})

    def test_a_weekly_bucket_that_is_not_a_known_model_is_an_extra_not_a_model_weekly(self):
        got = anthropic.limits({"seven_day_oauth_apps": {"utilization": 1, "resets_at": iso(days=1)}}, NOW)
        self.assertEqual([(l["role"], l["scope"], l["window_minutes"]) for l in got],
                         [("extra", "oauth_apps", 10080)])

    def test_a_statusline_capture_names_its_windows_like_the_api_does(self):
        got = {l["name"]: l["role"] for l in anthropic.statusline_limits(
            {"five_hour": {"used_percentage": 1, "resets_at": secs(hours=1)},
             "seven_day": {"used_percentage": 2, "resets_at": secs(days=1)}}, NOW)}
        self.assertEqual(got, {"five_hour": "session", "seven_day": "weekly"})

    def test_codex_main_window_is_named_by_its_length_and_extras_by_their_own_name(self):
        self.assertEqual(self.by["openai"], {"codex": ("weekly", None), "gpt-reserve": ("extra", "gpt-reserve")})
        plus = {"rate_limit": {"primary_window": {"used_percent": 1, "limit_window_seconds": 18000,
                                                  "reset_at": secs(hours=1)},
                               "secondary_window": {"used_percent": 2, "limit_window_seconds": 604800,
                                                    "reset_at": secs(days=1)}}}
        self.assertEqual(roles({"limits": openai.limits(plus, NOW)}),
                         {"codex": ("session", None), "codex (secondary)": ("weekly", None)})
        odd = {"rate_limit": {"primary_window": {"used_percent": 1, "limit_window_seconds": 86400,
                                                 "reset_at": secs(hours=1)}}}
        self.assertEqual(roles({"limits": openai.limits(odd, NOW)}), {"codex": ("extra", "codex")})

    def test_a_codex_session_log_names_its_windows_like_the_api_does(self):
        got = openai._session_limits({"limit_id": "codex", "primary": {"used_percent": 1, "window_minutes": 300,
                                                                       "resets_at": secs(hours=1)},
                                      "secondary": {"used_percent": 2, "window_minutes": 10080,
                                                    "resets_at": secs(days=1)}}, NOW)
        self.assertEqual(roles({"limits": got}), {"codex": ("session", None), "codex (secondary)": ("weekly", None)})
        extra = openai._session_limits({"limit_id": "x", "limit_name": "gpt-reserve", "primary": {
            "used_percent": 0, "window_minutes": 10080, "resets_at": secs(days=1)}}, NOW)
        self.assertEqual(roles({"limits": extra}), {"gpt-reserve": ("extra", "gpt-reserve")})

    def test_a_scoped_severity_entry_for_a_non_model_bucket_is_not_shown_twice(self):
        body = {"seven_day_haiku": {"utilization": 1, "resets_at": iso(days=1)},
                "limits": [{"kind": "weekly_scoped", "group": "weekly", "percent": 1, "resets_at": iso(days=1),
                            "scope": {"model": {"display_name": "Haiku"}}}]}
        self.assertEqual([l["role"] for l in anthropic.limits(body, NOW)], ["extra", None])

    def test_the_other_vendors_name_session_weekly_and_month(self):
        self.assertEqual(self.by["zai"], {"five_hour": ("session", None), "seven_day": ("weekly", None),
                                          "time_limit 5x1": (None, None)})
        self.assertEqual(self.by["kimi"], {"five_hour": ("session", None), "seven_day": ("weekly", None)})
        self.assertEqual(self.by["opencode"], {"five_hour": ("session", None), "seven_day": ("weekly", None),
                                               "month": ("month", None)})
        self.assertEqual(self.by["xai"], {"seven_day": ("weekly", None)})

    def test_a_real_window_nobody_recognises_is_extra_never_hidden(self):
        unknown = {
            "kimi": kimi.limits({"limits": [{"window": {"duration": 1, "timeUnit": "DAY"},
                                             "detail": {"used": "1", "limit": "10", "resetTime": iso(hours=1)}}]}, NOW),
            "zai": zai.limits({"data": {"limits": [{"type": "TOKENS_LIMIT", "unit": 3, "number": 24,
                                                    "percentage": 1, "nextResetTime": 0}]}}, NOW),
            "xai": xai.limits({"config": {"currentPeriod": {"start": iso(days=-1), "end": iso(days=1)}}}, NOW),
        }
        for vendor, got in unknown.items():
            with self.subTest(vendor):
                self.assertEqual([l["role"] for l in got], ["extra"])
        self.assertEqual(limit("rolling_3d", window_minutes=4320, used_at_least=0.1, resets_at=None,
                               held=None)["role"], "extra")


if __name__ == "__main__":
    unittest.main()
