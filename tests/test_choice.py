"""The choice (docs/choice.md): a worked example, and what is logged."""

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

    def test_temperature_zero_takes_the_lowest_and_above_it_samples_with_logged_odds(self):
        s = self.scored()
        self.assertEqual([s[i]["provider"] for i in choice.order(s, 0.0, random.Random(0))],
                         ["deepseek", "stealth", "glm", "codex"], "every candidate, cheapest first")
        self.assertEqual([c["prob"] for c in s], [0.0, 0.0, 1.0, 0.0])
        s = self.scored()
        got = choice.order(s, 2.0, random.Random(0))
        self.assertEqual(sorted(got), [0, 1, 2, 3], "sampled without replacement: each once")
        self.assertGreater(s[2]["prob"], 0.99)
        self.assertAlmostEqual(sum(c["prob"] for c in s), 1.0)

    def test_close_candidates_are_both_explored(self):
        cands = [{"provider": "a", "model": "a", "promoted": False}, {"provider": "b", "model": "b", "promoted": False}]
        s = choice.score(cands, {"a": 0.3, "b": 0.3}, {}, 1800, [])
        picks = [s[choice.order(s, 2.0, random.Random(n))[0]]["provider"] for n in range(200)]
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
        got = choice.choose(cat, tier="standard", task="example", candidates=["stealth", "glm"],
                            quota={"glm": 0.5}, deadline=1800, now=NOW, log=log)
        self.assertEqual([(c["provider"], c["model"], c["promoted"]) for c in got["candidates"]],
                         [("stealth", "bunny", True), ("glm", "glm-5.3-flash", False)])
        self.assertEqual(got["candidates"][got["pick"]]["provider"], "stealth", "free and unproven beats half a quota")
        (logged,), _ = outcomes.read(log)
        self.assertEqual((logged["type"], logged["decision"]), ("decision", got["decision"]))

    def test_a_recent_run_of_hangs_hands_the_task_to_another(self):
        local = Path(tempfile.mkdtemp()) / "catalog.toml"
        local.write_text('schema = 1\n[[promotions]]\nprovider = "stealth"\nmodel = "bunny"\ntiers = ["standard"]\n')
        cat = catalog.load(local)
        log = Path(tempfile.mkdtemp()) / "decisions.jsonl"
        for i in range(3):
            outcomes.start(provider="stealth", model="bunny", effort=None, task="example", account=None,
                           decision=None, deadline=1800, now=NOW - timedelta(hours=1 + i), p=log)
        got = choice.choose(cat, tier="standard", task="example", candidates=["stealth", "glm"],
                            quota={"glm": 0.5}, deadline=1800, now=NOW, log=log)
        self.assertEqual(got["candidates"][got["pick"]]["provider"], "glm")

    def test_the_whole_request_is_logged_with_the_callers_own_label_and_metadata(self):
        cat = catalog.load(Path(tempfile.mkdtemp()) / "none.toml")
        log = Path(tempfile.mkdtemp()) / "decisions.jsonl"
        got = choice.choose(cat, tier="standard", candidates=["glm", "codex"], quota={"glm": 0.4}, deadline=600,
                            now=NOW, temperature=1.5, quota_weight=10, task="summarise", meta={"ticket": "42"}, log=log)
        (logged,), _ = outcomes.read(log)
        self.assertEqual(logged["v"], 1)
        self.assertEqual(logged["request"], {"tier": "standard", "candidates": ["glm", "codex"], "quota": {"glm": 0.4},
                                             "deadline": 600, "temperature": 1.5, "quota_weight": 10,
                                             "task": "summarise", "meta": {"ticket": "42"}, "exclude": {},
                                             "vendors": None, "prefer": {}})
        self.assertEqual(logged["seed"] is not None, True, "a sampled pick can be replayed")
        self.assertEqual(got["decision"], logged["decision"])

    def test_a_non_finite_or_negative_parameter_is_refused_before_scoring(self):
        cat = catalog.load(Path(tempfile.mkdtemp()) / "none.toml")
        log = Path(tempfile.mkdtemp()) / "d.jsonl"
        for kw in ({"temperature": float("nan")}, {"temperature": -1.0}, {"quota_weight": float("inf")}):
            with self.assertRaises(ValueError, msg=kw):
                choice.choose(cat, tier="standard", candidates=["glm"], quota={}, deadline=60, now=NOW, log=log, **kw)
        self.assertFalse(log.exists(), "nothing is logged for a refused request")

    def test_tiers_are_the_catalogs_to_name(self):
        local = Path(tempfile.mkdtemp()) / "catalog.toml"
        # A local list of tiers replaces the shipped one, so the shipped promotions go with it.
        local.write_text('schema = 1\ntiers = ["small", "large"]\npromotions = []\n'
                         '[providers.x]\nusage = "openai"\nsmall = "m-small"\n')
        cat = catalog.load(local)
        got = choice.choose(cat, tier="small", candidates=["x"], quota={}, deadline=60, now=NOW,
                            log=Path(tempfile.mkdtemp()) / "d.jsonl")
        self.assertEqual(got["candidates"][got["pick"]]["model"], "m-small")
        local.write_text('schema = 1\ntiers = ["small"]\n[providers.x]\nusage = "openai"\n[[promotions]]\n'
                         'provider = "x"\nmodel = "p"\ntiers = ["huge"]\n')
        with self.assertRaises(catalog.CatalogError):
            catalog.load(local)

    def test_rank_decides_over_the_callers_attempts_and_touches_no_file(self):
        cat = catalog.load(Path(tempfile.mkdtemp()) / "none.toml")
        state = tempfile.mkdtemp()
        hangs = [{"provider": "glm", "model": "glm-5.3-flash", "offering": None, "effort": None, "task": None,
                  "at": NOW - timedelta(minutes=m), "outcome": "timeout", "secs": 1800.0, "tokens": {}} for m in (5, 10)]
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": state}):
            args = dict(tier="standard", candidates=["glm", "codex"], quota={"glm": 0.3, "codex": 0.3}, deadline=1800,
                        now=NOW)
            clean = choice.rank(cat, attempts=[], **args)
            hung = choice.rank(cat, attempts=hangs, **args)
        self.assertEqual(os.listdir(state), [], "nothing written")
        self.assertEqual(hung["candidates"][hung["pick"]]["provider"], "codex")
        self.assertNotEqual([c["e"] for c in clean["candidates"]], [c["e"] for c in hung["candidates"]])

    def test_rank_refuses_what_it_cannot_use_and_counts_history_it_cannot_place(self):
        cat = catalog.load(Path(tempfile.mkdtemp()) / "none.toml")
        args = dict(tier="standard", quota={}, deadline=600, now=NOW)
        run = lambda **kw: {"provider": "codex", "model": "gpt-6-luna", "offering": None, "effort": None,
                            "task": None, "at": NOW - timedelta(minutes=5), "outcome": "ok", "secs": 60.0,
                            "tokens": {}, **kw}
        for candidates, attempts in ((["nope", "codex"], []), (["codex"], [run(outcome="abandoned")]),
                                     (["codex"], [run(outcome="okay")]),
                                     (["codex"], [run(at=datetime(2026, 9, 26, 11, 0))])):
            with self.subTest(candidates=candidates, attempts=attempts), self.assertRaises(ValueError):
                choice.rank(cat, candidates=candidates, attempts=attempts, **args)
        got = choice.rank(cat, candidates=["codex"], **args,
                          attempts=[run(), run(provider="deepseek", model="x", offering="opencode-go/deepseek-v4.1-flash"),
                                    run(model="gpt-5"), run(provider="nope"), run(offering="commandcode/nope")])
        self.assertEqual(got["attempts_unknown"], 3, "a model or offering id no route carries, an unknown provider")

    def test_a_sampled_order_replays_from_its_seed(self):
        cat = catalog.load(Path(tempfile.mkdtemp()) / "none.toml")
        args = dict(tier="standard", candidates=["glm", "codex", "grok"], attempts=[], quota={}, deadline=600,
                    now=NOW, temperature=5.0)
        got = choice.rank(cat, **args)
        again = choice.rank(cat, **args, rng=random.Random(got["seed"]))
        self.assertEqual(again["order"], got["order"])
        self.assertEqual(got["pick"], got["order"][0])

    def test_an_abandoned_attempt_counts_against_no_route(self):
        log = Path(tempfile.mkdtemp()) / "d.jsonl"
        aid = outcomes.start(provider="glm", model="glm-5.3-flash", effort=None, task=None, account=None, decision=None, deadline=60,
                             now=NOW - timedelta(hours=1), p=log)
        outcomes.end(aid, outcome="abandoned", now=NOW - timedelta(minutes=59), p=log)
        records, _ = outcomes.read(log)
        self.assertEqual(outcomes.attempts(records, NOW), [], "not a timeout, though its deadline has passed")

    def test_cli_prints_the_decision(self):
        home, state = tempfile.mkdtemp(), tempfile.mkdtemp()
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": home, "XDG_STATE_HOME": state}), redirect_stdout(buf):
            self.assertEqual(cli.main(["choose", "--tier", "heavy", "--candidates", "glm,codex",
                                       "--quota", "glm=0.2,codex=0.9", "--deadline", "900", "--vendors", "any",
                                       "--json"]), 0)
        got = json.loads(buf.getvalue())
        self.assertEqual(got["candidates"][got["pick"]]["provider"], "glm")
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": home, "XDG_STATE_HOME": state}), redirect_stdout(buf):
            cli.main(["choose", "--tier", "heavy", "--candidates", "glm,codex", "--quota", "glm=0.2,codex=0.9",
                      "--exclude", "glm-5.3=benched,other", "--deadline", "900", "--vendors", "any",
                      "--prefer", "codex=-2.5", "--json"])
        got = json.loads(buf.getvalue())
        self.assertEqual([c["provider"] for c in got["candidates"]], ["codex"], "the ruled-out route is no candidate")
        self.assertEqual(got["request"]["exclude"], {"glm-5.3": "benched", "other": ""})
        self.assertEqual((got["request"]["prefer"], got["candidates"][0]["preference"]), ({"codex": -2.5}, -2.5))


    def test_a_route_this_machine_has_no_account_for_is_never_recommended(self):
        from unlimited.adapters import REGISTRY
        cat = catalog.load(Path(tempfile.mkdtemp()) / "none.toml")
        # This machine: a Z.ai account, and no OpenAI one.
        with mock.patch.dict(REGISTRY, {v: mock.Mock(discover=mock.Mock(return_value=[1] if v == "zai" else []))
                                        for v in REGISTRY}):
            here = choice.vendors_here(cat)
        self.assertIn("zai", here)
        self.assertNotIn("openai", here)
        got = choice.rank(cat, tier="heavy", candidates=["glm", "codex"], attempts=[], quota={}, deadline=900,
                          now=NOW, vendors=here)
        self.assertEqual([c["provider"] for c in got["candidates"]], ["glm"])
        self.assertEqual(got["request"]["vendors"], sorted(here))
        self.assertIsNone(choice.rank(cat, tier="heavy", candidates=["codex"], attempts=[], quota={}, deadline=900,
                                      now=NOW, vendors=here), "nothing it could use: no decision")

    def test_the_cli_by_default_offers_only_what_this_machine_can_spend(self):
        home, state = tempfile.mkdtemp(), tempfile.mkdtemp()
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": home, "XDG_STATE_HOME": state}), redirect_stdout(buf), \
                mock.patch.object(choice, "vendors_here", return_value={"openai"}):
            cli.main(["choose", "--tier", "heavy", "--candidates", "glm,codex", "--deadline", "900", "--json"])
        self.assertEqual([c["provider"] for c in json.loads(buf.getvalue())["candidates"]], ["codex"])

    def test_a_vendor_unlimited_cannot_read_is_not_ruled_out(self):
        from unlimited.adapters import REGISTRY
        cat = catalog.load(Path(tempfile.mkdtemp()) / "none.toml")
        failing = mock.Mock(discover=mock.Mock(side_effect=OSError("unreadable")))
        with mock.patch.dict(REGISTRY, {v: failing for v in REGISTRY}):
            self.assertEqual(choice.vendors_here(cat), {o["vendor"] for o in cat.offerings})


if __name__ == "__main__":
    unittest.main()
