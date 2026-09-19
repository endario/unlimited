"""The status view: what a person sees for each account."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from unlimited import show
from unlimited.schema import limit, reading

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
