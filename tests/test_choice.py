"""Phase 2A's choice (docs/usage-routing/phase-2a.md): the worked example, and what is logged."""

from __future__ import annotations

import io
import json
import os
import random
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from unlimited import catalog, choice, cli, outcomes

NOW = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)
MIN = 60.0


def stat(p, t_ok_min, t_fail_min=None, fail=0.0, ok=5.0):
    return {"p": p, "t_ok": t_ok_min * MIN, "t_fail": t_fail_min * MIN if t_fail_min else None, "fail": fail, "ok": ok}


class Price(unittest.TestCase):
    def test_the_quota_price_at_its_named_points(self):
        for rho, want in ((0.3, 0.030), (0.8, 0.368), (1.0, 1.0), (1.2, 2.718)):
            self.assertAlmostEqual(choice.price(rho), want, places=3)
        self.assertEqual(choice.price(None), 1.0, "unread is priced as at the limit")


class WorkedExample(unittest.TestCase):
    """The design's table: a free model that keeps hanging loses to a cheap, reliable one."""

    def scored(self):
        cands = [{"provider": "stealth", "model": "bunny", "promoted": True},
                 {"provider": "glm", "model": "flash", "promoted": False},
                 {"provider": "deepseek", "model": "ds", "promoted": False},
                 {"provider": "codex", "model": "luna", "promoted": False}]
        stats = {("stealth", "bunny"): stat(0.45, 4, 30, fail=1e9),
                 ("glm", "flash"): stat(0.1, 3.6, 5, fail=1e9),
                 ("deepseek", "ds"): stat(0.1, 3.5, 5, fail=1e9),
                 ("codex", "luna"): stat(0.1, 5, 5, fail=1e9)}
        return choice.score(cands, {"glm": 0.93, "deepseek": 0.4, "codex": 1.04}, stats, 1800, [])

    def test_expected_costs_match_the_table(self):
        e = {c["provider"]: round(c["e"], 1) for c in self.scored()}
        self.assertEqual(e, {"stealth": 17.3, "glm": 18.2, "deepseek": 5.0, "codex": 29.8})

    def test_a_final_round_takes_the_lowest_and_a_finding_round_samples_with_logged_odds(self):
        s = self.scored()
        self.assertEqual(s[choice.pick(s, "final", random.Random(0))]["provider"], "deepseek")
        self.assertEqual([c["prob"] for c in s], [0.0, 0.0, 1.0, 0.0])
        s = self.scored()
        choice.pick(s, "finding", random.Random(0))
        self.assertGreater(s[2]["prob"], 0.99)
        self.assertAlmostEqual(sum(c["prob"] for c in s), 1.0)

    def test_close_candidates_are_both_explored(self):
        cands = [{"provider": "a", "model": "a", "promoted": False}, {"provider": "b", "model": "b", "promoted": False}]
        s = choice.score(cands, {"a": 0.3, "b": 0.3}, {}, 1800, [])
        picks = [s[choice.pick(s, "finding", random.Random(n))]["provider"] for n in range(200)]
        self.assertTrue(60 < picks.count("a") < 140, picks.count("a"))

    def test_tie_preference_is_worth_a_minute_to_the_first(self):
        cands = [{"provider": "a", "model": "a", "promoted": False}, {"provider": "meta", "model": "m", "promoted": False}]
        s = choice.score(cands, {"a": 0.3, "meta": 0.3}, {}, 1800, ["meta", "deepseek"])
        self.assertAlmostEqual(s[0]["e"] - s[1]["e"], 1.0)

    def test_without_history_a_hang_is_priced_at_the_deadline(self):
        s = choice.score([{"provider": "a", "model": "a", "promoted": False}], {}, {}, 1500, [])
        self.assertAlmostEqual(s[0]["t_fail"], 25.0)


class Choose(unittest.TestCase):
    def test_promotions_are_candidates_and_the_decision_is_logged(self):
        local = Path(tempfile.mkdtemp()) / "catalog.toml"
        local.write_text('schema = 1\n[[promotions]]\nprovider = "stealth"\nmodel = "bunny"\ntiers = ["standard"]\n')
        cat = catalog.load(local)
        log = Path(tempfile.mkdtemp()) / "decisions.jsonl"
        got = choice.choose(cat, tier="standard", kind="final", mode="review", providers=["stealth", "glm"],
                            quota={"glm": 0.5}, deadline=1800, now=NOW, log=log)
        self.assertEqual([(c["provider"], c["model"], c["promoted"]) for c in got["candidates"]],
                         [("stealth", "bunny", True), ("glm", "glm-5.3-flash", False)])
        self.assertEqual(got["candidates"][got["pick"]]["provider"], "stealth", "free and unproven beats half a quota")
        (logged,), _ = outcomes.read(log)
        self.assertEqual((logged["type"], logged["decision"]), ("decision", got["decision"]))

    def test_a_recent_run_of_hangs_hands_the_round_to_the_next(self):
        local = Path(tempfile.mkdtemp()) / "catalog.toml"
        local.write_text('schema = 1\n[[promotions]]\nprovider = "stealth"\nmodel = "bunny"\ntiers = ["standard"]\n')
        cat = catalog.load(local)
        log = Path(tempfile.mkdtemp()) / "decisions.jsonl"
        for i in range(3):
            outcomes.start(provider="stealth", model="bunny", effort=None, kind="review", account=None,
                           decision=None, deadline=1800, now=NOW - timedelta(hours=1 + i), p=log)
        got = choice.choose(cat, tier="standard", kind="final", mode="review", providers=["stealth", "glm"],
                            quota={"glm": 0.5}, deadline=1800, now=NOW, log=log)
        self.assertEqual(got["candidates"][got["pick"]]["provider"], "glm")

    def test_cli_prints_the_decision(self):
        home, state = tempfile.mkdtemp(), tempfile.mkdtemp()
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": home, "XDG_STATE_HOME": state}), redirect_stdout(buf):
            self.assertEqual(cli.main(["choose", "--tier", "heavy", "--kind", "final", "--candidates", "glm,codex",
                                       "--quota", "glm=0.2,codex=0.9", "--deadline", "900", "--json"]), 0)
        got = json.loads(buf.getvalue())
        self.assertEqual(got["candidates"][got["pick"]]["provider"], "glm")


if __name__ == "__main__":
    unittest.main()
