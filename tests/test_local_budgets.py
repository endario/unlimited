"""Personal token pacing settings are display-only and never persist provider facts."""

from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from unlimited.credential import EnvKeys, account_of

import scratch

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
KEYS = EnvKeys("FY_API_KEY", "*.env")


def measured(key: str = "fixture-key") -> dict:
    return {
        "vendor": "woyo",
        "account": account_of(key),
        "usage": {
            "source": "gateway-log",
            "periods": [
                {"window_minutes": 10080, "starts_at": (NOW - timedelta(days=7)).isoformat(), "ends_at": NOW.isoformat(),
                 "total_tokens": 2_500, "coverage": "partial"},
                {"window_minutes": 43200, "starts_at": (NOW - timedelta(days=30)).isoformat(), "ends_at": NOW.isoformat(),
                 "total_tokens": 8_000, "coverage": "observed"},
            ],
        },
    }


class LocalBudgets(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(scratch.mkdtemp())
        self.home = self.tmp / "home"
        self.config = self.home / ".config"
        self.config.mkdir(parents=True)
        self.environ = mock.patch.dict(os.environ, {"HOME": str(self.home), "XDG_STATE_HOME": str(self.tmp / "state"),
                                                    "FY_API_KEY": ""}, clear=False)
        self.environ.start()
        self.addCleanup(self.environ.stop)

    def attach(self, reading: dict | None = None) -> dict:
        from unlimited import local_budgets

        return local_budgets.attach(reading or measured(), KEYS)

    def write(self, name: str, body: str) -> Path:
        path = self.config / name
        path.write_text(body)
        return path

    def test_unset_or_empty_limits_do_not_decorate_measured_periods(self):
        # A blank setting is no preference, not a zero-token budget or diagnostic.
        self.write("gateway.env", "FY_API_KEY=fixture-key\nUNLIMITED_WEEKLY_TOKEN_LIMIT=\n")
        reading = measured()
        reading["usage"]["periods"][0]["local_budget"] = {"status": "ok", "token_limit": 1, "profiles": ["stale"]}

        periods = self.attach(reading)["usage"]["periods"]

        self.assertTrue(all("local_budget" not in period for period in periods))

    def test_agreeing_file_profiles_resolve_each_period_independently(self):
        # Each rolling period has its own voluntary cap even when profiles name one shared key.
        body = "FY_API_KEY=fixture-key\nUNLIMITED_WEEKLY_TOKEN_LIMIT=10000\nUNLIMITED_MONTHLY_TOKEN_LIMIT=50000\n"
        self.write("one.env", body)
        self.write("two.env", body)

        weekly, monthly = self.attach()["usage"]["periods"]

        self.assertEqual(weekly["local_budget"], {"status": "ok", "token_limit": 10_000,
                                                   "profiles": ["one", "two"]})
        self.assertEqual(monthly["local_budget"], {"status": "ok", "token_limit": 50_000,
                                                    "profiles": ["one", "two"]})

    def test_conflicting_and_invalid_explicit_values_have_no_denominator_or_value_leak(self):
        # Reporting a secret-like invalid string would turn a local diagnostic into credential disclosure.
        self.write("one.env", "FY_API_KEY=fixture-key\nUNLIMITED_WEEKLY_TOKEN_LIMIT=10000\nUNLIMITED_MONTHLY_TOKEN_LIMIT=private-invalid-value\n")
        self.write("two.env", "FY_API_KEY=fixture-key\nUNLIMITED_WEEKLY_TOKEN_LIMIT=20000\nUNLIMITED_MONTHLY_TOKEN_LIMIT=30000\n")

        weekly, monthly = self.attach()["usage"]["periods"]

        self.assertEqual(weekly["local_budget"], {"status": "conflict", "token_limit": None,
                                                   "profiles": ["one", "two"]})
        self.assertEqual(monthly["local_budget"], {"status": "invalid", "token_limit": None,
                                                    "profiles": ["one", "two"]})
        self.assertNotIn("private-invalid-value", json.dumps({"weekly": weekly, "monthly": monthly}))

    def test_empty_environment_limit_is_unset(self):
        # Environment-only settings follow env-file semantics: blank means no budget.
        with mock.patch.dict(os.environ, {"FY_API_KEY": "environment-key", "UNLIMITED_WEEKLY_TOKEN_LIMIT": ""}):
            periods = self.attach(measured("environment-key"))["usage"]["periods"]

        self.assertTrue(all("local_budget" not in period for period in periods))

    def test_environment_settings_apply_only_without_a_matching_live_key_file(self):
        # A live credential profile is authoritative over an inherited environment setting for that key.
        with mock.patch.dict(os.environ, {"FY_API_KEY": "environment-key", "UNLIMITED_WEEKLY_TOKEN_LIMIT": "20000"}):
            self.assertEqual(self.attach(measured("environment-key"))["usage"]["periods"][0]["local_budget"],
                             {"status": "ok", "token_limit": 20_000, "profiles": ["environment"]})
            self.write("gateway.env", "FY_API_KEY=environment-key\nUNLIMITED_WEEKLY_TOKEN_LIMIT=30000\n")
            self.assertEqual(self.attach(measured("environment-key"))["usage"]["periods"][0]["local_budget"],
                             {"status": "ok", "token_limit": 30_000, "profiles": ["gateway"]})

    def test_local_budget_is_outside_provider_verdict_and_projection_facts(self):
        # Personal pacing is advisory display data and cannot alter the provider's quota interpretation.
        from unlimited import choice, projection, verdict
        from unlimited.schema import limit, reading

        self.write("gateway.env", "FY_API_KEY=fixture-key\nUNLIMITED_WEEKLY_TOKEN_LIMIT=10000\n")
        base = reading("woyo", account_of("fixture-key"), NOW, "ok", limits=[
            limit("weekly", window_minutes=10080, used_at_least=0.5, resets_at=NOW.replace(day=15), held=False),
        ])
        base["usage"] = measured()["usage"]
        decorated = self.attach(base)
        kwargs = {"model_scope": None, "now": NOW, "work": timedelta(minutes=10),
                  "max_age": timedelta(minutes=10)}

        self.assertEqual(decorated["limits"], base["limits"])
        budget_verdict, provider_verdict = verdict.verdict(decorated, **kwargs), verdict.verdict(base, **kwargs)
        self.assertEqual(budget_verdict, provider_verdict)
        candidate = [{"provider": "woyo", "model": "woyo", "promoted": False}]
        self.assertEqual(choice.score(candidate, {"woyo": budget_verdict["at_reset"][1]}, {}, 900, []),
                         choice.score(candidate, {"woyo": provider_verdict["at_reset"][1]}, {}, 900, []))
        self.assertEqual(projection.attach(decorated, {})["limits"], projection.attach(base, {})["limits"])

    def test_leading_zero_positive_integer_resolves_within_the_native_range(self):
        # Leading zeroes are formatting, not evidence that a representable positive limit is too large.
        self.write("gateway.env", "FY_API_KEY=fixture-key\nUNLIMITED_WEEKLY_TOKEN_LIMIT=000000000000000000010000\n")

        weekly = self.attach()["usage"]["periods"][0]

        self.assertEqual(weekly["local_budget"], {"status": "ok", "token_limit": 10_000,
                                                   "profiles": ["gateway"]})

    def test_unrepresentable_positive_integer_is_invalid_without_raising(self):
        # A JSON/Python integer beyond the native signed token representation must not poison the whole read.
        self.write("gateway.env", f"FY_API_KEY=fixture-key\nUNLIMITED_WEEKLY_TOKEN_LIMIT={2 ** 100}\n")

        weekly = self.attach()["usage"]["periods"][0]

        self.assertEqual(weekly["local_budget"], {"status": "invalid", "token_limit": None,
                                                   "profiles": ["gateway"]})
    def test_estimated_cost_prices_flash_without_counting_cache_tokens_twice(self):
        # The normalized prompt excludes separately priced cache reads and writes.
        rates = "".join(
            f"UNLIMITED_{name}_{bucket}_USD_PER_MILLION={value}\n"
            for name, values in {
                "GPT_6_1_SOL": ("2", "10", ".1", "2.5"), "GPT_6_LUNA": (".1", ".5", ".01", ".125"),
                "GLM_5_3": ("1.4", "4.4", ".26", "1.4"), "GLM_5_3_FLASH": (".15", ".5", ".03", ".15"),
            }.items() for bucket, value in zip(("INPUT", "OUTPUT", "CACHE_READ", "CACHE_WRITE"), values)
        )
        self.write("gateway.env", "FY_API_KEY=fixture-key\nUNLIMITED_WEEKLY_USD_LIMIT=200\n" + rates)
        reading = measured()
        reading["usage"]["collected_at"] = NOW.isoformat()
        event = {"id": "one", "at": (NOW - timedelta(minutes=1)).isoformat(), "model": "glm-5.3-flash",
                 "input_tokens": 100, "output_tokens": 40, "cache_read_tokens": 10, "cache_creation_tokens": 20}
        with mock.patch("unlimited.usage.events", return_value=[event]):
            cost = self.attach(reading)["usage"]["periods"][0]["estimated_cost"]

        self.assertEqual(cost, {"usd": 0.0000338, "budget_usd": 200.0, "unpriced_requests": 0, "status": "ok"})
        legacy = dict(event)
        legacy.pop("model")
        with mock.patch("unlimited.usage.events", return_value=[legacy]):
            partial = self.attach(reading)["usage"]["periods"][0]["estimated_cost"]
        self.assertEqual(partial, {"usd": None, "budget_usd": 200.0, "unpriced_requests": 1, "status": "partial"})

    def test_estimated_cost_uses_the_sol_large_context_tier(self):
        rates = "".join(
            f"UNLIMITED_{name}_{bucket}_USD_PER_MILLION={value}\n"
            for name, values in {
                "GPT_6_1_SOL": ("2", "10", ".1", "2.5"), "GPT_6_LUNA": (".1", ".5", ".01", ".125"),
                "GLM_5_3": ("1.4", "4.4", ".26", "1.4"), "GLM_5_3_FLASH": (".15", ".5", ".03", ".15"),
            }.items() for bucket, value in zip(("INPUT", "OUTPUT", "CACHE_READ", "CACHE_WRITE"), values)
        )
        self.write("gateway.env", "FY_API_KEY=fixture-key\n" + rates)
        reading = measured()
        reading["usage"]["collected_at"] = NOW.isoformat()
        event = {"id": "one", "at": (NOW - timedelta(minutes=1)).isoformat(), "model": "gpt-6.1-sol",
                 "input_tokens": 300_000, "output_tokens": 2, "cache_read_tokens": 299_999, "cache_creation_tokens": 0}
        with mock.patch("unlimited.usage.events", return_value=[event]):
            cost = self.attach(reading)["usage"]["periods"][0]["estimated_cost"]

        self.assertEqual(cost["usd"], 0.0600338)

    def test_astra_profile_rates_price_cache_buckets_and_total_input_tiers(self):
        rates = "".join(f"UNLIMITED_GPT_6_ASTRA_{bucket}_USD_PER_MILLION={value}\n"
                        for bucket, value in zip(("INPUT", "OUTPUT", "CACHE_READ", "CACHE_WRITE"),
                                                 ("10", "50", "1", "12.5")))
        self.write("gateway.env", "FY_API_KEY=fixture-key\nUNLIMITED_WEEKLY_USD_LIMIT=200\n" + rates)
        reading = measured()
        reading["usage"]["collected_at"] = NOW.isoformat()
        for inputs, outputs, reads, writes, expected in (
                (100, 40, 10, 20, 0.00296),
                (272_000, 2, 271_999, 0, 0.272109),
                (300_000, 2, 299_999, 0, 0.600168)):
            with self.subTest(inputs=inputs):
                event = {"id": "astra", "at": (NOW - timedelta(minutes=1)).isoformat(),
                         "model": "gpt-6-astra", "input_tokens": inputs, "output_tokens": outputs,
                         "cache_read_tokens": reads, "cache_creation_tokens": writes}
                with mock.patch("unlimited.usage.events", return_value=[event]):
                    cost = self.attach(reading)["usage"]["periods"][0]["estimated_cost"]
                self.assertEqual(cost, {"usd": expected, "budget_usd": 200.0,
                                        "unpriced_requests": 0, "status": "ok"})

    def test_usd_budget_that_underflows_the_native_number_is_invalid_not_zero(self):
        self.write("gateway.env", "FY_API_KEY=fixture-key\nUNLIMITED_WEEKLY_USD_LIMIT=1e-1000\n")
        cost = self.attach()["usage"]["periods"][0]["estimated_cost"]
        self.assertEqual((cost["status"], cost["budget_usd"]), ("invalid", None))

    def test_invalid_or_conflicting_usd_settings_have_no_budget_or_cost(self):
        self.write("one.env", "FY_API_KEY=fixture-key\nUNLIMITED_WEEKLY_USD_LIMIT=200\n"
                   "UNLIMITED_GLM_5_3_INPUT_USD_PER_MILLION=1\n")
        self.write("two.env", "FY_API_KEY=fixture-key\nUNLIMITED_WEEKLY_USD_LIMIT=not-a-decimal\n"
                   "UNLIMITED_GLM_5_3_INPUT_USD_PER_MILLION=2\n")
        reading = measured()
        reading["usage"]["collected_at"] = NOW.isoformat()
        with mock.patch("unlimited.usage.events", return_value=[]):
            cost = self.attach(reading)["usage"]["periods"][0]["estimated_cost"]

        self.assertEqual(cost, {"usd": None, "budget_usd": None, "unpriced_requests": 0, "status": "invalid"})
        self.write("two.env", "FY_API_KEY=fixture-key\nUNLIMITED_WEEKLY_USD_LIMIT=200\n"
                   "UNLIMITED_GLM_5_3_INPUT_USD_PER_MILLION=2\n")
        with mock.patch("unlimited.usage.events", return_value=[]):
            conflict = self.attach(reading)["usage"]["periods"][0]["estimated_cost"]
        self.assertEqual(conflict, {"usd": None, "budget_usd": None, "unpriced_requests": 0, "status": "conflict"})

    def test_usd_budget_is_also_a_weekly_limit_every_limits_reader_ranks(self):
        # A reader that knows only `limits` sees the budget as a weekly window, so it needs no
        # budget handling of its own; one that does can tell it by `kind`.
        from unlimited import verdict
        from unlimited.schema import reading

        self.write("gateway.env", "FY_API_KEY=fixture-key\nUNLIMITED_WEEKLY_USD_LIMIT=1\n"
                   + "".join(f"UNLIMITED_GLM_5_3_{b}_USD_PER_MILLION=1\n"
                             for b in ("INPUT", "OUTPUT", "CACHE_READ", "CACHE_WRITE")))
        base = reading("woyo", account_of("fixture-key"), NOW, "ok", limits=[])
        base["usage"] = dict(measured()["usage"], collected_at=NOW.isoformat())
        last = NOW - timedelta(days=2)
        events = [{"id": i, "at": at.isoformat(), "model": "glm-5.3", "input_tokens": 100_000,
                   "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0}
                  for i, at in (("old", NOW - timedelta(days=8)), ("one", NOW - timedelta(days=5)), ("two", last))]
        with mock.patch("unlimited.usage.events", return_value=events):
            decorated = self.attach(base)
            again = self.attach(decorated)

        self.assertEqual(decorated["budget"]["used"], 0.2)
        [weekly] = decorated["limits"]
        self.assertEqual((weekly["name"], weekly["role"], weekly["window_minutes"], weekly["kind"]),
                         ("seven_day", "weekly", 10080, "local-market-value"))
        self.assertEqual(weekly["used_at_least"], 0.2)
        self.assertEqual(weekly["resets_at"], (last + timedelta(days=7)).isoformat())
        self.assertEqual(again["limits"], decorated["limits"])
        ranked = verdict.verdict(decorated, model_scope=None, now=NOW, work=timedelta(minutes=10),
                                 max_age=timedelta(minutes=10))
        self.assertEqual((ranked["state"], ranked["window"], ranked["at_reset"]), ("ranked", "seven_day", [0.2, 0.2]))

    def test_budget_limit_without_requests_is_unopened_and_misconfigured_is_unread(self):
        from unlimited import verdict

        self.write("gateway.env", "FY_API_KEY=fixture-key\nUNLIMITED_WEEKLY_USD_LIMIT=200\n")
        reading = dict(measured(), status="ok", taken_at=NOW.isoformat(), limits=[])
        reading["usage"]["collected_at"] = NOW.isoformat()
        with mock.patch("unlimited.usage.events", return_value=[]):
            [idle] = self.attach(reading)["limits"]
        self.assertEqual((idle["used_at_least"], idle["resets_at"], idle["projection"]), (0.0, None, None))

        self.write("gateway.env", "FY_API_KEY=fixture-key\nUNLIMITED_WEEKLY_USD_LIMIT=nope\n")
        with mock.patch("unlimited.usage.events", return_value=[]):
            broken = self.attach(reading)
        self.assertIsNone(broken["limits"][0]["used_at_least"])
        self.assertEqual(verdict.verdict(broken, model_scope=None, now=NOW, work=timedelta(minutes=10),
                                         max_age=timedelta(minutes=10))["state"], "unread")


if __name__ == "__main__":
    unittest.main()
