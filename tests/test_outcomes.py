"""The attempt log and what it says about each model (docs/choice.md)."""

from __future__ import annotations

import fcntl
import io
import json
import os
import stat
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from unlimited import cli, outcomes

import scratch

NOW = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)
MIN = timedelta(minutes=1)


class Log(unittest.TestCase):
    def setUp(self):
        self.p = Path(scratch.mkdtemp()) / "decisions.jsonl"

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

    def test_an_unreachable_route_counts_against_itself_and_no_other(self):
        self.run_attempt("deepseek", "commandcode/deepseek/x", "unavailable", 0, 10 * MIN)
        self.run_attempt("deepseek", "opencode-go/deepseek/x", "ok", 3, 20 * MIN)
        got = self.stats()[0]
        self.assertGreater(got[("deepseek", "commandcode/deepseek/x")]["fail"], 0)
        self.assertEqual(got[("deepseek", "opencode-go/deepseek/x")]["fail"], 0.0)

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

    def test_an_attempts_offering_is_its_route_whatever_its_model_field_says(self):
        aid = outcomes.start(provider="deepseek", model="deepseek-v4-1-flash", effort=None, task=None, account=None,
                             decision=None, deadline=600, now=NOW - 10 * MIN, offering="commandcode/deepseek/x", p=self.p)
        outcomes.end(aid, outcome="error", now=NOW - 9 * MIN, p=self.p)
        self.assertEqual(list(self.stats()[0]), [("deepseek", "commandcode/deepseek/x")])

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

    def test_append_creates_private_history_under_a_public_umask(self):
        self.p.parent.chmod(0o755)
        p = self.p.parent / "unlimited" / self.p.name
        record = {"v": 1, "type": "decision", "at": NOW.isoformat(),
                  "request": {"task": "example", "accounts": {"route": "account"},
                              "meta": {"job": "42", "nested": [1, {"label": "opaque"}]}},
                  "candidates": [{"model": "route", "e": 1.5}], "order": [0], "pick": 0}
        previous = os.umask(0o022)
        try:
            outcomes.append(record, p)
        finally:
            os.umask(previous)
        self.assertEqual(outcomes.read(p), ([record], 0))
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(p.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(p.parent.parent.stat().st_mode), 0o755)

    def test_append_repairs_public_history_without_replacing_its_records(self):
        original = '{"type": "start", "attempt": "a", "meta": {"job": "42"}}\n'
        self.p.write_text(original)
        self.p.chmod(0o644)
        self.p.parent.chmod(0o755)
        inode = self.p.stat().st_ino
        record = {"type": "end", "attempt": "a", "outcome": "ok", "meta": {"result": ["opaque"]}}
        previous = os.umask(0o022)
        try:
            outcomes.append(record, self.p)
        finally:
            os.umask(previous)
        self.assertEqual(self.p.read_text(), original + json.dumps(record, separators=(",", ":")) + "\n")
        self.assertEqual(outcomes.read(self.p), ([json.loads(original), record], 0))
        self.assertEqual(self.p.stat().st_ino, inode)
        self.assertEqual(stat.S_IMODE(self.p.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.p.parent.stat().st_mode), 0o755)

    def test_compaction_repairs_public_history_and_preserves_full_retained_records(self):
        old = {"type": "decision", "at": (NOW - timedelta(days=8)).isoformat(),
               "request": {"meta": {"padding": "x" * outcomes.COMPACT_BYTES}}}
        kept = [{"type": "start", "at": (NOW - MIN).isoformat(), "attempt": "a", "account": "account",
                 "task": "example", "meta": {"nested": [1, {"job": "42"}]}},
                {"type": "end", "at": NOW.isoformat(), "attempt": "a", "outcome": "ok",
                 "tokens": {"in": 100, "out": 10}, "meta": {"result": "opaque"}}]
        retained = "".join(json.dumps(r) + "\n" for r in kept)
        self.p.write_text(json.dumps(old) + "\n" + retained)
        self.p.chmod(0o644)
        inode = self.p.stat().st_ino
        self.assertGreater(self.p.stat().st_size, outcomes.COMPACT_BYTES)
        previous = os.umask(0o022)
        try:
            outcomes.compact(NOW, self.p)
        finally:
            os.umask(previous)
        self.assertEqual(self.p.read_text(), retained)
        self.assertEqual(outcomes.read(self.p), (kept, 0))
        self.assertEqual(self.p.stat().st_ino, inode)
        self.assertEqual(stat.S_IMODE(self.p.stat().st_mode), 0o600)

    def test_compaction_repairs_small_public_history_without_compacting_it(self):
        original = json.dumps({"at": (NOW - timedelta(days=8)).isoformat(), "meta": {"job": "42"}}) + "\n"
        self.p.write_text(original)
        self.p.chmod(0o644)
        self.assertLess(self.p.stat().st_size, outcomes.COMPACT_BYTES)
        previous = os.umask(0o022)
        try:
            outcomes.compact(NOW, self.p)
        finally:
            os.umask(previous)
        self.assertEqual(self.p.read_text(), original)
        self.assertEqual(stat.S_IMODE(self.p.stat().st_mode), 0o600)

    def test_history_is_private_and_exclusively_locked_while_processing_records(self):
        record = {"at": NOW.isoformat(), "meta": {"job": "42"}}
        serialized = json.dumps(record) + "\n"
        for target, name, operation in ((outcomes.json, "dumps", lambda: outcomes.append(record, self.p)),
                                        (outcomes, "_time", lambda: outcomes.compact(NOW, self.p))):
            with self.subTest(operation=name):
                self.p.write_text(serialized)
                self.p.chmod(0o644)
                original = getattr(target, name)

                def checked(*args, **kwargs):
                    self.assertEqual(stat.S_IMODE(self.p.stat().st_mode), 0o600)
                    with open(self.p) as other:
                        with self.assertRaises(BlockingIOError):
                            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return original(*args, **kwargs)

                with mock.patch.object(target, name, side_effect=checked), \
                        mock.patch.object(outcomes, "COMPACT_BYTES", 0):
                    operation()
                with open(self.p) as other:
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_permission_failure_closes_history_without_modifying_records(self):
        original = json.dumps({"at": NOW.isoformat(), "meta": {"job": "42"}}) + "\n"
        for operation in (lambda: outcomes.append({"meta": {"job": "43"}}, self.p),
                          lambda: outcomes.compact(NOW, self.p)):
            with self.subTest(operation=operation):
                self.p.write_text(original)
                self.p.chmod(0o644)
                with mock.patch.object(outcomes.os, "fchmod", side_effect=PermissionError) as chmod, \
                        mock.patch.object(outcomes, "COMPACT_BYTES", 0):
                    with self.assertRaises(PermissionError):
                        operation()
                fd = chmod.call_args.args[0]
                with self.assertRaises(OSError):
                    os.fstat(fd)
                self.assertEqual(self.p.read_text(), original)

    def test_compaction_drops_only_old_records(self):
        self.run_attempt("glm", "old", "ok", 4, timedelta(days=8))
        self.run_attempt("glm", "new", "ok", 4, 10 * MIN)
        with mock.patch.object(outcomes, "COMPACT_BYTES", 0):
            outcomes.compact(NOW, self.p)
        self.assertEqual(set(self.stats()[0]), {("glm", "new")})


class Cli(unittest.TestCase):
    def test_start_end_then_outcomes(self):
        state = scratch.mkdtemp()

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

    def test_starting_an_attempt_on_no_catalog_route_says_so_and_still_records_it(self):
        # A caller that records the name its CLI resolved (claude-sonnet-5) rather than the route
        # it launched (sonnet) teaches no route; the choice only counted it in attempts_unknown.
        state, config = scratch.mkdtemp(), scratch.mkdtemp()
        for model, warned in (("claude-sonnet-5", True), ("sonnet", False)):
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": state, "XDG_CONFIG_HOME": config}), \
                    redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(cli.main(["attempt", "start", "--provider", "claude", "--model", model,
                                           "--deadline", "60"]), 0)
            self.assertTrue(out.getvalue().strip())
            self.assertEqual("no catalog route" in err.getvalue(), warned, err.getvalue())
            if warned:
                self.assertIn("sonnet", err.getvalue().split("routes:")[1])


if __name__ == "__main__":
    unittest.main()
