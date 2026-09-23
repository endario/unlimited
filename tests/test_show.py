"""The status view: what a person sees for each account."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from unlimited import show
from unlimited.schema import credits, limit, reading

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


class Render(unittest.TestCase):
    def test_windows_show_with_plan_and_reset_and_severity_entries_stay_hidden(self):
        r = reading("openai", "acct-1234567", NOW, "ok", plan="pro", limits=[
            limit("codex", window_minutes=10080, used_at_least=0.5, resets_at=NOW + timedelta(days=2), held=False),
            limit("limits:session", window_minutes=300, used_at_least=0.1, resets_at=None, held=None,
                  severity="normal")])
        with mock.patch.object(show, "_claude_dirs", lambda: {}):
            out = show.render([r], NOW)
        self.assertIn("Codex Pro · acct-123\n", out, "a fresh reading needs no note")
        self.assertIn("weekly", out)
        self.assertIn("50.0%", out)
        self.assertRegex(out, r"resets in +2d 0h")
        self.assertNotIn("limits:session", out)
        self.assertIn("limits:session", show.render([r], NOW, all_limits=True))

    def test_vendor_blocks_sort_by_their_displayed_name_not_their_internal_id(self):
        # "kimi" < "openai" as internal ids, but "Codex" must still print before "Kimi Code".
        readings = [reading("kimi", "k", NOW, "ok", limits=[]), reading("openai", "o", NOW, "ok", limits=[])]
        out = show.render(readings, NOW)
        self.assertLess(out.index("Codex"), out.index("Kimi Code"))

    def test_the_bare_claude_directory_is_labeled_account1_not_default(self):
        home = Path(tempfile.mkdtemp())
        (home / ".claude").mkdir()
        (home / ".claude" / ".claude.json").write_text('{"oauthAccount": {"accountUuid": "u"}}')
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            labels = show._claude_dirs()
        self.assertEqual(labels, {"u": "account1"})

    def test_a_zai_account_is_named_by_the_wrapper_holding_its_key(self):
        home = Path(tempfile.mkdtemp())
        (home / ".config").mkdir()
        (home / ".config" / "claude-glm-2.env").write_text("GLM_API_KEY=two\n")
        from unlimited.adapters import zai
        r = reading("zai", zai.account_of("two"), NOW, "ok", plan="max", limits=[])
        with mock.patch.dict(os.environ, {"HOME": str(home), "CLAUDE_GLM_ENV": ""}):
            self.assertIn("Z.ai GLM Max · claude-glm-2\n", show.render([r], NOW))

    def test_a_kimi_account_is_named_by_the_wrapper_holding_its_key(self):
        home = Path(tempfile.mkdtemp())
        (home / ".config").mkdir()
        (home / ".config" / "claude-kimi.env").write_text("KIMI_API_KEY=two\n")
        from unlimited.adapters import kimi
        r = reading("kimi", kimi.account_of("two"), NOW, "ok", limits=[])
        with mock.patch.dict(os.environ, {"HOME": str(home), "CLAUDE_KIMI_ENV": ""}):
            self.assertIn("Kimi Code · claude-kimi\n", show.render([r], NOW))

    def credits(self, **kw):
        return credits(NOW, **{"enabled": True, "used": 0.0, "limit": 200.0, "balance": None,
                               "currency": "SGD", **kw})

    def test_credits_show_what_is_left_to_spend_past_the_windows_and_whether_it_is_on(self):
        show_ = lambda c: show.render([reading("anthropic", "a", NOW, "ok", credits=c)], NOW)
        with mock.patch.object(show, "_claude_dirs", lambda: {}):
            self.assertIn("SGD 12.50 of 200.00 · on", show_(self.credits(used=12.5)))
            blocked = show_(self.credits(used=150.62, limit=150.0, enabled=False,
                                         disabled_reason="org_level_disabled_until"))
            self.assertIn("100.4%", blocked)
            self.assertIn("SGD 150.62 of 150.00 · OFF: org_level_disabled_until", blocked)
            self.assertIn("SGD 40.00 balance", show_(self.credits(balance=40.0)))
            self.assertRegex(show_(self.credits(limit=None, used=0.0, enabled=False)), r"credits +off\n")
            self.assertNotIn("credits", show.render([reading("openai", "a", NOW, "ok")], NOW))

    def test_credits_older_than_the_reading_carrying_them_say_how_old(self):
        old = credits(NOW - timedelta(hours=2), enabled=True, used=0.0, limit=10.0, balance=None, currency="SGD")
        with mock.patch.object(show, "_claude_dirs", lambda: {}):
            out = show.render([reading("anthropic", "a", NOW, "ok", credits=old)], NOW)
        self.assertIn("(read 2h 00m ago)", out.split("credits")[1])

    def test_an_unread_account_says_why(self):
        r = reading("zai", "z", NOW, "refused", why="http-429", retry_until=NOW + timedelta(minutes=5))
        self.assertIn("not read: http-429, retry in 5m", show.render([r], NOW))

    def test_a_throttled_account_shows_its_kept_reading_and_when_it_is_next_read(self):
        r = reading("zai", "z", NOW - timedelta(minutes=10), "ok", limits=[])
        r["retry_until"] = (NOW + timedelta(minutes=4)).isoformat()
        self.assertIn("(read 10m ago, throttled: next read in 4m)", show.render([r], NOW))

    def test_colour_is_green_to_85_orange_to_95_then_red(self):
        paint = lambda used: show._paint("x", used, None, True)
        self.assertEqual([paint(u)[2:4] for u in (0.85, 0.86, 0.95, 0.96)], ["32", "38", "38", "31"])
        self.assertIn("[31m", show._paint("x", 0.1, True, True), "held is red whatever the figure")


if __name__ == "__main__":
    unittest.main()
