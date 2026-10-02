"""Manual time-cost steering: persistence, expiry and choice integration."""

from __future__ import annotations

import math
import io
import json
import os
import random
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

from unlimited import catalog, choice, incentives, show

import scratch

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


class Incentives(unittest.TestCase):
    def setUp(self):
        self.local = Path(scratch.mkdtemp()) / "catalog.toml"
        self.cat = catalog.load(self.local)

    def test_a_whole_route_multiplier_divides_only_its_time_cost(self):
        got = choice.rank(self.cat, tier="heavy", candidates=["glm", "codex"], attempts=[], quota={}, deadline=900,
                          now=NOW, temperature=0, incentives=[{"target": "glm", "multiplier": 10,
                                                                  "activated_at": NOW.isoformat(),
                                                                  "until": (NOW + timedelta(hours=1)).isoformat()}])
        by_provider = {c["provider"]: c for c in got["candidates"]}
        self.assertEqual(by_provider["glm"]["multiplier"], 10)
        self.assertAlmostEqual(by_provider["glm"]["pi"], 0.5, msg="quota pricing is unchanged")
        self.assertLess(by_provider["glm"]["e"], by_provider["codex"]["e"])

    def test_a_neutral_offering_override_wins_over_an_older_vendor_setting(self):
        route = self.cat.route("gpt-6.1-sol")
        got = incentives.resolve(self.cat, [
            {"target": "codex", "multiplier": 10, "activated_at": "2026-10-02T10:00:00+00:00",
             "until": "2026-10-03T12:00:00+00:00"},
            {"target": "gpt-6.1-sol", "multiplier": 1, "activated_at": "2026-10-02T11:00:00+00:00",
             "until": "2026-10-03T12:00:00+00:00"},
        ], {route["id"]: "work"}, NOW)
        self.assertEqual(got.factors[route["id"]], 1)

    def test_an_account_setting_without_a_binding_is_reported_not_applied(self):
        got = incentives.resolve(self.cat, [{"target": "openai", "account": "work", "multiplier": .1,
                                             "activated_at": NOW.isoformat(),
                                             "until": (NOW + timedelta(hours=1)).isoformat()}], {}, NOW)
        self.assertEqual(got.factors["gpt-6.1-sol"], 1)
        self.assertEqual(got.unresolved, ["openai/work"])

    def test_expired_entries_are_inactive_and_pruned_by_a_mutation(self):
        path = incentives.incentives_path(self.local)
        incentives.write([{"target": "glm", "multiplier": 10, "activated_at": NOW.isoformat(),
                           "until": (NOW - timedelta(seconds=1)).isoformat()}], path)
        self.assertEqual(incentives.read(path, NOW), [])
        incentives.set_incentive(self.cat, "codex", 10, now=NOW, duration=timedelta(hours=1), path=path)
        self.assertEqual([x["target"] for x in incentives.read(path, NOW)], ["codex"])

    def test_invalid_multiplier_is_refused_before_any_write(self):
        path = incentives.incentives_path(self.local)
        for multiplier in (0, -1, math.inf, math.nan, True):
            with self.subTest(multiplier=multiplier), self.assertRaises(ValueError):
                incentives.set_incentive(self.cat, "codex", multiplier, now=NOW, duration=timedelta(hours=1), path=path)
        self.assertFalse(path.exists())

    def test_reset_expiry_uses_the_latest_relevant_future_window(self):
        readings = [{"vendor": "openai", "account": "work", "names": ["primary"], "status": "ok", "limits": [
            {"role": "session", "window_minutes": 300, "resets_at": "2026-10-02T13:00:00+00:00"},
            {"role": "weekly", "window_minutes": 10080, "resets_at": "2026-10-06T12:00:00+00:00"},
            {"role": "TIME_LIMIT", "window_minutes": 10080, "resets_at": "2026-10-10T12:00:00+00:00"},
            {"role": None, "window_minutes": 10080, "resets_at": "2026-10-11T12:00:00+00:00"},
        ]}]
        group = incentives.set_incentive(self.cat, "openai", 10, now=NOW, readings=readings,
                                         path=incentives.incentives_path(self.local))
        self.assertEqual(group["bindings"][0]["until"], "2026-10-06T12:00:00+00:00")
    def test_explicit_duration_needs_no_reading_and_negative_direction_inflates_time(self):
        group = incentives.set_incentive(self.cat, "glm", .1, now=NOW, duration=timedelta(hours=1),
                                         path=incentives.incentives_path(self.local))
        self.assertIn("until", group)
        plain = choice.rank(self.cat, tier="heavy", candidates=["glm"], attempts=[], quota={}, deadline=900,
                            now=NOW, temperature=0)
        steered = choice.rank(self.cat, tier="heavy", candidates=["glm"], attempts=[], quota={}, deadline=900,
                              now=NOW, temperature=0, incentives=[group])
        self.assertGreater(steered["candidates"][0]["e"], plain["candidates"][0]["e"])
        self.assertEqual(steered["candidates"][0]["t_next"], plain["candidates"][0]["t_next"])

    def test_reset_groups_keep_accounts_independent_and_aliases_match(self):
        group = {"target": "openai", "multiplier": 10, "activated_at": NOW.isoformat(), "bindings": [
            {"vendor": "openai", "account": "a", "names": ["alpha"], "until": "2026-10-02T13:00:00+00:00"},
            {"vendor": "openai", "account": "b", "names": ["beta"], "until": "2026-10-03T12:00:00+00:00"},
        ]}
        short = incentives.resolve(self.cat, [group], {"gpt-6.1-sol": "alpha"}, NOW)
        later = incentives.resolve(self.cat, [group], {"gpt-6.1-sol": "beta"}, NOW + timedelta(hours=2))
        self.assertEqual(short.factors["gpt-6.1-sol"], 10)
        self.assertEqual(later.factors["gpt-6.1-sol"], 10)

    def test_overlay_keeps_losing_settings_and_neutral_route_winner(self):
        groups = [
            {"target": "openai", "multiplier": 10, "activated_at": NOW.isoformat(),
             "until": "2026-10-03T12:00:00+00:00"},
            {"target": "gpt-6.1-sol", "multiplier": 1, "activated_at": (NOW + timedelta(seconds=1)).isoformat(),
             "until": "2026-10-03T12:00:00+00:00"},
        ]
        reading = {"vendor": "openai", "account": "a", "names": [], "status": "ok", "limits": []}
        steering = incentives.overlay([reading], self.cat, groups, NOW)[0]["steering"]
        routes = {x["id"]: x for x in steering["routes"]}
        self.assertEqual(routes["gpt-6.1-sol"], {"id": "gpt-6.1-sol", "target": "gpt-6.1-sol", "account": None,
                                                   "multiplier": 1, "until": "2026-10-03T12:00:00+00:00"})
        self.assertEqual([x["target"] for x in steering["settings"]], ["openai", "gpt-6.1-sol"])

    def test_alias_targets_replace_the_same_route_scope_and_clear_it(self):
        path = incentives.incentives_path(self.local)
        incentives.set_incentive(self.cat, "gpt-6.1-sol", 10, now=NOW, duration=timedelta(hours=1), path=path)
        incentives.set_incentive(self.cat, "codex:gpt-6.1-sol", .1, now=NOW, duration=timedelta(hours=1), path=path)
        self.assertEqual(len(incentives.read(path, NOW)), 1)
        self.assertEqual(incentives.clear_incentive(self.cat, "gpt-6.1-sol", account=None, now=NOW, path=path), [])
    def test_terminal_summary_shows_symmetric_direction_strengths(self):
        reading = {"steering": {"routes": [{"multiplier": 10}, {"multiplier": .1}]}}
        self.assertEqual(show._steering(reading, False), "▲▲▲▼▼▼")
    def test_multiplier_x_syntax_and_choose_loads_the_catalogs_local_policy(self):
        self.assertEqual(incentives.parse_multiplier("10x"), 10)
        self.assertEqual(incentives.parse_multiplier("0.1x"), .1)
        incentives.set_incentive(self.cat, "glm", 10, now=NOW, duration=timedelta(hours=1),
                                 path=incentives.incentives_path(self.local))
        got = choice.choose(self.cat, tier="heavy", candidates=["glm"], quota={}, deadline=900, now=NOW,
                            temperature=0, log=Path(scratch.mkdtemp()) / "d.jsonl")
        self.assertEqual(got["candidates"][0]["multiplier"], 10)
        self.assertIn("incentives", got["request"])
        explicit = choice.choose(self.cat, tier="heavy", candidates=["glm"], quota={}, deadline=900, now=NOW,
                                 temperature=0, incentives=[], log=Path(scratch.mkdtemp()) / "e.jsonl")
        self.assertEqual(explicit["candidates"][0]["multiplier"], 1)

    def test_reset_vendor_policy_does_not_beat_route_neutral_override(self):
        groups = [
            {"target": "openai", "multiplier": 10, "activated_at": NOW.isoformat(), "bindings": [
                {"vendor": "openai", "account": "work", "names": [], "until": "2026-10-03T12:00:00+00:00"}]},
            {"target": "gpt-6.1-sol", "multiplier": 1, "activated_at": (NOW + timedelta(seconds=1)).isoformat(),
             "until": "2026-10-03T12:00:00+00:00"},
        ]
        got = incentives.resolve(self.cat, groups, {"gpt-6.1-sol": "work"}, NOW)
        self.assertEqual(got.factors["gpt-6.1-sol"], 1)

    def test_invalid_policy_is_refused_and_unbound_siblings_are_not_unresolved(self):
        with self.assertRaises(ValueError):
            incentives.resolve(self.cat, [{"target": "openai", "multiplier": 0}], {}, NOW)
        group = {"target": "openai", "multiplier": 10, "activated_at": NOW.isoformat(), "bindings": [
            {"vendor": "openai", "account": "other", "names": [], "until": "2026-10-03T12:00:00+00:00"}]}
        got = incentives.resolve(self.cat, [group], {"gpt-6.1-sol": "work"}, NOW)
        self.assertEqual(got.unresolved, [])

    def test_a_non_ok_affected_account_refuses_reset_expiry_atomically(self):
        path = incentives.incentives_path(self.local)
        readings = [
            {"vendor": "openai", "account": "a", "names": [], "status": "ok", "limits": [
                {"role": "weekly", "window_minutes": 10, "resets_at": "2026-10-03T12:00:00+00:00"}]},
            {"vendor": "openai", "account": "b", "names": [], "status": "unread", "limits": []},
        ]
        with self.assertRaises(ValueError):
            incentives.set_incentive(self.cat, "openai", 10, now=NOW, readings=readings, path=path)
        self.assertFalse(path.exists())

    def test_reset_uses_actual_time_limit_kind_and_requires_known_later_window(self):
        reading = {"limits": [
            {"role": "extra", "kind": "TIME_LIMIT", "window_minutes": 1, "resets_at": "2026-10-10T12:00:00+00:00"},
            {"role": "weekly", "window_minutes": 10, "resets_at": "2026-10-03T12:00:00+00:00"},
            {"role": "month", "window_minutes": 100, "resets_at": None, "used_at_least": .1},
        ]}
        self.assertIsNone(incentives._reset(reading, NOW))
        self.assertIsNone(incentives._reset({"limits": [
            {"role": "weekly", "window_minutes": 10, "resets_at": "2026-10-03T12:00:00+00:00"},
            {"role": "month", "window_minutes": 100, "resets_at": "2026-10-02T11:00:00+00:00"},
        ]}, NOW))

    def test_account_switch_excludes_only_the_explicitly_bound_candidate_despite_incentive(self):
        self.cat.off = [{"target": "openai", "account": "a"}]
        policy = [{"target": "openai", "multiplier": 10, "activated_at": NOW.isoformat(),
                   "until": "2026-10-03T12:00:00+00:00"}]
        blocked = choice.rank(self.cat, tier="heavy", candidates=["codex"], attempts=[], quota={}, deadline=900,
                              now=NOW, temperature=0, incentives=policy, accounts={"gpt-6.1-sol": "a"})
        sibling = choice.rank(self.cat, tier="heavy", candidates=["codex"], attempts=[], quota={}, deadline=900,
                              now=NOW, temperature=0, incentives=policy, accounts={"gpt-6.1-sol": "b"})
        self.assertIsNone(blocked)
        self.assertIsNotNone(sibling)
        self.assertEqual(sibling["candidates"][0]["multiplier"], 10)

    def test_overlay_keeps_live_steering_for_a_vendor_switched_off_from_choice(self):
        self.cat.off = [{"target": "openai"}]
        policy = [{"target": "openai", "multiplier": 10, "activated_at": NOW.isoformat(),
                   "until": "2026-10-03T12:00:00+00:00"}]
        reading = {"vendor": "openai", "account": "a", "names": [], "status": "ok", "limits": []}
        self.assertTrue(incentives.overlay([reading], self.cat, policy, NOW)[0]["steering"]["routes"])
        self.assertIsNone(choice.rank(self.cat, tier="heavy", candidates=["codex"], attempts=[], quota={}, deadline=900,
                                       now=NOW, temperature=0, incentives=policy))

    def test_reset_bindings_without_aliases_decode_without_a_key_error(self):
        group = {"target": "openai", "multiplier": 10, "activated_at": NOW.isoformat(), "bindings": [
            {"vendor": "openai", "account": "a", "until": "2026-10-03T12:00:00+00:00"}]}
        path = incentives.incentives_path(self.local)
        incentives.write([group], path)
        self.assertEqual(incentives.read(path, NOW)[0]["bindings"][0]["account"], "a")

    def test_reset_group_rejects_a_boolean_account_selector(self):
        group = {"target": "openai", "account": True, "multiplier": 10, "activated_at": NOW.isoformat(), "bindings": [
            {"vendor": "openai", "account": "a", "names": [], "until": "2026-10-03T12:00:00+00:00"}]}
        with self.assertRaises(ValueError):
            incentives.write([group], incentives.incentives_path(self.local))

    def test_expired_account_bindings_drop_from_reads_and_the_next_write(self):
        group = {"target": "openai", "multiplier": 10, "activated_at": NOW.isoformat(), "bindings": [
            {"vendor": "openai", "account": "a", "names": [], "until": "2026-10-02T11:00:00+00:00"},
            {"vendor": "openai", "account": "b", "names": [], "until": "2026-10-03T12:00:00+00:00"}]}
        path = incentives.incentives_path(self.local)
        incentives.write([group], path)
        self.assertEqual([b["account"] for b in incentives.read(path, NOW)[0]["bindings"]], ["b"])
        incentives.set_incentive(self.cat, "glm", 2, now=NOW, duration=timedelta(hours=1), path=path)
        self.assertEqual([b["account"] for b in incentives.read(path, NOW)[0]["bindings"]], ["b"])

    def test_public_activation_normalizes_a_reading_alias_for_clear_by_id(self):
        reading = {"vendor": "openai", "account": "a", "names": ["primary"], "status": "ok", "limits": [
            {"role": "weekly", "window_minutes": 10080, "resets_at": "2026-10-03T12:00:00+00:00"}]}
        path = incentives.incentives_path(self.local)
        group = incentives.set_incentive(self.cat, "openai", 10, account="primary", now=NOW, readings=[reading], path=path)
        self.assertEqual(group["account"], "a")
        self.assertEqual(incentives.clear_incentive(self.cat, "openai", account="a", now=NOW, path=path), [])

    def test_registered_vendor_without_offerings_can_expire_at_its_account_reset(self):
        cat = catalog.Catalog({"tiers": ["standard"], "models": {}, "offerings": []})
        reading = {"vendor": "neuralwatt", "account": "a", "names": [], "status": "ok", "limits": [
            {"role": "weekly", "window_minutes": 10080, "resets_at": "2026-10-03T12:00:00+00:00"}]}
        group = incentives.set_incentive(cat, "neuralwatt", 2, now=NOW, readings=[reading],
                                         path=incentives.incentives_path(self.local))
        self.assertEqual(group["bindings"][0]["account"], "a")
        self.assertEqual(incentives.overlay([reading], cat, [group], NOW)[0]["steering"]["settings"][0]["multiplier"], 2)

    def test_cli_reads_a_registered_vendor_even_without_catalog_offerings(self):
        import io
        import os
        from contextlib import redirect_stdout, redirect_stderr
        from unittest import mock
        from unlimited import cli
        cat = catalog.Catalog({"tiers": ["standard"], "models": {}, "offerings": []})
        reading = {"vendor": "neuralwatt", "account": "a", "names": [], "status": "ok", "limits": [
            {"role": "weekly", "window_minutes": 10080, "resets_at": "2026-10-03T12:00:00+00:00"}]}
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": scratch.mkdtemp()}), \
                mock.patch.object(catalog, "load", return_value=cat), \
                mock.patch.object(cli.cache, "through", return_value=[reading]), \
                mock.patch.object(cli, "datetime") as clock, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            clock.now.return_value = NOW
            self.assertEqual(cli.main(["incentive", "neuralwatt", "2x", "--json"]), 0)

    def test_mutation_defaults_to_the_custom_catalog_policy_file(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": scratch.mkdtemp()}):
            incentives.set_incentive(self.cat, "glm", 10, now=NOW, duration=timedelta(hours=1))
            got = choice.choose(self.cat, tier="heavy", candidates=["glm"], quota={}, deadline=900,
                                now=NOW, temperature=0, log=Path(scratch.mkdtemp()) / "d.jsonl")
            self.assertEqual(got["candidates"][0]["multiplier"], 10)
            self.assertEqual(incentives.clear_incentive(self.cat, "glm", account=None, now=NOW), [])

    def test_ambiguous_local_alias_is_refused_for_activation_and_binding(self):
        from unlimited.adapters import REGISTRY
        adapter = mock.Mock(names=mock.Mock(return_value={"a": ["shared"], "b": ["shared"]}))
        with mock.patch.dict(REGISTRY, {"openai": adapter}):
            for call in (lambda: incentives.account_context(self.cat, "openai", "shared"),
                         lambda: incentives.account_binding(self.cat, "gpt-6.1-sol", "shared")):
                with self.assertRaises(ValueError):
                    call()

    def test_offering_scope_does_not_leak_to_a_sibling_using_its_model_name(self):
        cat = catalog.Catalog({"tiers": ["standard"], "models": {"sonnet": {"provider": "claude", "tiers": ["standard"]}},
                               "offerings": [{"id": "sonnet", "model": "sonnet", "vendor": "anthropic"},
                                             {"id": "reseller/sonnet", "model": "sonnet", "vendor": "opencode"}]})
        group = {"target": "sonnet", "multiplier": .1, "activated_at": NOW.isoformat(),
                 "until": (NOW + timedelta(hours=1)).isoformat()}
        resolved = incentives.resolve(cat, [group], {}, NOW)
        self.assertEqual(resolved.factors["sonnet"], .1)
        self.assertEqual(resolved.factors["reseller/sonnet"], 1)

    def test_softmax_rebases_fallback_weights_after_a_dominant_first_pick(self):
        scored = [{"e": 0}, {"e": 1000}, {"e": 2000}]
        self.assertEqual(choice.order(scored, 1, random.Random(42)), [0, 1, 2])

    def test_cli_can_clear_a_stored_scope_no_longer_in_the_catalog(self):
        from unlimited import cli
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": scratch.mkdtemp()}), \
                mock.patch.object(catalog, "load", return_value=catalog.Catalog({"tiers": ["standard"]})), \
                mock.patch.object(cli, "datetime") as clock, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            clock.now.return_value = NOW
            incentives.write([{"target": "retired-route", "multiplier": 2, "activated_at": NOW.isoformat(),
                               "until": (NOW + timedelta(hours=1)).isoformat()}])
            self.assertEqual(cli.main(["incentive", "retired-route", "off", "--json"]), 0)
            self.assertEqual(incentives.read(now=NOW), [])

    def test_tracking_without_incentives_does_not_require_a_valid_catalog(self):
        from unlimited import cli
        reading = {"schema": 1, "vendor": "openai", "account": "a", "status": "ok", "limits": []}
        output = io.StringIO()
        with mock.patch.object(catalog, "load", side_effect=catalog.CatalogError("broken")), \
                mock.patch.object(incentives, "read", return_value=[]), \
                mock.patch.object(cli.cache, "through", return_value=[reading]), \
                redirect_stdout(output), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["read", "--vendor", "openai", "--json"]), 0)
        self.assertEqual(json.loads(output.getvalue())[0]["account"], "a")

    def test_a_tiny_multiplier_is_refused_before_it_can_strand_choice(self):
        path = incentives.incentives_path(self.local)
        for factor in (1e-310, 1e-12):
            with self.assertRaises(ValueError):
                incentives.set_incentive(self.cat, "glm", factor, now=NOW, duration=timedelta(hours=1), path=path)
        self.assertFalse(path.exists())

    def test_a_reused_alias_does_not_inherit_another_accounts_reset_binding(self):
        group = {"target": "openai", "multiplier": 10, "activated_at": NOW.isoformat(), "bindings": [
            {"vendor": "openai", "account": "old", "names": ["primary"], "until": "2026-10-03T12:00:00+00:00"}]}
        context = {"gpt-6.1-sol": {"account": "new", "names": ["primary"]}}
        resolved = incentives.resolve(self.cat, [group], context, NOW)
        self.assertEqual(resolved.factors["gpt-6.1-sol"], 1)

    def test_terminal_render_hides_an_expired_route_winner(self):
        reading = {"vendor": "openai", "account": "a", "status": "ok", "limits": [],
                   "steering": {"routes": [{"multiplier": 10, "until": (NOW + timedelta(hours=1)).isoformat()}]}}
        self.assertNotIn("▲", show.render([reading], NOW + timedelta(hours=2)))


