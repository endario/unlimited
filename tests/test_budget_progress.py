"""Local budget progress is additive data, not a provider quota verdict."""

from __future__ import annotations

import copy
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest import mock

import scratch
from unlimited import cli, local_budgets
from unlimited.schema import limit, reading
from unlimited.verdict import verdict

NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


def measured(*, coverage="observed", **cost):
    r = reading("woyo", "fixture", NOW, "ok")
    r["usage"] = {"source": "gateway-log", "as_of": NOW.isoformat(), "periods": [
        {"window_minutes": 10080, "coverage": coverage, "estimated_cost": {
            "status": "ok", "usd": 50.0, "budget_usd": 200.0,
            "unpriced_requests": 0, **cost}}
    ]}
    return r


class BudgetProgress(unittest.TestCase):
    def test_complete_progress_is_a_fraction_without_reset_or_forecast(self):
        progress = local_budgets.budget_progress(measured())
        self.assertEqual(progress, {
            "source": "local-market-value", "window_type": "rolling", "window_minutes": 10080,
            "state": "ok", "used": 0.25, "usd": 50.0, "budget_usd": 200.0,
            "coverage": "observed", "unpriced_requests": 0,
        })

    def test_partial_history_cannot_claim_complete_headroom_but_can_cross_the_budget(self):
        for coverage, status, unpriced, usd, state, used in (
            ("observed", "ok", 0, 0, "ok", 0),
            ("observed", "ok", 0, 200, "exceeded", 1),
            ("observed", "ok", 0, 250, "exceeded", 1.25),
            ("partial", "ok", 0, 50, "partial", 0.25),
            ("partial", "ok", 0, 250, "exceeded", 1.25),
            ("observed", "partial", 1, 50, "partial", 0.25),
            ("observed", "partial", 1, 250, "exceeded", 1.25),
            ("unknown", "partial", 0, None, "partial", None),
        ):
            with self.subTest(coverage=coverage, status=status, usd=usd):
                p = local_budgets.budget_progress(measured(
                    coverage=coverage, status=status, unpriced_requests=unpriced, usd=usd))
                self.assertEqual((p["state"], p["used"]), (state, used))

    def test_invalid_configuration_or_numeric_fields_are_sanitized(self):
        cases = [
            {"status": "invalid", "budget_usd": None},
            {"status": "conflict", "budget_usd": None},
            {"usd": "private-invalid-value"}, {"usd": True}, {"usd": -1},
            {"usd": float("inf")}, {"usd": float("nan")},
            {"budget_usd": "private-invalid-value"}, {"budget_usd": True},
            {"budget_usd": 0}, {"budget_usd": -1}, {"budget_usd": float("inf")},
            {"unpriced_requests": True}, {"unpriced_requests": -1},
        ]
        for fields in cases:
            with self.subTest(fields=fields):
                p = local_budgets.budget_progress(measured(**fields))
                self.assertEqual(p["state"], "conflict" if fields.get("status") == "conflict" else "invalid")
                self.assertIsNone(p["used"])
                self.assertNotIn("private-invalid-value", repr(p))

    def test_no_configured_usd_budget_omits_the_side_field(self):
        for r in (None, {}, {"usage": None}, {"usage": {"periods": None}}, measured(budget_usd=None)):
            self.assertIsNone(local_budgets.budget_progress(r))

    def test_redecoration_removes_a_budget_overlay_when_configuration_is_absent(self):
        from unlimited.credential import EnvKeys

        r = measured()
        r["budget"] = local_budgets.budget_progress(r)
        with mock.patch("unlimited.local_budgets._sources", return_value=[]):
            out = local_budgets.attach(r, EnvKeys("FY_API_KEY", "*.env"))
        self.assertNotIn("budget", out)
        self.assertIn("budget", r, "decoration must not mutate the caller's prior reading")

    def test_normalization_is_pure_and_cannot_change_provider_advice(self):
        r = measured(coverage="partial", usd=250)
        r["limits"] = [limit("seven_day", window_minutes=10080, used_at_least=0.2,
                             resets_at=NOW + timedelta(days=5), held=False)]
        original = copy.deepcopy(r)
        args = {"model_scope": None, "now": NOW, "work": timedelta(minutes=10),
                "max_age": timedelta(minutes=10)}
        before = verdict(r, **args)
        with mock.patch("unlimited.local_budgets._sources", side_effect=AssertionError("profile I/O")), \
                mock.patch("unlimited.usage.events", side_effect=AssertionError("ledger I/O")):
            progress = local_budgets.budget_progress(r)
        self.assertEqual(progress["state"], "exceeded")
        self.assertEqual(r, original)
        self.assertEqual(verdict(r, **args), before)


class BudgetCli(unittest.TestCase):
    def test_cli_composes_budget_without_rewriting_the_provider_result(self):
        r = measured()
        r["budget"] = local_budgets.budget_progress(r)
        provider_results = [
            {"state": "unread", "reason": "no-window"},
            {"state": "unread", "reason": "stale"},
            {"state": "unread", "reason": "not-ok"},
            {"state": "excluded", "reason": "off", "until": None},
            {"state": "excluded", "reason": "exhausted", "until": "2026-10-19T12:00:00+00:00"},
            {"state": "ranked", "tier": 0, "score": 0.8, "used": 0.2, "at_reset": [0.3, 0.4]},
        ]
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": scratch.mkdtemp()}), \
                mock.patch("unlimited.cache.through", return_value=[r]):
            for provider in provider_results:
                with self.subTest(provider=provider), \
                        mock.patch("unlimited.verdict.verdict", return_value=provider):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        self.assertEqual(cli.main(["verdict", "--vendor", "woyo", "--work", "900"]), 0)
                    actual = json.loads(output.getvalue())[0]["verdict"]
                    self.assertEqual(actual.pop("budget"), r["budget"])
                    self.assertEqual(actual, provider)
                    self.assertNotIn("budget", provider, "composition must not mutate the pure result")

    def test_cli_without_budget_preserves_provider_output(self):
        r = measured(budget_usd=None)
        provider = {"state": "unread", "reason": "no-window"}
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": scratch.mkdtemp()}), \
                mock.patch("unlimited.cache.through", return_value=[r]), \
                mock.patch("unlimited.verdict.verdict", return_value=provider):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(cli.main(["verdict", "--vendor", "woyo", "--work", "900"]), 0)
        self.assertEqual(json.loads(output.getvalue())[0]["verdict"], provider)


if __name__ == "__main__":
    unittest.main()
