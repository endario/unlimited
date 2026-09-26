"""The attempt log and what it says about each model (docs/choice.md)."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from unlimited import cli, outcomes

NOW = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)
MIN = timedelta(minutes=1)


class Log(unittest.TestCase):
    def setUp(self):
        self.p = Path(tempfile.mkdtemp()) / "decisions.jsonl"

    def run_attempt(self, provider, model, outcome, minutes, ago, deadline=1800.0):
        t0 = NOW - ago
        aid = outcomes.start(provider=provider, model=model, effort="medium", task="example", account=None,
                             decision=None, deadline=deadline, now=t0, p=self.p)
        if outcome is not None:
            outcomes.end(aid, outcome=outcome, now=t0 + minutes * MIN, p=self.p)
        return aid

    def stats(self, now=NOW):
        records, bad = outcomes.read(self.p)
        return outcomes.stats(outcomes.attempts(records, now), now), bad

    def test_no_history_is_the_prior(self):
        self.run_attempt("glm", "g", "unavailable", 0, 10 * MIN)
        self.assertEqual(self.stats()[0], {})

    def test_recent_failures_raise_the_rate_and_it_decays_back_towards_the_prior(self):
        for i in range(2):
            self.run_attempt("stealth", "bunny", "timeout", 30, (40 + i) * MIN)
        s = self.stats()[0][("stealth", "bunny")]
        self.assertAlmostEqual(s["p"], (0.5 + s["fail"]) / (5 + s["fail"]), places=9)
        self.assertGreater(s["p"], 0.35)
        self.assertAlmostEqual(s["t_fail"], 1800.0, delta=1)
        later = self.stats(NOW + timedelta(days=2))[0][("stealth", "bunny")]
        self.assertLess(later["p"], 0.13)

    def test_a_start_with_no_end_is_a_timeout_once_its_deadline_passes(self):
        self.run_attempt("stealth", "bunny", None, 0, 20 * MIN, deadline=1500)
        self.assertEqual(self.stats()[0], {}, "still inside its deadline")
        self.run_attempt("stealth", "bunny", None, 0, 30 * MIN, deadline=1500)
        s = self.stats()[0][("stealth", "bunny")]
        self.assertEqual((round(s["fail"], 2) > 0, s["ok"], s["t_fail"]), (True, 0.0, 1500.0))

    def test_a_second_end_for_one_attempt_is_ignored(self):
        aid = self.run_attempt("glm", "g", "ok", 4, 10 * MIN)
        outcomes.end(aid, outcome="error", now=NOW, p=self.p)
        self.assertEqual(self.stats()[0][("glm", "g")]["fail"], 0.0)

    def test_successes_pull_the_expected_duration_from_the_prior_towards_what_was_seen(self):
        for i in range(20):
            self.run_attempt("deepseek", "d", "ok", 2, (10 + i) * MIN)
        self.assertLess(self.stats()[0][("deepseek", "d")]["t_ok"], 200)

    def test_an_end_with_an_unknown_outcome_is_not_read_as_a_timeout(self):
        aid = self.run_attempt("glm", "g", None, 0, 40 * MIN, deadline=60)
        outcomes.end(aid, outcome="later-schema", now=NOW - 39 * MIN, p=self.p)
        self.assertEqual(self.stats()[0], {})

    def test_an_unreadable_line_is_skipped_and_counted(self):
        self.run_attempt("glm", "g", "ok", 4, 10 * MIN)
        with open(self.p, "a") as f:
            f.write('{"type": "start", "attem')
        got, bad = self.stats()
        self.assertEqual((("glm", "g") in got, bad), (True, 1))

    def test_compaction_drops_only_old_records(self):
        self.run_attempt("glm", "old", "ok", 4, timedelta(days=8))
        self.run_attempt("glm", "new", "ok", 4, 10 * MIN)
        with mock.patch.object(outcomes, "COMPACT_BYTES", 0):
            outcomes.compact(NOW, self.p)
        self.assertEqual(set(self.stats()[0]), {("glm", "new")})


class Cli(unittest.TestCase):
    def test_start_end_then_outcomes(self):
        state = tempfile.mkdtemp()

        def run(*args):
            buf = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": state}), redirect_stdout(buf):
                self.assertEqual(cli.main(list(args)), 0)
            return buf.getvalue().strip()
        aid = run("attempt", "start", "--provider", "glm", "--model", "g", "--task", "example", "--meta", "k=v", "--deadline", "60")
        run("attempt", "end", aid, "--outcome", "ok", "--tokens-out", "10", "--meta", "verdict=good")
        (got,) = json.loads(run("outcomes", "--json"))
        start, end = outcomes.read(Path(state) / "unlimited" / "decisions.jsonl")[0]
        self.assertEqual((start["task"], start["meta"], end["meta"], end["tokens"]),
                         ("example", {"k": "v"}, {"verdict": "good"}, {"out": 10}))
        self.assertEqual((got["provider"], got["model"], got["fail"]), ("glm", "g", 0.0))


if __name__ == "__main__":
    unittest.main()
