"""Projection: where an open window is heading, from the readings of it so far."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from unlimited import cache, projection, show
from unlimited.schema import limit, moment, reading
from unlimited.transport import Answer
from unlimited.adapters import zai

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
WEEK = 10080
RESET = NOW + timedelta(days=3)  # four days of the week gone


def at(t, used, resets=RESET):
    return reading("zai", "a", t, "ok", limits=[
        limit("seven_day", window_minutes=WEEK, used_at_least=used, resets_at=resets, held=False)])


def history(*pairs):
    h = {}
    for t, u in pairs:
        h = projection.record(h, [at(t, u)])
    return h


def proj(h, t, used):
    return projection.attach(at(t, used), h)["limits"][0]["projection"]


class Project(unittest.TestCase):
    def test_a_steady_pace_projects_the_same_from_average_and_recent(self):
        # 10% a day since the window opened: 40% now, 70% at reset.
        h = history(*[(NOW - timedelta(days=d), 0.4 - 0.1 * d) for d in (1, 0)])
        self.assertEqual(proj(h, NOW, 0.4)["at_reset"], [0.7, 0.7])

    def test_a_recent_burst_raises_the_high_end_and_says_when_it_runs_out(self):
        # 20% in the last day after 20% over three: average 10%/day → 70%, recent 20%/day → 100%+.
        h = history((NOW - timedelta(days=1), 0.2), (NOW, 0.45))
        p = proj(h, NOW, 0.45)
        lo, hi = p["at_reset"]
        self.assertLess(lo, 1)
        self.assertGreater(hi, 1)
        self.assertLess(moment(p["exhausts_at"]), RESET)

    def test_the_recent_pace_alone_is_reported_whichever_end_it_lands(self):
        # A consumer can name "today's pace", which the sorted [low, high] cannot.
        burst = proj(history((NOW - timedelta(days=1), 0.2), (NOW, 0.45)), NOW, 0.45)
        self.assertEqual(burst["recent_at_reset"], burst["at_reset"][1])
        quiet = proj(history((NOW - timedelta(days=1), 0.4), (NOW, 0.4)), NOW, 0.4)
        self.assertEqual(quiet["recent_at_reset"], quiet["at_reset"][0])

    def test_a_quiet_spell_lowers_the_recent_pace_below_the_average(self):
        # 40% in the first three days, nothing in the last one.
        h = history((NOW - timedelta(days=1), 0.4), (NOW, 0.4))
        lo, hi = proj(h, NOW, 0.4)["at_reset"]
        self.assertAlmostEqual(lo, 0.463, places=3)  # the three busy days, half-lives back
        self.assertAlmostEqual(hi, 0.7)

    def test_one_reading_gives_only_the_average_pace(self):
        p = proj(history((NOW, 0.4)), NOW, 0.4)
        self.assertEqual(p["at_reset"], [0.7, 0.7])
        self.assertEqual(p["samples"], 1)

    def test_nothing_is_projected_just_after_a_window_opens(self):
        opened = RESET - timedelta(minutes=WEEK)
        self.assertIsNone(proj(history((opened + timedelta(minutes=5), 0.01)), opened + timedelta(minutes=5), 0.01))

    def test_a_reset_starts_the_history_over(self):
        h = history((NOW - timedelta(days=1), 0.9))
        h = projection.record(h, [at(NOW, 0.05, resets=NOW + timedelta(days=7) - timedelta(hours=1))])
        self.assertEqual(len(h["a\tseven_day"]["samples"]), 1)

    def test_readings_closer_than_the_sampling_step_are_not_kept(self):
        h = history((NOW, 0.4), (NOW + timedelta(minutes=5), 0.41))
        self.assertEqual(len(h["a\tseven_day"]["samples"]), 1)

    def test_a_window_read_only_partway_is_not_kept_as_a_past_window(self):
        h = history((NOW - timedelta(days=2), 0.2), (NOW - timedelta(days=1), 0.3), (NOW, 0.4))
        self.assertEqual(projection.prune(h, RESET + timedelta(seconds=1)), {})

    def test_a_window_read_to_its_end_is_kept_as_a_past_window_at_reset(self):
        h = history(*[(RESET - timedelta(days=d), 0.1 * (7 - d)) for d in (3, 2, 1, 0.5)])
        h = projection.prune(h, RESET + timedelta(seconds=1))
        self.assertEqual(h["a\tseven_day"]["samples"], [])
        curve = h["a\tseven_day"]["past"][0]["curve"]
        self.assertEqual((curve[0], curve[-1]), (0.0, 0.65))

    def test_a_reset_moved_back_restarts_the_window_without_archiving_it(self):
        h = history(*[(RESET - timedelta(days=d), 0.1) for d in (3, 2, 1)])
        h = projection.record(h, [at(RESET - timedelta(hours=20), 0.2, resets=RESET - timedelta(hours=1))])
        self.assertEqual((len(h["a\tseven_day"]["samples"]), h["a\tseven_day"]["past"]), (1, []))

    def test_a_window_is_archived_at_its_own_length_when_the_next_one_differs(self):
        h = history(*[(RESET - timedelta(days=d), 0.1 * (7 - d)) for d in (3, 2, 1, 0.5)])
        nxt = reading("zai", "a", RESET + timedelta(hours=1), "ok", limits=[
            limit("seven_day", window_minutes=300, used_at_least=0.01, resets_at=RESET + timedelta(hours=5), held=False)])
        self.assertEqual(len(projection.record(h, [nxt])["a\tseven_day"]["past"]), 1)

    def test_the_old_bare_sample_list_still_reads(self):
        h = {"a\tseven_day": [[NOW.isoformat(), 0.4, RESET.isoformat()]]}
        self.assertEqual(proj(h, NOW, 0.4)["at_reset"], [0.7, 0.7])

    def test_a_malformed_history_is_ignored_not_fatal(self):
        h = {"a\tseven_day": [["x", 0.1, None], "junk", [1, 2]]}
        self.assertIsNone(proj(h, NOW, 0.4))
        self.assertEqual(len(projection.record(h, [at(NOW, 0.4)])["a\tseven_day"]["samples"]), 1)
        self.assertEqual(projection.prune({"k": "junk"}, NOW), {})

    def test_a_window_past_its_reset_is_not_projected(self):
        from unlimited.schema import settled
        r = settled(at(NOW, 0.4), RESET + timedelta(seconds=1))
        self.assertIsNone(projection.attach(r, history((NOW, 0.4)))["limits"][0]["projection"])


def past(*shapes):
    """Past weekly windows, newest last, each a function of the elapsed fraction to use."""
    return [{"resets_at": (RESET - timedelta(weeks=len(shapes) - i)).isoformat(),
             "curve": [round(f(i / projection.GRID), 4) for i in range(projection.GRID + 1)]}
            for i, f in enumerate(shapes)]


class FromPastWindows(unittest.TestCase):
    def entry(self, shapes, used=0.4):
        return {"a\tseven_day": {"samples": [[NOW.isoformat(), used, RESET.isoformat()]], "past": past(*shapes)}}

    def test_past_windows_that_went_quiet_late_pull_the_projection_down(self):
        # Every past week used 40% by this point and nothing after: a front-loaded shape.
        front = lambda x: min(x / (4 / 7), 1) * 0.4
        p = proj(self.entry([front] * 8), NOW, 0.4)
        self.assertLess(p["at_reset"][1], 0.5)
        self.assertLess(p["run_out"], 0.2)
        self.assertIsNone(p["exhausts_at"])

    def test_past_windows_that_ran_out_say_so_and_when(self):
        # Every past week was back-loaded: 40% by now, then 100% two days later.
        back = lambda x: 0.4 * x / (4 / 7) if x <= 4 / 7 else min(0.4 + 0.6 * (x - 4 / 7) / (2 / 7), 1.0)
        p = proj(self.entry([back] * 8), NOW, 0.4)
        self.assertGreater(p["at_reset"][0], 1)
        self.assertGreater(p["run_out"], 0.8)
        self.assertAlmostEqual((moment(p["exhausts_at"]) - NOW) / timedelta(days=1), 2, delta=0.1)

    def test_past_windows_move_the_range_but_not_the_recent_pace(self):
        front = lambda x: min(x / (4 / 7), 1) * 0.4
        p = proj(self.entry([front] * 8), NOW, 0.4)
        self.assertNotEqual(p["recent_at_reset"], p["at_reset"][1])
        self.assertEqual(p["recent_at_reset"], proj(self.entry([]), NOW, 0.4)["recent_at_reset"])

    def test_too_few_past_windows_leave_the_paces_in_charge(self):
        front = lambda x: min(x / (4 / 7), 1) * 0.4
        self.assertEqual(proj(self.entry([front]), NOW, 0.4)["at_reset"], [0.7, 0.7])

    def test_a_past_window_that_hit_its_limit_counts_its_unspent_demand(self):
        capped = lambda x: min(x * 1.4, 1.0)  # would have used 140%
        c = projection._extended(past(capped)[0]["curve"])
        self.assertAlmostEqual(c[-1], 1.4, delta=0.01)


class ThroughCache(unittest.TestCase):
    def test_history_accumulates_across_reads_and_reaches_the_reading(self):
        tmp = Path(tempfile.mkdtemp())
        used = {"v": 2000}

        def get(url, headers, now):
            return Answer({"code": 200, "success": True, "data": {"level": "max", "limits": [
                {"type": "CREDIT_LIMIT", "unit": 6, "number": 1, "usage": 10000, "currentValue": used["v"],
                 "nextResetTime": int(RESET.timestamp() * 1000)}]}}, 200, None)

        with mock.patch.dict(os.environ, {"GLM_API_KEY": "k", "CLAUDE_GLM_ENV": "", "HOME": str(tmp), "XDG_CACHE_HOME": str(tmp)}):
            cache.through(zai, max_age=60, clock=lambda: NOW - timedelta(days=1), get=get)
            used["v"] = 4000
            out = cache.through(zai, max_age=60, clock=lambda: NOW, get=get)
        p = next(l for l in out[0]["limits"] if l["window_minutes"] == WEEK)["projection"]
        self.assertEqual(p["samples"], 2)
        self.assertEqual(p["at_reset"][0], 0.7)
        self.assertGreater(p["at_reset"][1], 0.7)


class Shown(unittest.TestCase):
    def test_the_status_row_shows_the_projected_range(self):
        r = projection.attach(at(NOW, 0.45), history((NOW - timedelta(days=1), 0.2), (NOW, 0.45)))
        out = show.render([r], NOW)
        self.assertRegex(out, r"\n {18}→ \d+–\d+% at reset · runs out in \d+d \d+h\n")

    def test_a_full_window_has_no_forecast(self):
        full = projection.attach(at(NOW, 1.0), history((NOW, 1.0)))
        self.assertNotIn("→", show.render([full], NOW))

    def test_only_a_forecast_without_past_windows_is_marked_as_a_guess(self):
        # Past windows back it: printed plainly, with no footnote. Without them: italic.
        e = {"a\tseven_day": {"samples": [[NOW.isoformat(), 0.4, RESET.isoformat()]],
                              "past": past(*[lambda x: x * 0.7] * 4)}}
        known = show.render([projection.attach(at(NOW, 0.4), e)], NOW, color=True)
        guess = show.render([projection.attach(at(NOW, 0.45), history((NOW - timedelta(days=1), 0.2), (NOW, 0.45)))],
                            NOW, color=True)
        self.assertNotIn("past windows", known)
        self.assertNotIn(show.ITALIC, known)
        self.assertIn(show.ITALIC + "→", guess)

    def test_a_held_row_says_held_not_where_it_is_heading(self):
        r = projection.attach(at(NOW, 0.45), history((NOW, 0.45)))
        r["limits"][0]["held"] = True
        self.assertNotIn("→", show.render([r], NOW))


if __name__ == "__main__":
    unittest.main()
