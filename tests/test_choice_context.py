"""Source-local route policy and independent account exclusions."""

import copy
import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from unlimited import catalog, choice, incentives, outcomes
from test_context import NOW, ROUTE, setting
import scratch


def ctx(account="a", factor=1):
    return {"v": 1, "vendor": "openai", "account": account,
            "observed_at": NOW.isoformat(), "settings": [setting("openai", factor)],
            "unresolved": []}


class ChoiceContext(unittest.TestCase):
    def setUp(self):
        self.cat = catalog.load_metadata(Path(scratch.mkdtemp()) / "absent.toml")
        self.args = dict(tier="heavy", candidates=[ROUTE], attempts=[], quota={},
                         deadline=900, now=NOW, temperature=0, seed=17)

    def test_neutral_context_preserves_scoring_and_seeded_order(self):
        base = {**self.args, "candidates": ["codex", "glm"]}
        for temperature in (0, None, 2):
            for context in (ctx(), dict(ctx(), settings=[])):
                with self.subTest(temperature=temperature, context=context):
                    plain = choice.rank(self.cat, **{**base, "temperature": temperature})
                    contextual = choice.rank(self.cat, **{**base, "temperature": temperature},
                                             contexts={ROUTE: context})
                    self.assertEqual(plain["candidates"], contextual["candidates"])
                    for key in ("order", "pick", "seed", "policy"):
                        self.assertEqual(plain[key], contextual[key])
                    self.assertEqual(contextual["request"]["contexts"], {ROUTE: context})

    def test_malformed_policy_preserves_context_account_off_identity(self):
        self.cat.off = [{"target": "openai", "account": "a"}]
        for context in (dict(ctx(), settings="malformed"), dict(ctx(), v=99),
                        dict(ctx(), error="policy-unavailable")):
            with self.subTest(context=context):
                self.assertIsNone(choice.rank(self.cat, **self.args, contexts={ROUTE: context}))
        sibling = choice.rank(self.cat, **self.args, contexts={ROUTE: ctx(account="b", factor=10)})
        self.assertEqual(sibling["candidates"][0]["multiplier"], 10)

    def test_conflicting_identity_is_rejected_before_policy_failure_or_exclusion(self):
        for context in (ctx(), dict(ctx(), settings="malformed"), dict(ctx(), v=99),
                        dict(ctx(), error="policy-unavailable")):
            for filters in ({}, {"exclude": {ROUTE: "not available"}}, {"vendors": []}):
                with self.subTest(context=context, filters=filters), self.assertRaises(ValueError):
                    choice.rank(self.cat, **self.args, **filters, contexts={ROUTE: context},
                                accounts={ROUTE: {"account": "b", "names": ["a"]}})
        self.cat.off = [{"target": "openai", "account": "a"}]
        with self.assertRaises(ValueError):
            choice.rank(self.cat, **self.args, contexts={ROUTE: dict(ctx(), vendor="anthropic", v=99)})

    def test_context_input_combinations_and_alias_only_bindings_are_rejected(self):
        for extra in ({"incentives": []}, {"incentives": [setting()]},
                      {"accounts": {ROUTE: {"names": ["alpha"]}}},
                      {"accounts": {ROUTE: "a"}}, {"accounts": {ROUTE: None}},
                      {"accounts": {ROUTE: {"account": None}}},
                      {"accounts": {ROUTE: {"account": " "}}},
                      {"accounts": {ROUTE: {"account": True}}}):
            for contexts in ({ROUTE: ctx()}, {}):
                with self.subTest(extra=extra, contexts=contexts), self.assertRaises(ValueError):
                    choice.rank(self.cat, **self.args, contexts=contexts, **extra)

    def test_local_alias_and_uncovered_identity_still_enforce_off(self):
        self.cat.off = [{"target": "openai", "account": "alpha"}]
        for contexts in ({ROUTE: ctx()}, {}, {ROUTE: dict(ctx(), vendor=None)},
                         {ROUTE: ctx(account=None)}, {ROUTE: dict(ctx(), settings="malformed")}):
            with self.subTest(contexts=contexts):
                self.assertIsNone(choice.rank(self.cat, **self.args, contexts=contexts,
                                              accounts={ROUTE: {"account": "a", "names": ["alpha"]}}))

    def test_uncovered_canonical_account_still_enforces_off(self):
        self.cat.off = [{"target": "openai", "account": "a"}]
        for contexts in ({}, {ROUTE: dict(ctx(), account=" ")}, {ROUTE: ctx(account=None)}):
            with self.subTest(contexts=contexts):
                self.assertIsNone(choice.rank(self.cat, **self.args, contexts=contexts,
                                              accounts={ROUTE: {"account": "a", "names": []}}))

    def test_context_aliases_do_not_supply_off_identity(self):
        self.cat.off = [{"target": "openai", "account": "alpha"}]
        got = choice.rank(self.cat, **self.args, contexts={ROUTE: dict(ctx(), names=["alpha"])})
        self.assertIsNotNone(got)

    def test_supplied_aliases_do_not_become_context_policy(self):
        context = dict(ctx(), settings=[setting("openai", 10, account="alpha")])
        got = choice.rank(self.cat, **self.args, contexts={ROUTE: context},
                          accounts={ROUTE: {"account": "a", "names": ["alpha"]}})
        self.assertEqual(got["candidates"][0]["multiplier"], 1)
        self.assertEqual(got["context_evaluations"][ROUTE]["reason"], "invalid-context")

    def test_null_header_unbound_policy_does_not_erase_supplied_canonical_account(self):
        accounts = {ROUTE: {"account": "a", "names": []}}
        got = choice.rank(self.cat, **self.args, contexts={ROUTE: ctx(account=None, factor=10)}, accounts=accounts)
        self.assertEqual(got["candidates"][0]["multiplier"], 10)
        self.assertEqual(got["request"]["accounts"], accounts)
        self.assertFalse(got["context_evaluations"][ROUTE]["account_scoped"])

    def test_explicit_empty_context_does_not_read_local_policy(self):
        with mock.patch.object(incentives, "read", side_effect=AssertionError("implicit policy")):
            got = choice.choose(self.cat, tier="heavy", candidates=[ROUTE], quota={}, deadline=900,
                                now=NOW, temperature=0, contexts={},
                                accounts={ROUTE: {"account": "a", "names": []}},
                                log=Path(scratch.mkdtemp()) / "decisions.jsonl")
        self.assertEqual(got["candidates"][0]["multiplier"], 1)
        self.assertEqual(got["context_evaluations"][ROUTE]["reason"], "missing-context")
        self.assertEqual(got["request"]["contexts"], {})
        self.assertNotIn("incentives", got["request"])

    def test_mixed_partial_context_does_not_borrow_local_policy(self):
        remote = {"v": 1, "vendor": "anthropic", "account": "remote-b",
                  "observed_at": NOW.isoformat(), "settings": [setting("anthropic", 3)], "unresolved": []}
        self.assertEqual(self.cat.route("opus")["vendor"], "anthropic")
        with mock.patch.object(incentives, "read", side_effect=AssertionError("implicit local policy")):
            got = choice.choose(self.cat, tier="heavy", candidates=[ROUTE, "opus"], quota={}, deadline=900,
                                now=NOW, temperature=0, contexts={"opus": remote},
                                accounts={ROUTE: {"account": "local-a", "names": []}},
                                log=Path(scratch.mkdtemp()) / "mixed.jsonl")
        by_id = {c["model"]: c for c in got["candidates"]}
        self.assertEqual(by_id[ROUTE]["multiplier"], 1)
        self.assertEqual(by_id["opus"]["multiplier"], 3)
        self.assertEqual(got["context_evaluations"][ROUTE]["reason"], "missing-context")
        self.assertEqual(got["context_evaluations"]["opus"]["scope"], "vendor")

    def test_choose_rejects_both_policy_inputs_without_logging(self):
        log = Path(scratch.mkdtemp()) / "decisions.jsonl"
        with self.assertRaises(ValueError):
            choice.choose(self.cat, tier="heavy", candidates=[ROUTE], quota={}, deadline=900,
                          now=NOW, contexts={}, incentives=[], log=log)
        self.assertFalse(log.exists())

    def test_pure_context_rank_does_not_load_policy_catalog_or_log(self):
        with mock.patch.object(incentives, "read", side_effect=AssertionError("local policy")), \
                mock.patch.object(catalog, "load", side_effect=AssertionError("catalog I/O")), \
                mock.patch.object(catalog, "load_metadata", side_effect=AssertionError("metadata I/O")), \
                mock.patch.object(outcomes, "read", side_effect=AssertionError("log read")), \
                mock.patch.object(outcomes, "append", side_effect=AssertionError("log write")):
            got = choice.rank(self.cat, **self.args, contexts={ROUTE: ctx(factor=10)})
        self.assertEqual(got["candidates"][0]["multiplier"], 10)

    def test_unavailable_policy_is_explained_without_using_display_routes(self):
        for context, reason in ((None, "missing-context"), (dict(ctx(), v=99), "unsupported-version"),
                                (dict(ctx(), error="catalog-unavailable"), "policy-unavailable"),
                                (dict(ctx(), settings="malformed"), "invalid-context")):
            if context is not None:
                context["routes"] = [{"id": ROUTE, "multiplier": 100}]
            with self.subTest(reason=reason):
                got = choice.rank(self.cat, **self.args, contexts={ROUTE: context})
                self.assertEqual(got["candidates"][0]["multiplier"], 1)
                self.assertEqual(got["context_evaluations"][ROUTE]["status"], "unavailable")
                self.assertEqual(got["context_evaluations"][ROUTE]["reason"], reason)
        got = choice.rank(self.cat, **self.args, contexts={ROUTE: dict(ctx(), settings=[],
                          routes=[{"id": ROUTE, "multiplier": 100}])})
        self.assertEqual(got["candidates"][0]["multiplier"], 1)
        self.assertEqual(got["context_evaluations"][ROUTE]["reason"], "no-live-rule")

    def test_effective_factor_changes_only_existing_time_cost_paths(self):
        for temperature in (0, None, 2):
            args = {**self.args, "candidates": [ROUTE, "glm"], "quota": {ROUTE: .8, "glm": .4},
                    "temperature": temperature, "prefer": {ROUTE: 2}}
            plain = choice.rank(self.cat, **args)
            for factor in (.1, 10, sys.float_info.max):
                with self.subTest(temperature=temperature, factor=factor):
                    contextual = choice.rank(self.cat, **args, contexts={ROUTE: ctx(factor=factor)})
                    legacy = choice.rank(self.cat, **args, incentives=[setting("openai", factor)])
                    self.assertEqual(contextual["candidates"], legacy["candidates"])
                    self.assertEqual(contextual["order"], legacy["order"])
                    before, after = plain["candidates"][0], contextual["candidates"][0]
                    for key in ("rho", "pi", "preference", "p", "t_ok", "t_fail", "t_next", "ok", "fail", "mu", "var"):
                        self.assertEqual(before[key], after[key])
                    effective = min(factor, 1e6)
                    self.assertAlmostEqual(after["e"], (before["e"] - 20 * before["pi"] + before["preference"])
                                           / effective + 20 * before["pi"] - before["preference"])
                    explanation = contextual["context_evaluations"][ROUTE]
                    self.assertEqual((explanation["multiplier"], explanation["requested_multiplier"],
                                      explanation["clamped"]), (effective, factor, factor > 1e6))

    def test_context_expiry_reveals_broader_rule_at_decision_time(self):
        context = dict(ctx(), settings=[setting("openai", 10, hours=2), setting(ROUTE, 1, hours=1)])
        for hours, factor, reason in ((0, 1, "explicit-neutral"), (1, 10, None), (2, 1, "no-live-rule")):
            with self.subTest(hours=hours):
                got = choice.rank(self.cat, **{**self.args, "now": NOW + timedelta(hours=hours)},
                                  contexts={ROUTE: context})
                self.assertEqual(got["candidates"][0]["multiplier"], factor)
                self.assertEqual(got["context_evaluations"][ROUTE]["reason"], reason)

    def test_shared_account_does_not_merge_source_policies(self):
        self.cat.offerings.append({"id": "other-sol", "model": self.cat.route(ROUTE)["model"], "vendor": "openai"})
        got = choice.rank(self.cat, **{**self.args, "candidates": [ROUTE, "other-sol"]},
                          contexts={ROUTE: ctx(factor=10), "other-sol": ctx(factor=.1)})
        self.assertEqual({c["model"]: c["multiplier"] for c in got["candidates"]}, {ROUTE: 10, "other-sol": .1})

    def test_unknown_targets_are_diagnosed_without_borrowing_scope(self):
        context = dict(ctx(factor=2), settings=[setting("openai", 2), setting("unknown-target", 100)])
        got = choice.rank(self.cat, **self.args, contexts={ROUTE: context})
        self.assertEqual(got["candidates"][0]["multiplier"], 2)
        self.assertEqual(got["context_evaluations"][ROUTE]["unresolved"], ["unknown-target"])

    def test_choose_records_whole_context_request_and_resolved_explanation(self):
        context = dict(ctx(factor=10), settings=[setting("openai", 10, account="a")],
                       source={"label": "opaque", "details": [1, 2]}, routes=[{"id": ROUTE, "multiplier": 999}])
        contexts = {ROUTE: context, "unused": {"opaque": ["retained"]}}
        accounts = {ROUTE: {"account": "a", "names": ["alpha"], "opaque": {"retain": True}}}
        before = copy.deepcopy((contexts, accounts))
        log = Path(scratch.mkdtemp()) / "decisions.jsonl"
        got = choice.choose(self.cat, tier="heavy", candidates=[ROUTE], quota={ROUTE: .8}, deadline=900,
                            now=NOW, temperature=0, seed=17, contexts=contexts, accounts=accounts,
                            task="opaque-label", meta={"source": {"generation": 9}}, log=log)
        (logged,), bad = outcomes.read(log)
        self.assertEqual(bad, 0)
        self.assertEqual(logged, got)
        self.assertEqual(logged["request"]["contexts"], before[0])
        self.assertEqual(logged["request"]["accounts"], before[1])
        self.assertEqual((contexts, accounts), before)
        self.assertEqual(logged["request"]["meta"], {"source": {"generation": 9}})
        self.assertEqual(logged["at"], NOW.isoformat())
        self.assertEqual(logged["seed"], 17)
        self.assertEqual(logged["context_evaluations"][ROUTE], {
            "status": "applied", "reason": None, "multiplier": 10.0, "requested_multiplier": 10.0,
            "target": "openai", "scope": "vendor", "until": (NOW + timedelta(hours=2)).isoformat(),
            "account_scoped": True, "clamped": False, "unresolved": []})
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)
        json.dumps(logged, allow_nan=False)
