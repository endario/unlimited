"""Projection: where an open window is heading, from the readings of it so far."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from unlimited import cache, projection, show
from unlimited.schema import limit, moment, reading
from unlimited.transport import Answer
from unlimited.adapters import zai

import scratch

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
    def test_a_steady_pace_projects_around_the_same_figure_from_average_and_recent(self):
        # 10% a day since the window opened: 40% now, 70% at reset, blurred by what it still draws.
        h = history(*[(NOW - timedelta(days=d), 0.4 - 0.1 * d) for d in (1, 0)])
        p = proj(h, NOW, 0.4)
        self.assertLess(p["at_reset"][0], 0.7)
        self.assertGreater(p["at_reset"][1], 0.7)
        self.assertAlmostEqual(p["recent_at_reset"], 0.7, places=3)

    def test_a_recent_burst_raises_the_high_end_and_says_when_it_runs_out(self):
        # 20% in the last day after 20% over three: average 10%/day → 70%, recent 20%/day → 100%+.
        h = history((NOW - timedelta(days=1), 0.2), (NOW, 0.45))
        p = proj(h, NOW, 0.45)
        lo, hi = p["at_reset"]
        self.assertLess(lo, 1)
        self.assertGreater(hi, 1)
        self.assertLess(moment(p["exhausts_at"]), RESET)

    def test_the_recent_pace_alone_is_reported_apart_from_the_range(self):
        # A consumer can name "today's pace", which the blurred [low, high] does not say.
        burst = proj(history((NOW - timedelta(days=1), 0.2), (NOW, 0.45)), NOW, 0.45)
        self.assertGreater(burst["recent_at_reset"], burst["at_reset"][1])
        quiet = proj(history((NOW - timedelta(days=1), 0.4), (NOW, 0.4)), NOW, 0.4)
        self.assertLess(quiet["recent_at_reset"], quiet["at_reset"][0])

    def test_a_quiet_spell_lowers_the_recent_pace_below_the_average(self):
        # 40% in the first three days, nothing in the last one.
        h = history((NOW - timedelta(days=1), 0.4), (NOW, 0.4))
        p = proj(h, NOW, 0.4)
        self.assertAlmostEqual(p["recent_at_reset"], 0.463, places=3)  # the three busy days, half-lives back
        lo, hi = p["at_reset"]
        self.assertTrue(p["recent_at_reset"] < hi and lo < 0.7)  # the range leans between the two paces

    def test_a_forecast_never_falls_below_a_reading_newer_than_the_stored_sample(self):
        # Stored samples are thinned to one per window/SAMPLES, so the newest can trail the reading
        # being shown: the forecast extends from the reading, not from the stale sample.
        late = RESET - timedelta(minutes=13)
        h = history((late - timedelta(minutes=41), 0.92), (late - timedelta(minutes=20), 0.93))
        p = proj(h, late, 0.94)
        self.assertGreaterEqual(p["at_reset"][0], 0.94)
        self.assertEqual(p["samples"], 2)

    def test_one_reading_gives_only_the_average_pace(self):
        p = proj(history((NOW, 0.4)), NOW, 0.4)
        self.assertLess(p["at_reset"][0], 0.7)
        self.assertGreater(p["at_reset"][1], 0.7)
        self.assertAlmostEqual(p["recent_at_reset"], 0.7, places=3)
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
        self.assertAlmostEqual(proj(h, NOW, 0.4)["recent_at_reset"], 0.7, places=3)

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
        self.assertGreater(p["at_reset"][0], 0.8)  # a week that ran dry is a floor, blurred
        self.assertGreater(p["at_reset"][1], 1)
        self.assertGreater(p["run_out"], 0.5)
        # They reach 100% two days on, and 95%, which is what "running out" reads as, at 11/12 of that.
        self.assertAlmostEqual((moment(p["exhausts_at"]) - NOW) / timedelta(days=1), 2 * 11 / 12, delta=0.1)

    def test_past_windows_move_the_range_but_not_the_recent_pace(self):
        front = lambda x: min(x / (4 / 7), 1) * 0.4
        p = proj(self.entry([front] * 8), NOW, 0.4)
        self.assertNotEqual(p["recent_at_reset"], p["at_reset"][1])
        self.assertEqual(p["recent_at_reset"], proj(self.entry([]), NOW, 0.4)["recent_at_reset"])

    def test_the_own_pace_counts_for_less_as_past_windows_accumulate(self):
        front = lambda x: min(x / (4 / 7), 1) * 0.4
        high = [proj(self.entry([front] * n), NOW, 0.4)["at_reset"][1] for n in (0, 1, 2, 3)]
        self.assertTrue(all(a > b for a, b in zip(high, high[1:])), high)

    def test_a_past_window_that_hit_its_limit_is_a_floor_never_extended(self):
        capped = lambda x: min(x * 1.4, 1.0)  # reached the limit at 71% of the way through
        [(mean, _, weight, _)] = projection._past_votes(past(capped), RESET, timedelta(minutes=WEEK), 4 / 7, 0.4)
        self.assertLess(mean, 1.0)  # it drew 0.2 more after this point; it may have wanted up to twice that
        self.assertEqual(weight, 1.0)


def curve_to(peak, by):
    """A past window's use: rising evenly to `peak` by `by` of the way through, flat after."""
    return lambda x: min(peak, peak * x / by)


class Boundaries(unittest.TestCase):
    """Cases a replay on real weeks cannot contain: a window already at its limit, one with no history, none used."""

    def entry(self, shapes, x, used):
        t = RESET - timedelta(minutes=WEEK) + timedelta(minutes=WEEK * x)
        samples = [[(RESET - timedelta(minutes=WEEK) + (t - (RESET - timedelta(minutes=WEEK))) * f / 20).isoformat(),
                    used * f / 20, RESET.isoformat()] for f in range(1, 21)]
        return t, {"a\tseven_day": {"samples": samples, "past": past(*shapes)}}

    def test_windows_already_at_the_limit_cast_no_vote(self):
        votes = projection._past_votes(past(curve_to(1, 0.4), lambda x: 0.9 * x), RESET, timedelta(minutes=WEEK), 0.9, 0.036)
        self.assertEqual(len(votes), 1)
        t, e = self.entry([curve_to(1, 0.4)] * 3, 0.9, 0.036)
        p = proj(e, t, 0.036)
        self.assertLess(p["at_reset"][1], 0.3)
        self.assertIsNone(p["exhausts_at"])

    def test_a_week_that_caps_late_does_not_make_an_idle_window_run_out(self):
        t, e = self.entry([curve_to(1, 0.95)] * 3, 0.9, 0.036)
        p = proj(e, t, 0.036)
        self.assertLess(p["at_reset"][1], 0.3)
        self.assertLess(p["run_out"], 0.2)

    def test_the_own_pace_tells_the_time_when_there_is_no_history(self):
        t, e = self.entry([], 0.3, 0.6)
        p = proj(e, t, 0.6)
        self.assertGreater(p["run_out"], 0.9)
        phase = (moment(p["exhausts_at"]) - (RESET - timedelta(minutes=WEEK))) / timedelta(minutes=WEEK)
        self.assertTrue(0.3 < phase < 0.7, phase)
        t, e = self.entry([], 0.5, 0.02)
        p = proj(e, t, 0.02)
        self.assertIsNone(p["exhausts_at"])
        self.assertLess(p["run_out"], 0.05)

    def test_no_time_is_told_from_a_vote_the_mixture_discarded(self):
        mid = lambda x: 0.8 * x / 0.5 if x <= 0.5 else 0.8 + 0.2 * (x - 0.5) / 0.5
        t, e = self.entry([mid] * 3, 0.5, 0.6)
        p = proj(e, t, 0.6)
        self.assertGreaterEqual(p["run_out"], 0.5)
        self.assertIsNone(p["exhausts_at"])

    def test_no_bound_is_below_what_is_used_or_below_zero(self):
        t, e = self.entry([], 0.5, 0.0)
        self.assertGreaterEqual(proj(e, t, 0.0)["at_reset"][0], 0)
        t, e = self.entry([], 0.5, 0.3)
        self.assertGreaterEqual(proj(e, t, 0.3)["at_reset"][0], 0.3)

    def test_six_full_weeks_do_not_make_an_early_window_read_175(self):
        import json
        curves = json.loads((Path(__file__).parent / "fixtures" / "weekly-curves.json").read_text())
        x, used = 0.1543, 0.04
        t = RESET - timedelta(minutes=WEEK) + timedelta(minutes=WEEK * x)
        p_ = [{"resets_at": (RESET - timedelta(weeks=len(curves) - i)).isoformat(), "curve": c} for i, c in enumerate(curves)]
        samples = [[(t - (t - (RESET - timedelta(minutes=WEEK))) * (1 - f / 20)).isoformat(), used * f / 20, RESET.isoformat()]
                   for f in range(1, 21)]
        p = proj({"a\tseven_day": {"samples": samples, "past": p_}}, t, used)
        lo, hi = p["at_reset"]
        self.assertAlmostEqual(lo, 0.86, delta=0.03)
        self.assertAlmostEqual(hi, 1.08, delta=0.03)
        self.assertAlmostEqual(p["run_out"], 0.57, delta=0.05)


class ThroughCache(unittest.TestCase):
    def test_history_accumulates_across_reads_and_reaches_the_reading(self):
        tmp = Path(scratch.mkdtemp())
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
        self.assertGreater(p["at_reset"][1], 0.7)


class Shown(unittest.TestCase):
    def test_the_status_row_shows_the_projected_range(self):
        r = projection.attach(at(NOW, 0.45), history((NOW - timedelta(days=1), 0.2), (NOW, 0.45)))
        out = show.render([r], NOW)
        self.assertRegex(out, r"\n {18}→ \d+–\d+% at reset · runs out in \d+d \d+h(?: · \d+% chance of running out)?\n")

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
