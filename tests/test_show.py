"""The status view: what a person sees for each account."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from unlimited import show
from unlimited.credential import account_of
from unlimited.schema import credits, limit, reading

import scratch

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


class Render(unittest.TestCase):
    def test_windows_show_with_plan_and_reset_and_severity_entries_stay_hidden(self):
        r = reading("openai", "acct-1234567", NOW, "ok", plan="pro", limits=[
            limit("codex", window_minutes=10080, used_at_least=0.5, resets_at=NOW + timedelta(days=2), held=False),
            limit("limits:session", window_minutes=300, used_at_least=0.1, resets_at=None, held=None,
                  severity="normal")])
        out = show.render([r], NOW)
        self.assertIn("Codex Pro · acct-123\n", out, "a fresh reading needs no note")
        self.assertIn("weekly", out)
        self.assertIn("50.0%", out)
        self.assertRegex(out, r"resets in +2d 0h")
        self.assertNotIn("limits:session", out)
        self.assertIn("limits:session", show.render([r], NOW, all_limits=True))

    def test_bars_start_in_one_column_whatever_a_labels_width(self):
        # A per-model weekly label runs past the default column, and a CJK one takes two per character.
        r = reading("anthropic", "a", NOW, "ok", limits=[
            limit("five_hour", window_minutes=300, used_at_least=0.5, resets_at=None, held=False),
            dict(limit("seven_day_opus", window_minutes=10080, used_at_least=0.5, resets_at=None, held=False),
                 role="weekly_model", scope="claude-opus-4-5-thinking"),
            dict(limit("seven_day_x", window_minutes=10080, used_at_least=0.5, resets_at=None, held=False),
                 role="weekly_model", scope="当前账号")])
        rows = [l for l in show.render([r], NOW).splitlines() if "█" in l]
        cols = {sum(2 if ord(c) > 0x2e80 else 1 for c in row[:row.index("█")]) for row in rows}
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(cols), 1, rows)

    def test_vendor_blocks_sort_by_their_displayed_name_not_their_internal_id(self):
        # "kimi" < "openai" as internal ids, but "Codex" must still print before "Kimi Code".
        readings = [reading("kimi", "k", NOW, "ok", limits=[]), reading("openai", "o", NOW, "ok", limits=[])]
        out = show.render(readings, NOW)
        self.assertLess(out.index("Codex"), out.index("Kimi Code"))

    def test_an_account_is_labeled_by_its_names(self):
        r = reading("zai", "z" * 16, NOW, "ok", plan="max", limits=[])
        self.assertIn("Z.ai GLM Max · zzzzzzzz\n", show.render([r], NOW), "no names: the id, shortened")
        r["names"] = ["claude-glm", "claude-glm-2"]
        self.assertIn("Z.ai GLM Max · claude-glm, claude-glm-2\n", show.render([r], NOW))


    def credits(self, **kw):
        return credits(NOW, **{"enabled": True, "used": 0.0, "limit": 200.0, "balance": None,
                               "currency": "SGD", **kw})

    def test_credits_show_what_is_left_to_spend_past_the_windows_and_whether_it_is_on(self):
        show_ = lambda c: show.render([reading("anthropic", "a", NOW, "ok", credits=c)], NOW)
        self.assertIn("SGD 12.50 of 200.00 · on", show_(self.credits(used=12.5)))
        blocked = show_(self.credits(used=150.62, limit=150.0, enabled=False,
                                     disabled_reason="org_level_disabled_until"))
        self.assertIn("100.4%", blocked)
        self.assertIn("SGD 150.62 of 150.00 · off", blocked)
        self.assertNotIn("org_level", blocked, "a vendor's reason code is for machines")
        self.assertIn("SGD 40.00 balance", show_(self.credits(balance=40.0)))
        self.assertRegex(show_(self.credits(limit=None, used=0.0, enabled=False)), r"credits +off\n")
        self.assertNotIn("credits", show.render([reading("openai", "a", NOW, "ok")], NOW))

    def test_credits_older_than_the_reading_carrying_them_say_how_old(self):
        old = credits(NOW - timedelta(hours=2), enabled=True, used=0.0, limit=10.0, balance=None, currency="SGD")
        out = show.render([reading("anthropic", "a", NOW, "ok", credits=old)], NOW)
        self.assertIn("(read 2h 00m ago)", out.split("credits")[1])

    def test_an_unread_account_explains_the_failure_and_retry_delay(self):
        r = reading("zai", "z", NOW, "refused", why="http-429", retry_until=NOW + timedelta(minutes=5))
        out = show.render([r], NOW)
        self.assertIn("? Usage service busy", out)
        self.assertIn("The provider is limiting usage requests", out)
        self.assertIn("Retry in 5m", out)
        self.assertNotIn("http-429", out)

    def test_failures_are_explained_in_words_instead_of_reason_codes(self):
        cases = [("signed-out", "Signed out", "Sign in"),
                 ("credential-expired", "Sign-in expired", "Sign in"),
                 ("no-credential", "No sign-in found", "Sign in"),
                 ("http-401", "Sign-in rejected", "Sign in"),
                 ("http-403", "Access denied", "access permissions"),
                 ("unreachable", "Cannot reach usage service", "Check your connection"),
                 ("not-json", "Invalid usage response", "could not read"),
                 ("not-an-object", "Invalid usage response", "could not read"),
                 ("no-limits", "No usage limits reported", "no usage limits"),
                 ("no-subscription", "No subscription found", "Check its plan"),
                 ("vendor-refused", "Usage request refused", "provider refused"),
                 ("http-503", "Usage unavailable", "temporarily unavailable"),
                 ("http-418", "Usage unavailable", "418"),
                 ("new-reason", "Usage unavailable", "Refresh")]
        for why, title, hint in cases:
            with self.subTest(why=why):
                r = reading("anthropic", "a", NOW, "unread", why=why)
                out = show.render([r], NOW)
                self.assertIn("? " + title, out)
                self.assertIn(hint, out)
                self.assertEqual(r["why"], why, "human formatting must not rewrite the machine reason")

    def test_a_subminute_retry_does_not_tell_the_user_to_wait_zero_minutes(self):
        r = reading("anthropic", "a", NOW, "refused", why="http-401", retry_until=NOW + timedelta(seconds=30))
        out = show.render([r], NOW)
        self.assertIn("Retry in under a minute", out)
        self.assertNotIn("Retry in 0m", out)

    def test_a_retained_reading_reports_a_subminute_refresh_delay(self):
        r = reading("anthropic", "a", NOW, "ok", limits=[], retry_until=NOW + timedelta(seconds=30))
        out = show.render([r], NOW)
        self.assertIn("refresh paused: next read in under a minute", out)
        self.assertNotIn("next read in 0m", out)

    def test_unmapped_provider_reasons_keep_their_diagnostic_details(self):
        for why in ("vendor-123", "new-reason"):
            with self.subTest(why=why):
                out = show.render([reading("zai", "a", NOW, "refused", why=why)], NOW)
                self.assertIn("? Usage unavailable", out)
                self.assertIn(why, out)
                self.assertIn("Refresh", out)

    def test_a_missing_reason_still_explains_the_failed_read(self):
        out = show.render([reading("openai", "a", NOW, "unread")], NOW)
        self.assertIn("? Usage unavailable", out)
        self.assertIn("Refresh", out)

    def test_a_paused_refresh_keeps_its_reading_without_inventing_throttling(self):
        r = reading("zai", "z", NOW - timedelta(minutes=10), "ok", limits=[])
        r["retry_until"] = (NOW + timedelta(minutes=4)).isoformat()
        out = show.render([r], NOW)
        self.assertIn("(read 10m ago, refresh paused: next read in 4m)", out)
        self.assertNotIn("throttled", out)

    def test_colour_is_green_to_85_orange_to_95_then_red(self):
        paint = lambda used: show._paint("x", used, None, True)
        self.assertEqual([paint(u)[2:4] for u in (0.85, 0.86, 0.95, 0.96)], ["32", "38", "38", "31"])
        self.assertIn("[31m", show._paint("x", 0.1, True, True), "held is red whatever the figure")

    def test_logged_tokens_show_both_rolling_windows_and_survive_a_refusal(self):
        # Replacing these measured counts with quota bars or dropping them on refusal hides retained facts.
        usage = {"source": "gateway-log", "as_of": NOW.isoformat(),
                 "collected_at": (NOW - timedelta(hours=2)).isoformat(), "periods": [
                     {"window_minutes": 10080, "input_tokens": 2851, "output_tokens": 85,
                      "total_tokens": 2936, "cache_read_tokens": 1088, "cache_creation_tokens": 0,
                      "coverage": "partial", "reasons": ["horizon-edge"]},
                     {"window_minutes": 43200, "input_tokens": 0, "output_tokens": 0,
                      "total_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0,
                      "coverage": "observed", "reasons": []}]}
        refused = reading("woyo", "a", NOW, "refused", why="http-401")
        refused["usage"] = usage
        out = show.render([refused], NOW)
        self.assertIn("Woyo", out)
        self.assertIn("rolling 7d: 2.94K logged tokens", out)
        self.assertIn("input 2.85K · output 85", out)
        self.assertIn("partial (horizon-edge)", out)
        self.assertIn("collected 2h 00m ago", out)
        self.assertIn("rolling 30d: 0 logged tokens", out)
        self.assertIn("? Sign-in rejected", out)

    def test_local_pacing_progress_is_explicit_and_qualifies_partial_history(self):
        # A voluntary limit must never look like a provider quota or promise room under incomplete history.
        r = reading("woyo", "a", NOW, "ok", limits=[])
        r["usage"] = {"source": "gateway-log", "as_of": NOW.isoformat(), "collected_at": NOW.isoformat(), "periods": [
            {"window_minutes": 10080, "input_tokens": 2000, "output_tokens": 500, "total_tokens": 2500,
             "coverage": "partial", "reasons": ["horizon-edge"],
             "local_budget": {"status": "ok", "token_limit": 2000, "profiles": ["gateway"]}},
            {"window_minutes": 43200, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
             "coverage": "observed", "reasons": [],
             "local_budget": {"status": "conflict", "token_limit": None, "profiles": ["one", "two"]}},
        ]}

        out = show.render([r], NOW)

        self.assertIn("local pacing: 2.50K of 2.00K (125.0%) · partial history · profiles gateway", out)
        self.assertIn("local pacing: configuration conflict · profiles one, two", out)
        self.assertNotIn("████", out, "local pacing is text, not a provider quota bar")

    def test_local_pacing_invalid_setting_exposes_no_value(self):
        # An invalid setting is actionable without disclosing the text a person accidentally pasted.
        r = reading("woyo", "a", NOW, "ok", limits=[])
        r["usage"] = {"source": "gateway-log", "as_of": NOW.isoformat(), "collected_at": NOW.isoformat(), "periods": [
            {"window_minutes": 10080, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
             "coverage": "observed", "reasons": [],
             "local_budget": {"status": "invalid", "token_limit": None, "profiles": ["gateway"]}},
        ]}

        out = show.render([r], NOW)

        self.assertIn("local pacing: configuration invalid · profiles gateway", out)
        self.assertNotIn("private-invalid-value", out)

    def test_unknown_logged_tokens_remain_distinct_from_a_measured_zero(self):
        # Turning absent collection into zero would fabricate an observation.
        r = reading("woyo", "a", NOW, "ok", limits=[])
        r["usage"] = {"source": "gateway-log", "as_of": NOW.isoformat(), "collected_at": None,
                      "periods": [{"window_minutes": 10080, "input_tokens": None, "output_tokens": None,
                                   "total_tokens": None, "cache_read_tokens": None, "cache_creation_tokens": None,
                                   "coverage": "unknown", "reasons": []}]}
        out = show.render([r], NOW)
        self.assertIn("rolling 7d: ? logged tokens", out)
        self.assertIn("unknown", out)


class Names(unittest.TestCase):
    """Each adapter names its accounts by the directories or wrappers holding them."""

    def test_the_bare_claude_directory_is_labeled_account1_not_default(self):
        home = Path(scratch.mkdtemp())
        (home / ".claude").mkdir()
        (home / ".claude" / ".claude.json").write_text('{"oauthAccount": {"accountUuid": "u"}}')
        from unlimited.adapters import anthropic
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            self.assertEqual(anthropic.names(), {"u": ["account1"]})

    def test_a_zai_account_is_named_by_the_wrapper_holding_its_key(self):
        home = Path(scratch.mkdtemp())
        (home / ".config").mkdir()
        (home / ".config" / "claude-glm-2.env").write_text("GLM_API_KEY=two\n")
        from unlimited.adapters import zai
        with mock.patch.dict(os.environ, {"HOME": str(home), "CLAUDE_GLM_ENV": ""}):
            self.assertEqual(zai.names(), {account_of("two"): ["claude-glm-2"]})

    def test_a_kimi_account_is_named_by_the_wrapper_holding_its_key(self):
        home = Path(scratch.mkdtemp())
        (home / ".config").mkdir()
        (home / ".config" / "claude-kimi.env").write_text("KIMI_API_KEY=two\n")
        from unlimited.adapters import kimi
        with mock.patch.dict(os.environ, {"HOME": str(home), "CLAUDE_KIMI_ENV": ""}):
            self.assertEqual(kimi.names(), {account_of("two"): ["claude-kimi"]})

    def test_the_default_opencode_identity_is_labeled_opencode(self):
        home = Path(scratch.mkdtemp())
        d = home / ".local" / "share" / "opencode"
        d.mkdir(parents=True)
        (d / "auth.json").write_text('{"opencode-go": {"type": "api", "key": "one"}}')
        from unlimited.adapters import opencode
        with mock.patch.dict(os.environ, {"HOME": str(home), "XDG_DATA_HOME": ""}):
            self.assertEqual(opencode.names(), {opencode.account_of("one"): ["opencode"]})

    def test_a_second_opencode_identity_is_named_by_its_directory(self):
        home = Path(scratch.mkdtemp())
        d = home / ".opencode-2" / "opencode"
        d.mkdir(parents=True)
        (d / "auth.json").write_text('{"opencode-go": {"type": "api", "key": "two"}}')
        from unlimited.adapters import opencode
        with mock.patch.dict(os.environ, {"HOME": str(home), "XDG_DATA_HOME": ""}):
            self.assertEqual(opencode.names(), {opencode.account_of("two"): ["opencode-2"]})

    def test_the_same_opencode_key_in_two_identities_shows_both_not_one(self):
        home = Path(scratch.mkdtemp())
        for d in (home / ".local" / "share" / "opencode", home / ".opencode-2" / "opencode"):
            d.mkdir(parents=True)
            (d / "auth.json").write_text('{"opencode-go": {"type": "api", "key": "one"}}')
        from unlimited.adapters import opencode
        with mock.patch.dict(os.environ, {"HOME": str(home), "XDG_DATA_HOME": ""}):
            self.assertEqual(opencode.names(), {opencode.account_of("one"): ["opencode", "opencode-2"]})


if __name__ == "__main__":
    unittest.main()
