"""The account verdict (docs/choice.md): the ranking and exclusion rules, the model scope, the
vendor stop and the monthly bucket."""

from __future__ import annotations

import copy
import io
import os
import json
import math
import subprocess
import sys
import textwrap
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from unlimited import catalog, choice, cli, incentives
from unlimited.verdict import verdict

import scratch

NOW = datetime(2026, 9, 24, 8, 0, tzinfo=timezone.utc)
WEEK, FIVE, MONTH = 10080, 300, 43200
HOUR = timedelta(hours=1)


def window(name, used, minutes, left_minutes, *, at_reset=None, exhausts=None, held=None, held_why=None,
           role="weekly", scope=None):
    out = {"name": name, "used_at_least": used, "window_minutes": minutes,
           "resets_at": (NOW + timedelta(minutes=left_minutes)).isoformat(),
           "held": held, "held_why": held_why, "role": role, "scope": scope}
    if at_reset is not None:
        out["projection"] = {"at_reset": list(at_reset), "exhausts_at": exhausts.isoformat() if exhausts else None}
    return out


def five(used, left, **kw):
    return window("five_hour", used, FIVE, left, role="session", **kw)


def reading(*limits, vendor="openai", taken=NOW, status="ok"):
    return {"schema": 1, "vendor": vendor, "account": "a", "taken_at": taken.isoformat(),
            "status": status, "limits": list(limits)}


def v(r, work=HOUR, scope=None, **kw):
    return verdict(r, model_scope=scope, now=NOW, work=work, max_age=timedelta(minutes=5), **kw)


def rank(named: dict) -> list:
    """Tier 0 before 1, higher score first; what a consumer's own ordering starts from."""
    ranked = [(k, x) for k, x in named.items() if x["state"] == "ranked"]
    return [k for k, x in sorted(ranked, key=lambda e: (e[1]["tier"], -e[1]["score"]))]


class Ranking(unittest.TestCase):
    def test_near_its_reset_unused_quota_ranks_first(self):
        late = reading(window("codex", 0.5, WEEK, WEEK * 0.1))
        early = reading(window("codex", 0.5, WEEK, WEEK * 0.3))
        self.assertEqual(rank({"late": v(late), "early": v(early)}), ["late", "early"])
        soon = reading(window("codex", 0.5, WEEK, WEEK * 0.2, at_reset=(0.6, 0.7)))
        far = reading(window("codex", 0.5, WEEK, WEEK * 0.8, at_reset=(0.6, 0.7)))
        self.assertEqual(rank({"far": v(far), "soon": v(soon)}), ["soon", "far"])

    def test_projected_to_run_out_ranks_below_every_account_that_is_not(self):
        one = reading(window("seven_day", 0.40, WEEK, 4 * 1440 + 22 * 60, at_reset=(0.94, 1.37),
                             exhausts=NOW + timedelta(days=3, hours=1)),
                      five(0.03, 137, at_reset=(0.03, 0.14)), vendor="anthropic")
        two = reading(window("seven_day", 0.63, WEEK, 2 * 1440 + 8 * 60, at_reset=(0.87, 0.95)),
                      five(0.01, 237, at_reset=(0.02, 0.15)), vendor="anthropic")
        three = reading(window("seven_day", 0.01, WEEK, 6 * 1440 + 18 * 60, at_reset=(0.34, 0.74)),
                        five(0.03, 227, at_reset=(0.03, 0.44)), vendor="anthropic")
        self.assertEqual(rank({"1": v(one), "2": v(two), "3": v(three)}), ["3", "2", "1"])

    def test_the_run_out_time_names_the_window_it_belongs_to(self):
        # The week is scored, but the five-hour window runs out first: a time shown beside the
        # week's forecast without this reads as the week's.
        r = reading(window("seven_day", 0.41, WEEK, 3 * 1440, at_reset=(0.74, 1.19),
                           exhausts=NOW + timedelta(days=2)),
                    five(0.8, 200, at_reset=(0.9, 1.3), exhausts=NOW + 2 * HOUR), vendor="anthropic")
        got = v(r)
        self.assertEqual((got["window"], got["exhausts_by"]), ("seven_day", "five_hour"), got)
        self.assertEqual(got["exhausts_at"], (NOW + 2 * HOUR).isoformat())
        calm = v(reading(window("codex", 0.2, WEEK, 5000, at_reset=(0.3, 0.4))))
        self.assertIsNone(calm["exhausts_by"])

    def test_95_percent_resetting_in_twenty_minutes_is_not_excluded_for_shorter_work(self):
        for r in (reading(window("codex", 0.95, WEEK, 20, at_reset=(0.95, 0.96))),
                  reading(window("codex", 0.95, WEEK, 20))):
            got = v(r, timedelta(minutes=19))
            self.assertEqual(got["state"], "ranked", got)

    def test_a_window_not_yet_opened_is_present_and_constrains_nothing(self):
        idle = reading({"name": "five_hour", "used_at_least": 0.0, "window_minutes": FIVE, "resets_at": None,
                        "held": None, "role": "session", "scope": None},
                       window("seven_day", 0.1, WEEK, 5000, at_reset=(0.3, 0.4)), vendor="anthropic")
        self.assertEqual((v(idle)["state"], v(idle)["tier"]), ("ranked", 0))

    def test_a_window_that_resets_before_the_work_starts_neither_scores_nor_tiers(self):
        r = reading(window("codex", 0.5, WEEK, WEEK * 0.5, at_reset=(0.6, 0.7)),
                    window("secondary", 0.9, WEEK, 200, at_reset=(1.5, 1.6)))
        got = v(r, starts=NOW + timedelta(minutes=300))
        self.assertEqual((got["window"], got["tier"]), ("codex", 0))

    def test_exclusions_and_when_they_lift(self):
        spent = reading(window("seven_day", 0.3, WEEK, 5000, at_reset=(0.4, 0.5)),
                        five(1.0, 90, at_reset=(1.0, 1.0)), vendor="anthropic")
        got = v(spent)
        self.assertEqual((got["reason"], got["by"], got["until"]), ("exhausted", "five_hour", spent["limits"][1]["resets_at"]))
        self.assertEqual(v(spent, starts=NOW + timedelta(minutes=91))["state"], "ranked")
        running = reading(window("codex", 0.8, WEEK, 3000, at_reset=(1.1, 1.3), exhausts=NOW + 2 * HOUR))
        self.assertEqual(v(running, 3 * HOUR)["reason"], "runs-out")
        self.assertEqual((v(running, HOUR)["state"], v(running, HOUR)["tier"]), ("ranked", 1))

    def test_unread_cases(self):
        for r in (None, reading(status="refused"), reading(window("codex", "x", WEEK, 100)),
                  reading(vendor="anthropic"), reading(window("seven_day", 0.1, WEEK, 100), vendor="anthropic")):
            self.assertEqual(v(r)["state"], "unread", r)

    def test_a_vendor_switched_off_here_is_excluded_before_any_window(self):
        r = reading(five(0.2, 300), vendor="openai")
        self.assertEqual(v(r, off={"openai": None})["state"], "excluded")
        self.assertEqual(v(r, off={"openai": "2026-09-24T10:00:00+00:00"}),
                         {"state": "excluded", "reason": "off", "until": "2026-09-24T10:00:00+00:00"})
        self.assertEqual(v(r)["state"], "ranked")                      # no policy: unchanged
        self.assertEqual(v(r, off={"zai": None})["state"], "ranked")   # another vendor's switch
        self.assertEqual(v("broken", off={"openai": None})["state"], "unread")  # not a reading

    def test_one_account_of_a_vendor_switched_off_excludes_only_it(self):
        zai = lambda **kw: {**reading(five(0.2, 300), window("seven_day", 0.2, WEEK, 5000), vendor="zai"), **kw}
        glm2 = zai(account="4ff9f720f21e938b", names=["claude-glm-2"])
        glm1 = zai(account="da68cb2cdf8f3998", names=["claude-glm"])
        by_name = {"zai/claude-glm-2": None}
        self.assertEqual(v(glm2, off=by_name)["state"], "excluded")
        self.assertEqual(v(glm1, off=by_name)["state"], "ranked")
        by_id = {"zai/4ff9f720f21e938b": "2026-09-25T00:00:00+00:00"}  # the account id works too
        self.assertEqual(v(glm2, off=by_id),
                         {"state": "excluded", "reason": "off", "until": "2026-09-25T00:00:00+00:00"})
        self.assertEqual(v(glm1, off=by_id)["state"], "ranked")


class Scope(unittest.TestCase):
    def test_an_exhausted_opus_week_excludes_opus_work_and_not_sonnet_work(self):
        r = reading(window("seven_day", 0.3, WEEK, 5000, at_reset=(0.4, 0.5)), five(0.1, 200),
                    window("seven_day_opus", 1.0, WEEK, 3000, role="weekly_model", scope="Opus"),
                    window("seven_day_oauth_apps", 0.2, WEEK, 3000, role="extra", scope="oauth_apps"),
                    vendor="anthropic")
        self.assertEqual((v(r, scope="opus")["state"], v(r, scope="opus")["by"]), ("excluded", "seven_day_opus"))
        self.assertEqual(v(r, scope="sonnet")["state"], "ranked")
        self.assertIn("seven_day_oauth_apps", v(r, scope="sonnet")["binding"], "an extra pool binds all work")

    def test_a_reading_without_roles_binds_every_limit(self):
        opus = {k: x for k, x in window("seven_day_opus", 1.0, WEEK, 3000).items() if k not in ("role", "scope")}
        r = reading(window("seven_day", 0.3, WEEK, 5000), five(0.1, 200), opus, vendor="anthropic")
        self.assertEqual(v(r, scope="sonnet")["state"], "excluded")

    def test_a_severity_entry_binds_nothing(self):
        sev = window("limits:weekly_all", 1.0, WEEK, 3000, role=None)
        r = reading(window("seven_day", 0.3, WEEK, 5000), five(0.1, 200), sev, vendor="anthropic")
        self.assertEqual(v(r)["state"], "ranked")

    def test_the_monthly_bucket_is_scored_where_a_plan_has_one(self):
        r = reading(five(0.0, 200), window("seven_day", 0.1, WEEK, 3000),
                    window("month", 0.9, MONTH, 20000, role="month"), vendor="opencode")
        self.assertEqual(v(r)["window"], "month")
        weekly_only = reading(window("codex", 0.1, WEEK, 3000))
        self.assertEqual(v(weekly_only)["window"], "codex")

    def test_only_a_vendor_stop_excludes_by_itself(self):
        stop = reading(window("codex", 0.5, WEEK, 1000, held=True, held_why="limit_reached"))
        self.assertEqual((v(stop)["state"], v(stop)["reason"]), ("excluded", "stopped"))
        overage = reading(window("month", 0.5, MONTH, 10000, held=True, held_why="overage", role="month"))
        self.assertEqual(v(overage)["state"], "ranked")

    def test_a_stale_reading_is_unread(self):
        old = reading(window("codex", 0.1, WEEK, 3000), taken=NOW - timedelta(minutes=6))
        self.assertEqual(v(old), {"state": "unread", "reason": "stale"})


class Preference(unittest.TestCase):
    def setUp(self):
        self.cat = catalog.Catalog({
            "tiers": ["standard"],
            "models": {"m": {"provider": "p", "tiers": ["standard"]}},
            "offerings": [{"id": "o", "model": "m", "vendor": "openai"}],
        })

    def context(self, factor=1.0, *, target="openai", account="a", until=None):
        return {"v": 1, "vendor": "openai", "account": account,
                "observed_at": NOW.isoformat(), "settings": [
                    {"target": target, "multiplier": factor, "activated_at": NOW.isoformat(),
                     "until": (until or NOW + HOUR).isoformat()}]}

    def policy(self, context, *, offering="o", now=NOW):
        return incentives.evaluate_context(
            self.cat, context, vendor="openai", account="a", offering=offering, now=now)

    def test_omitted_and_none_preserve_the_raw_ranked_dictionary(self):
        for tier, forecast, exhausts, score in ((0, .1, None, 1.8), (1, 1.2, NOW + 2 * HOUR, 2.0)):
            with self.subTest(tier=tier):
                r = reading(window("codex", .05, WEEK, WEEK / 2,
                                   at_reset=(forecast, forecast), exhausts=exhausts))
                r["steering"] = self.context(10)
                expected = {"window": "codex", "used": .05, "at_reset": [forecast, forecast], "paced": False,
                            "resets_at": (NOW + timedelta(minutes=WEEK / 2)).isoformat(),
                            "exhausts_at": exhausts.isoformat() if exhausts else None,
                            "exhausts_by": "codex" if exhausts else None,
                            "runway": 7200.0 if exhausts else None,
                            "binding": ["codex"], "state": "ranked", "tier": tier, "score": score}
                self.assertEqual(v(r), expected)
                self.assertEqual(v(r, policy=None), expected)
                self.assertEqual(json.dumps(v(r)), json.dumps(v(r, policy=None)))

    def test_preference_is_additive_and_does_not_mutate_inputs(self):
        r = reading(window("codex", .05, WEEK, WEEK / 2, at_reset=(.1, .1)))
        context = self.context(10)
        context["settings"][0]["account"] = "a"
        policy = {**self.policy(context), "base_score": -1, "effective_score": -1}
        self.assertIs(policy["account_scoped"], True)
        self.assertEqual((policy["target"], policy["scope"], policy["until"]),
                         ("openai", "vendor", (NOW + HOUR).isoformat()))
        before = copy.deepcopy((r, policy))
        raw = v(r)
        weighted = v(r, policy=policy)
        self.assertEqual({k: x for k, x in weighted.items() if k != "preference"}, raw)
        self.assertEqual(weighted["preference"],
                         {**policy, "base_score": 1.8, "effective_score": 18.0})
        self.assertEqual((r, policy), before)

    def test_empty_expired_and_unavailable_policy_keep_usable_quota(self):
        good = self.context()
        cases = [
            ({**good, "settings": []}, "neutral", "no-live-rule"),
            (self.context(10, until=NOW), "neutral", "no-live-rule"),
            (good, "neutral", "explicit-neutral"),
            (None, "unavailable", "missing-context"),
            ({**good, "v": 999}, "unavailable", "unsupported-version"),
            ({**good, "error": "source policy unreadable"}, "unavailable", "policy-unavailable"),
            ({**good, "settings": "broken"}, "unavailable", "invalid-context"),
        ]
        r = reading(window("codex", .05, WEEK, WEEK / 2, at_reset=(.1, .1)))
        raw = v(r)
        for context, status, reason in cases:
            with self.subTest(reason=reason):
                policy = self.policy(context)
                weighted = v(r, policy=policy)
                self.assertEqual({k: x for k, x in weighted.items() if k != "preference"}, raw)
                pref = weighted["preference"]
                self.assertEqual((pref["status"], pref["reason"], pref["multiplier"]),
                                 (status, reason, 1.0))
                self.assertEqual(pref["effective_score"].hex(), raw["score"].hex())
        unavailable = incentives.evaluate_context(
            None, good, vendor="openai", account="a", offering=None, now=NOW)
        self.assertEqual(unavailable["reason"], "catalog-unavailable")
        self.assertEqual(v(r, policy=unavailable)["preference"]["effective_score"], 1.8)

    def test_different_source_policies_promote_and_discourage_before_reduction(self):
        rows = [
            reading(window("codex", .05, WEEK, WEEK / 2, at_reset=(.1, .1))),
            reading(window("codex", .05, WEEK, WEEK / 14, at_reset=(.4, .4))),
        ]
        plain = [v(r, policy=self.policy(self.context())) for r in rows]
        strict = lambda vs: min(range(len(vs)), key=lambda i:
                               (vs[i]["tier"], -vs[i]["preference"]["effective_score"]))
        # This demonstrates SDK quantities, not a new product account-selector API.
        self.assertEqual(min(range(len(rows)), key=lambda i: plain[i]["at_reset"][1]), 0)
        self.assertEqual(strict(plain), 1)
        encouraged = [v(rows[0], policy=self.policy(self.context(10))), plain[1]]
        self.assertEqual(strict(encouraged), 0)
        discouraged = [plain[0], v(rows[1], policy=self.policy(self.context(.1)))]
        self.assertEqual(strict(discouraged), 0)
        for got, raw in zip(encouraged, plain):
            self.assertEqual(got["at_reset"], raw["at_reset"])
        same_account = rows[0]
        source_a = v(same_account, policy=self.policy(self.context(10)))
        source_b = v(same_account, policy=self.policy(self.context(.1)))
        self.assertEqual(source_a["preference"]["multiplier"], 10)
        self.assertEqual(source_b["preference"]["multiplier"], .1)
        shared = [v(r, policy=self.policy(self.context(5))) for r in rows]
        self.assertEqual(strict(shared), strict(plain))
        self.assertEqual(strict([plain[0], copy.deepcopy(plain[0])]), 0)

    def test_policy_does_not_cross_tiers_or_add_preference_to_nonranked_verdicts(self):
        strong = self.policy(self.context(sys.float_info.max))
        weak = self.policy(self.context(1e-6))
        calm = reading(window("codex", .05, WEEK, WEEK / 2, at_reset=(.9, .9)))
        risky = reading(window("codex", .05, WEEK, WEEK / 2, at_reset=(1.2, 1.2),
                               exhausts=NOW + 2 * HOUR))
        ranked = [v(calm, policy=weak), v(risky, policy=strong)]
        self.assertEqual([x["tier"] for x in ranked], [0, 1])
        self.assertGreater(ranked[1]["preference"]["effective_score"],
                           ranked[0]["preference"]["effective_score"])
        self.assertEqual(min(range(2), key=lambda i:
                             (ranked[i]["tier"], -ranked[i]["preference"]["effective_score"])), 0)
        cases = [
            (None, {}),
            (reading(status="refused"), {}),
            (reading(window("codex", .05, WEEK, 100), taken=NOW - timedelta(minutes=6)), {}),
            (reading(window("codex", 1, WEEK, 100)), {}),
            (reading(window("codex", .2, WEEK, 100, held=True, held_why="limit_reached")), {}),
            (risky, {"work": 3 * HOUR}),
            (calm, {"off": {"openai/a": None}}),
        ]
        for r, kw in cases:
            with self.subTest(reading=r, options=kw):
                raw = v(r, **kw)
                self.assertIn(raw["state"], ("unread", "excluded"))
                self.assertEqual(v(r, policy=None, **kw), raw)
                self.assertEqual(v(r, policy=strong, **kw), raw)
                self.assertNotIn("preference", v(r, policy=strong, **kw))
        scoped = reading(window("codex", .05, WEEK, 100, at_reset=(.2, .2)),
                         window("model_week", 1, WEEK, 100, role="weekly_model", scope="X"))
        self.assertEqual(v(scoped, scope="X", policy=strong)["state"], "excluded")
        self.assertEqual(v(scoped, scope="Y", policy=strong)["state"], "ranked")

    def test_clamped_actual_scores_are_finite_and_zero_stays_zero(self):
        cap = self.policy(self.context(sys.float_info.max))
        tier_zero = reading(window("codex", 0, WEEK, 1, at_reset=(0, 0)))
        far = window("month", .05, 43200, 100, role="month", at_reset=(1.2, 1.2))
        far["resets_at"] = datetime.max.replace(tzinfo=timezone.utc).isoformat()
        for r in (tier_zero, reading(far)):
            with self.subTest(reading=r):
                got = v(r, policy=cap)
                self.assertEqual(got["state"], "ranked")
                self.assertTrue(math.isfinite(got["preference"]["effective_score"]))
                self.assertEqual(got["preference"]["effective_score"], got["score"] * 1e6)
                json.dumps(got, allow_nan=False)
        zero_window = window("codex", .05, WEEK, WEEK / 2,
                             at_reset=(1, 1), exhausts=NOW + HOUR)
        zero_window["projection"]["exhausts_at"] = NOW.isoformat()
        got = v(reading(zero_window), work=timedelta(0), policy=cap)
        self.assertEqual(got["score"], 0.0)
        self.assertEqual(got["preference"]["effective_score"], 0.0)

    def test_explicit_neutral_preserves_adjacent_raw_band_comparisons(self):
        r = reading(window("codex", .05, WEEK, WEEK / 2, at_reset=(.1, .1)))
        raw = v(r)
        weighted = v(r, policy=self.policy(self.context()))
        best = raw["score"]
        effective = weighted["preference"]["effective_score"]
        boundary = best - .1 * abs(best)
        effective_boundary = effective - .1 * abs(effective)
        self.assertEqual(effective.hex(), best.hex())
        outcomes = []
        for candidate in (math.nextafter(boundary, -math.inf), boundary,
                          math.nextafter(boundary, math.inf)):
            outcomes.append(candidate >= effective_boundary)
            self.assertEqual(candidate >= effective_boundary, candidate >= boundary)
        self.assertEqual(outcomes, [False, True, True])

    def test_route_and_verdict_use_the_same_effective_factor_with_raw_quota(self):
        context = self.context(sys.float_info.max, target="o")
        policy = self.policy(context)
        r = reading(window("codex", .05, WEEK, WEEK / 2, at_reset=(.1, .1)))
        weighted = v(r, policy=policy)
        self.assertEqual((weighted["preference"]["scope"], weighted["preference"]["target"]), ("offering", "o"))
        unspecified = v(r, policy=self.policy(context, offering=None))
        self.assertEqual((unspecified["preference"]["multiplier"], unspecified["preference"]["effective_score"],
                          unspecified["preference"]["reason"]), (1.0, 1.8, "no-live-rule"))
        quota = {"o": weighted["at_reset"][1]}
        args = dict(tier="standard", candidates=["o"], attempts=[], quota=quota,
                    deadline=900, now=NOW, seed=7, contexts={"o": context})
        for temperature in (0, .5, None):
            with self.subTest(temperature=temperature):
                got = choice.rank(self.cat, temperature=temperature, **args)
                candidate = got["candidates"][0]
                self.assertEqual(candidate["multiplier"], policy["multiplier"])
                self.assertEqual(candidate["rho"], v(r)["at_reset"][1])
                self.assertEqual(candidate["pi"], choice.price(v(r)["at_reset"][1]))
                for key in ("t_ok", "t_fail", "t_next", "e"):
                    self.assertTrue(math.isfinite(candidate[key]))
                neutral = self.context()
                baseline = choice.rank(self.cat, temperature=temperature,
                                       **{**args, "contexts": {"o": neutral}})
                for key in ("rho", "pi", "p", "t_ok", "t_fail", "t_next", "preference"):
                    self.assertEqual(candidate[key], baseline["candidates"][0][key])
                again = choice.rank(self.cat, temperature=temperature, **args)
                self.assertEqual(got["order"], again["order"])
                self.assertEqual(got["candidates"], again["candidates"])


class Cli(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {
            "HOME": scratch.mkdtemp(), "XDG_CONFIG_HOME": scratch.mkdtemp(),
            "XDG_CACHE_HOME": scratch.mkdtemp(), "XDG_STATE_HOME": scratch.mkdtemp(),
        }, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_verdict_prints_one_per_account(self):
        import io
        import json
        from contextlib import redirect_stdout
        from unittest import mock

        from unlimited import cli
        r = reading(window("codex", 0.1, WEEK, 3000))
        r["taken_at"] = datetime.now(timezone.utc).isoformat()
        r["limits"][0]["resets_at"] = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        buf = io.StringIO()
        with mock.patch.object(cli.cache, "through", return_value=[dict(r, names=["x"])]), redirect_stdout(buf):
            self.assertEqual(cli.main(["verdict", "--vendor", "openai", "--work", "600", "--json"]), 0)
        (got,) = json.loads(buf.getvalue())
        self.assertEqual((got["vendor"], got["names"], got["verdict"]["state"]), ("openai", ["x"], "ranked"))


class VerdictPolicyCli(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.config = scratch.mkdtemp()
        self.stack.enter_context(mock.patch.dict(os.environ, {
            "HOME": scratch.mkdtemp(), "XDG_CONFIG_HOME": self.config,
            "XDG_CACHE_HOME": scratch.mkdtemp(),
            "XDG_STATE_HOME": scratch.mkdtemp(),
        }, clear=True))
        self.cat_data = {
            "tiers": ["standard"],
            "models": {"model-a": {"provider": "maker", "tiers": ["standard"]}},
            "offerings": [
                {"id": "route-a", "model": "model-a", "vendor": "openai"},
                {"id": "route-b", "model": "model-a", "vendor": "neuralwatt"},
            ],
        }
        self.cat = catalog.Catalog(self.cat_data)
        self.groups = []
        self.readings = [reading(window("codex", .1, WEEK, 3000,
                                        at_reset=(.3, .4)))]
        adapters = {v: SimpleNamespace(VENDOR=v) for v in ("openai", "neuralwatt")}
        self.stack.enter_context(mock.patch.dict(cli.REGISTRY, adapters, clear=True))
        self.stack.enter_context(mock.patch.object(catalog, "load_metadata", return_value=self.cat))
        self.off = self.stack.enter_context(mock.patch.object(catalog, "off_policy", return_value={}))
        self.stack.enter_context(mock.patch.object(incentives, "read",
                                                  side_effect=lambda *a, **k: self.groups))
        self.clock = self.stack.enter_context(mock.patch.object(cli, "datetime"))
        self.clock.now.return_value = NOW
        self.read_cache = self.stack.enter_context(mock.patch.object(
            cli.cache, "through", side_effect=lambda adapter, **kw: [
                copy.deepcopy(r) for r in self.readings if r["vendor"] == adapter.VENDOR]))

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["verdict", "--work", "600", "--json", *args])
        return code, out.getvalue(), err.getvalue()

    def test_offering_infers_only_its_usage_vendor(self):
        code, text, _ = self.run_cli("--offering", "route-a")
        self.assertEqual(code, 0)
        self.assertEqual([r["vendor"] for r in json.loads(text)], ["openai"])
        self.assertEqual([c.args[0].VENDOR for c in self.read_cache.call_args_list], ["openai"])

    def test_explicit_cross_vendor_requests_fail_before_cache_io(self):
        for vendors in (("neuralwatt",), ("openai", "neuralwatt")):
            self.read_cache.reset_mock()
            args = [x for v in vendors for x in ("--vendor", v)]
            code, text, err = self.run_cli("--offering", "route-a", *args)
            self.assertEqual((code, text), (2, ""))
            self.assertIn("--offering", err)
            self.read_cache.assert_not_called()

    def test_non_offering_names_fail_before_cache_io(self):
        for name in ("unknown", "maker", "model-a"):
            self.read_cache.reset_mock()
            code, text, _ = self.run_cli("--offering", name)
            self.assertEqual((code, text), (2, ""))
            self.read_cache.assert_not_called()

    def test_an_unreadable_offering_catalog_fails_before_cache_io(self):
        with mock.patch.object(catalog, "load_metadata", side_effect=catalog.CatalogError("broken")):
            code, text, _ = self.run_cli("--offering", "route-a")
        self.assertEqual((code, text), (2, ""))
        self.read_cache.assert_not_called()

    def test_matching_explicit_vendor_is_accepted(self):
        code, text, _ = self.run_cli("--offering", "route-a", "--vendor", "openai")
        self.assertEqual(code, 0)
        self.assertEqual([r["vendor"] for r in json.loads(text)], ["openai"])

    def test_unregistered_offering_vendor_is_refused_before_cache_io(self):
        self.cat.offerings.append({"id": "external/a", "model": "model-a", "vendor": "external"})
        code, text, _ = self.run_cli("--offering", "external/a")
        self.assertEqual((code, text), (2, ""))
        self.read_cache.assert_not_called()

    def test_offering_identity_does_not_add_route_admission_to_verdict(self):
        self.cat.banned = frozenset({"route-a"})
        self.cat.off = [{"target": "route-a"}]
        code, text, _ = self.run_cli("--offering", "route-a")
        self.assertEqual(code, 0)
        (got,) = json.loads(text)
        self.assertEqual(got["verdict"]["state"], "ranked")

    def test_repeatable_matching_vendors_preserve_read_behavior(self):
        code, text, _ = self.run_cli("--offering", "route-a", "--vendor", "openai", "--vendor", "openai")
        self.assertEqual(code, 0)
        self.assertEqual([r["vendor"] for r in json.loads(text)], ["openai", "openai"])
        self.assertEqual([c.args[0].VENDOR for c in self.read_cache.call_args_list], ["openai", "openai"])

    def test_model_name_that_is_an_offering_id_is_accepted(self):
        self.cat.offerings.append({"id": "model-a", "model": "model-a", "vendor": "openai"})
        code, text, _ = self.run_cli("--offering", "model-a")
        self.assertEqual(code, 0)
        self.assertEqual([r["vendor"] for r in json.loads(text)], ["openai"])

    def test_sibling_offering_infers_its_own_vendor(self):
        self.readings = [reading(window("codex", .1, WEEK, 3000), vendor="neuralwatt")]
        code, text, _ = self.run_cli("--offering", "route-b")
        self.assertEqual(code, 0)
        self.assertEqual([r["vendor"] for r in json.loads(text)], ["neuralwatt"])
        self.assertEqual([c.args[0].VENDOR for c in self.read_cache.call_args_list], ["neuralwatt"])

    def test_metadata_failures_with_offering_are_fatal_before_cache_io(self):
        for error in (OSError, ValueError, TypeError, OverflowError):
            with self.subTest(error=error), mock.patch.object(catalog, "load_metadata", side_effect=error("private")):
                code, text, err = self.run_cli("--offering", "route-a")
            self.assertEqual((code, text), (2, ""))
            self.assertIn("--offering", err)
            self.read_cache.assert_not_called()

    def test_expired_offering_is_still_an_identity(self):
        self.cat.offerings[0]["until"] = (NOW - timedelta(days=1)).date()
        code, text, _ = self.run_cli("--offering", "route-a")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text)[0]["verdict"]["state"], "ranked")

    def setting(self, target, factor, *, account=None, until=None):
        return {"target": target, "multiplier": factor,
                "activated_at": (NOW - timedelta(minutes=1)).isoformat(),
                "until": (until or NOW + timedelta(hours=1)).isoformat(),
                **({"account": account} if account is not None else {})}

    def assert_sdk_row(self, got, r, *, offering=None, scope=None):
        annotated = incentives.annotate([r], now=NOW)[0]
        policy = incentives.evaluate_context(self.cat, annotated["steering"],
                                            vendor=r["vendor"], account=r.get("account"),
                                            offering=offering, now=NOW)
        raw = verdict(r, model_scope=scope, now=NOW, work=timedelta(seconds=600),
                      max_age=timedelta(seconds=300), off=self.off.return_value)
        expected = verdict(r, model_scope=scope, now=NOW, work=timedelta(seconds=600),
                           max_age=timedelta(seconds=300), off=self.off.return_value,
                           policy=policy)
        self.assertEqual(got["steering"], annotated["steering"])
        self.assertEqual(got["verdict"], expected)
        self.assertEqual({k: v for k, v in got["verdict"].items() if k != "preference"}, raw)
        self.assertEqual((got["vendor"], got["account"], got["names"]),
                         (r["vendor"], r.get("account"), r.get("names", [])))
        return policy

    def test_cli_sdk_parity_for_vendor_and_actual_offering(self):
        self.groups = [self.setting("openai", 2), self.setting("route-a", 10)]
        for args, offering, factor in ((("--vendor", "openai"), None, 2),
                                       (("--offering", "route-a"), "route-a", 10)):
            code, text, _ = self.run_cli(*args)
            self.assertEqual(code, 0)
            (got,) = json.loads(text)
            policy = self.assert_sdk_row(got, self.readings[0], offering=offering)
            self.assertEqual(policy["multiplier"], factor)
            self.assertEqual(got["verdict"]["preference"]["multiplier"], factor)

    def test_scope_text_is_not_a_catalog_offering(self):
        self.groups = [self.setting("route-a", 10), self.setting("model-a", 4),
                       self.setting("maker", 3)]
        code, text, _ = self.run_cli("--vendor", "openai", "--model-scope", "route-a")
        self.assertEqual(code, 0)
        (got,) = json.loads(text)
        policy = self.assert_sdk_row(got, self.readings[0], scope="route-a")
        self.assertEqual(policy["multiplier"], 1)
        self.assertEqual(got["verdict"]["preference"]["effective_score"], got["verdict"]["score"])

    def test_mixed_vendor_work_without_an_offering_evaluates_each_row_vendor_only(self):
        self.readings.append(reading(window("codex", .1, WEEK, 3000, at_reset=(.3, .4)),
                                     vendor="neuralwatt"))
        self.groups = [self.setting("openai", 2), self.setting("neuralwatt", .1),
                       self.setting("route-a", 10), self.setting("model-a", 5)]
        code, text, _ = self.run_cli("--vendor", "neuralwatt", "--vendor", "openai")
        self.assertEqual(code, 0)
        got = json.loads(text)
        self.assertEqual([x["vendor"] for x in got], ["neuralwatt", "openai"])
        by_vendor = {r["vendor"]: r for r in self.readings}
        for row, factor in zip(got, (.1, 2)):
            policy = self.assert_sdk_row(row, by_vendor[row["vendor"]])
            self.assertEqual(policy["multiplier"], factor)

    def test_actual_offering_does_not_replace_model_family_limits(self):
        self.readings[0]["limits"].append(
            window("opus-week", 1, WEEK, 3000, role="weekly_model", scope="Opus"))
        self.groups = [self.setting("route-a", 10)]
        code, text, _ = self.run_cli("--offering", "route-a")
        self.assertEqual(code, 0)
        (got,) = json.loads(text)
        self.assertEqual(got["verdict"]["state"], "ranked")
        code, text, _ = self.run_cli("--offering", "route-a", "--model-scope", "Opus")
        self.assertEqual(code, 0)
        (got,) = json.loads(text)
        self.assertEqual((got["verdict"]["state"], got["verdict"]["by"]),
                         ("excluded", "opus-week"))
        self.assertNotIn("preference", got["verdict"])

    def test_preference_is_ranked_only_and_does_not_change_quota(self):
        self.groups = [self.setting("openai", 1e300, account="a")]
        cases = [
            (reading(window("codex", .1, WEEK, 3000, at_reset=(.3, .4))), "ranked"),
            (reading(window("codex", .8, WEEK, 3000, at_reset=(1.1, 1.3),
                            exhausts=NOW + timedelta(hours=2))), "ranked"),
            (reading(window("codex", 1, WEEK, 3000)), "excluded"),
            (reading(window("codex", .1, WEEK, 3000), status="refused"), "unread"),
            (reading(window("codex", .1, WEEK, 3000), taken=NOW - timedelta(minutes=6)), "unread"),
        ]
        for r, state in cases:
            self.readings = [r]
            code, text, _ = self.run_cli("--vendor", "openai")
            self.assertEqual(code, 0)
            (got,) = json.loads(text, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
            self.assert_sdk_row(got, r)
            self.assertEqual(got["verdict"]["state"], state)
            if state == "ranked":
                p = got["verdict"]["preference"]
                self.assertEqual((p["requested_multiplier"], p["multiplier"], p["clamped"]),
                                 (1e300, 1e6, True))
                self.assertTrue(math.isfinite(p["effective_score"]))
                self.assertEqual(p["reason"], "clamped")
            else:
                self.assertNotIn("preference", got["verdict"])

    def test_policy_loading_failure_preserves_identity_and_ranked_quota(self):
        private = "/private/fixture/home/incentives.json fixture-secret"
        with mock.patch.object(incentives, "read", side_effect=catalog.CatalogError(private)):
            code, text, _ = self.run_cli("--vendor", "openai")
        self.assertEqual(code, 0)
        (got,) = json.loads(text)
        self.assertEqual((got["account"], got["steering"]["account"]), ("a", "a"))
        self.assertEqual(got["verdict"]["state"], "ranked")
        p = got["verdict"]["preference"]
        self.assertEqual((p["status"], p["reason"], p["multiplier"]),
                         ("unavailable", "policy-unavailable", 1))
        self.assertEqual(p["effective_score"], got["verdict"]["score"])
        self.assertNotIn(private, text)
        self.assertNotIn("fixture-secret", text)
        self.assertNotIn("/private/fixture", text)

    def test_bad_switches_remain_fatal_before_vendor_reads(self):
        self.off.side_effect = catalog.CatalogError("broken switches")
        code, text, _ = self.run_cli("--vendor", "openai")
        self.assertEqual((code, text), (2, ""))
        self.read_cache.assert_not_called()

    def test_neutral_cases_keep_raw_score(self):
        for groups in ([], [self.setting("openai", 1)],
                       [self.setting("openai", 10, until=NOW - timedelta(seconds=1))]):
            self.groups = groups
            code, text, _ = self.run_cli("--vendor", "openai")
            self.assertEqual(code, 0)
            (got,) = json.loads(text)
            self.assert_sdk_row(got, self.readings[0])
            self.assertEqual(got["verdict"]["preference"]["multiplier"], 1)
            self.assertEqual(got["verdict"]["preference"]["effective_score"], got["verdict"]["score"])

    def test_active_policy_catalog_error_is_advisory_without_offering(self):
        self.groups = [self.setting("openai", 2)]
        with mock.patch.object(catalog, "load_metadata", side_effect=catalog.CatalogError(
                "/private/fixture/catalog.toml fixture-secret")):
            code, text, _ = self.run_cli("--vendor", "openai")
        self.assertEqual(code, 0)
        (got,) = json.loads(text)
        self.assertEqual(got["verdict"]["state"], "ranked")
        self.assertEqual(got["verdict"]["preference"]["status"], "unavailable")
        self.assertEqual(got["verdict"]["preference"]["multiplier"], 1)
        self.assertNotIn("/private/fixture", text)
        self.assertNotIn("fixture-secret", text)

    def test_decode_failure_keeps_raw_off_exclusion(self):
        self.off.return_value = {"openai/a": None}
        self.groups = []
        annotated = incentives.annotate(self.readings, now=NOW)
        annotated[0]["steering"]["v"] = 999
        with mock.patch.object(incentives, "_annotate", return_value=(annotated, [])):
            code, text, _ = self.run_cli("--vendor", "openai")
        self.assertEqual(code, 0)
        (got,) = json.loads(text)
        self.assertEqual(got["verdict"], {"state": "excluded", "reason": "off", "until": None})
        self.assertEqual(got["steering"]["account"], "a")

    def test_decode_failures_with_valid_identity_are_unavailable(self):
        for field, value, reason in (("v", 999, "unsupported-version"),
                                     ("settings", "not-a-list", "invalid-context")):
            annotated = incentives.annotate(self.readings, now=NOW)
            annotated[0]["steering"][field] = value
            with mock.patch.object(incentives, "_annotate", return_value=(annotated, [])):
                code, text, _ = self.run_cli("--vendor", "openai")
            self.assertEqual(code, 0)
            (got,) = json.loads(text)
            p = got["verdict"]["preference"]
            self.assertEqual((p["status"], p["reason"], p["multiplier"]),
                             ("unavailable", reason, 1))
            self.assertEqual(got["steering"]["account"], "a")
            raw = verdict(self.readings[0], model_scope=None, now=NOW,
                          work=timedelta(seconds=600), max_age=timedelta(seconds=300), off={})
            self.assertEqual({k: v for k, v in got["verdict"].items() if k != "preference"}, raw)

    def test_valid_policy_with_unavailable_catalog_uses_shared_failure_result(self):
        self.groups = [self.setting("openai", 2)]
        annotated = incentives.annotate(self.readings, now=NOW)
        with mock.patch.object(incentives, "_annotate", return_value=(annotated, [])), \
             mock.patch.object(catalog, "load_metadata", side_effect=catalog.CatalogError("broken")):
            code, text, _ = self.run_cli("--vendor", "openai")
        self.assertEqual(code, 0)
        (got,) = json.loads(text)
        p = got["verdict"]["preference"]
        self.assertEqual((p["status"], p["reason"], p["multiplier"]),
                         ("unavailable", "catalog-unavailable", 1))

    def test_unexpected_cross_vendor_reading_cannot_borrow_offering_policy(self):
        wrong = reading(window("codex", .1, WEEK, 3000, at_reset=(.3, .4)),
                        vendor="neuralwatt")
        self.groups = [self.setting("route-a", 10)]
        self.read_cache.side_effect = None
        self.read_cache.return_value = [wrong]
        code, text, err = self.run_cli("--offering", "route-a")
        self.assertEqual((code, text), (2, ""))
        self.assertTrue(err)

    def test_unbound_policy_does_not_fabricate_an_account(self):
        self.readings = [dict(self.readings[0], account=None, names=[])]
        self.groups = [self.setting("openai", 2)]
        code, text, _ = self.run_cli("--vendor", "openai")
        self.assertEqual(code, 0)
        (got,) = json.loads(text)
        self.assertEqual((got["account"], got["steering"]["account"]), (None, None))
        self.assert_sdk_row(got, self.readings[0])
        self.assertEqual(got["verdict"]["preference"]["multiplier"], 2)

    def test_metadata_filesystem_type_and_numeric_failures_are_advisory_without_offering(self):
        self.groups = [self.setting("openai", 2)]
        private = "/private/fixture/catalog.toml fixture-secret"
        for error in (OSError, ValueError, TypeError, OverflowError):
            with self.subTest(error=error), mock.patch.object(catalog, "load_metadata", side_effect=error(private)):
                code, text, err = self.run_cli("--vendor", "openai")
                self.assertEqual(code, 0)
                (got,) = json.loads(text)
                self.assertEqual(got["account"], "a")
                self.assertEqual(got["verdict"]["state"], "ranked")
                self.assertEqual(got["steering"]["error"], "catalog-unavailable")
                pref = got["verdict"]["preference"]
                self.assertEqual((pref["status"], pref["reason"], pref["multiplier"]),
                                 ("unavailable", "policy-unavailable", 1))
                self.assertEqual(pref["effective_score"], got["verdict"]["score"])
                self.assertNotIn(private, text + err)
                self.assertIn("catalog-unavailable", err)

    def test_evaluation_catalog_failures_use_shared_unavailable_result(self):
        self.groups = [self.setting("openai", 2)]
        annotated = incentives.annotate(self.readings, now=NOW)
        for error in (OSError, ValueError, TypeError, OverflowError):
            with self.subTest(error=error), \
                 mock.patch.object(incentives, "_annotate", return_value=(annotated, [])), \
                 mock.patch.object(catalog, "load_metadata", side_effect=error("fixture-secret")):
                code, text, err = self.run_cli("--vendor", "openai")
                self.assertEqual(code, 0)
                (got,) = json.loads(text)
                pref = got["verdict"]["preference"]
                self.assertEqual((pref["status"], pref["reason"], pref["multiplier"]),
                                 ("unavailable", "catalog-unavailable", 1))
                self.assertNotIn("fixture-secret", text + err)

    def test_zero_row_policy_failure_keeps_an_advisory_warning(self):
        self.readings = []
        with mock.patch.object(incentives, "read", side_effect=catalog.CatalogError("fixture-secret")):
            code, text, err = self.run_cli("--vendor", "openai")
        self.assertEqual((code, json.loads(text)), (0, []))
        self.assertIn("policy-unavailable", err)
        self.assertNotIn("fixture-secret", text + err)

    def test_zero_row_metadata_failure_keeps_an_advisory_warning(self):
        self.readings = []
        self.groups = [self.setting("openai", 2)]
        with mock.patch.object(catalog, "load_metadata", side_effect=OSError("fixture-secret")):
            code, text, err = self.run_cli("--vendor", "openai")
        self.assertEqual((code, json.loads(text)), (0, []))
        self.assertEqual(err.count("catalog-unavailable"), 1)
        self.assertNotIn("fixture-secret", text + err)

    def test_context_identity_conflict_does_not_leave_partial_json(self):
        for field, value in (("vendor", "neuralwatt"), ("account", "b")):
            annotated = incentives.annotate(self.readings * 2, now=NOW)
            annotated[1]["steering"][field] = value
            annotated[1]["steering"]["v"] = 999
            with self.subTest(field=field), \
                 mock.patch.object(incentives, "_annotate", return_value=(annotated, [])):
                code, text, err = self.run_cli("--vendor", "openai")
            self.assertEqual((code, text), (2, ""))
            self.assertIn("conflict", err)

    def test_later_cross_vendor_reading_does_not_leave_partial_json(self):
        wrong = reading(window("codex", .1, WEEK, 3000), vendor="neuralwatt")
        self.groups = [self.setting("route-a", 10)]
        self.read_cache.side_effect = None
        self.read_cache.return_value = [self.readings[0], wrong]
        code, text, err = self.run_cli("--offering", "route-a")
        self.assertEqual((code, text), (2, ""))
        self.assertIn("conflict", err)

    def test_multiple_accounts_keep_input_order_and_distinct_preferences(self):
        self.readings = [dict(self.readings[0], account="b", names=["beta"]),
                         dict(self.readings[0], names=["alpha"])]
        self.groups = [self.setting("openai", 10, account="a"), self.setting("openai", .1, account="b")]
        before = copy.deepcopy(self.readings)
        code, text, _ = self.run_cli("--vendor", "openai")
        self.assertEqual(code, 0)
        rows = json.loads(text)
        self.assertEqual([row["account"] for row in rows], ["b", "a"])
        self.assertEqual([row["verdict"]["preference"]["multiplier"] for row in rows], [.1, 10])
        for row, r in zip(rows, self.readings):
            self.assert_sdk_row(row, r)
            for key in ("names", "bindings", "credentials"):
                self.assertNotIn(key, row["steering"])
                self.assertNotIn(key, row["verdict"]["preference"])
        self.assertEqual(self.readings, before)

    def test_sibling_offering_policy_does_not_borrow_vendor_binding(self):
        self.readings = [reading(window("codex", .1, WEEK, 3000), vendor="neuralwatt")]
        self.groups = [self.setting("route-a", 10), self.setting("route-b", .1)]
        code, text, _ = self.run_cli("--offering", "route-b")
        self.assertEqual(code, 0)
        (got,) = json.loads(text)
        self.assert_sdk_row(got, self.readings[0], offering="route-b")
        self.assertEqual(got["verdict"]["preference"]["multiplier"], .1)

    def test_omitted_vendors_without_offering_still_reads_all_vendors(self):
        self.readings.append(reading(window("codex", .1, WEEK, 3000), vendor="neuralwatt"))
        code, text, _ = self.run_cli()
        self.assertEqual(code, 0)
        rows = json.loads(text)
        self.assertEqual([row["vendor"] for row in rows], ["neuralwatt", "openai"])
        self.assertEqual([c.args[0].VENDOR for c in self.read_cache.call_args_list], ["neuralwatt", "openai"])
        self.assertTrue(all(row["verdict"]["preference"]["multiplier"] == 1 for row in rows))


    def test_process_json_matches_sdk_with_real_local_policy_loading(self):
        self.groups = [self.setting("openai", 2), self.setting("route-a", 10, account="a")]
        incentives.write(self.groups)  # isolated XDG_CONFIG_HOME from setUp
        source = str(Path(cli.__file__).resolve().parents[1])
        program = textwrap.dedent(f'''\
    import json, sys
    from datetime import datetime
    from unittest import mock
    from unlimited import catalog, cli
    cat = catalog.Catalog(json.loads({json.dumps(self.cat_data)!r}))
    readings = json.loads({json.dumps(self.readings)!r})
    now = datetime.fromisoformat({NOW.isoformat()!r})
    with mock.patch.object(catalog, "load_metadata", return_value=cat), \\
         mock.patch.object(cli.cache, "through", return_value=readings), \\
         mock.patch.object(cli, "datetime") as clock:
        clock.now.return_value = now
        raise SystemExit(cli.main(sys.argv[1:]))
    ''')
        env = dict(os.environ, PYTHONPATH=source)
        argv = ["verdict", "--offering", "route-a", "--work", "600", "--json"]
        proc = subprocess.run([sys.executable, "-c", program, *argv], env=env,
                              capture_output=True, text=True, timeout=10, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        (got,) = json.loads(proc.stdout, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        self.assert_sdk_row(got, self.readings[0], offering="route-a")
        self.assertEqual(got["verdict"]["preference"]["multiplier"], 10)
        rejected = subprocess.run(
            [sys.executable, "-c", program, *argv, "--vendor", "neuralwatt"], env=env,
            capture_output=True, text=True, timeout=10, check=False)
        self.assertEqual((rejected.returncode, rejected.stdout), (2, ""))


if __name__ == "__main__":
    unittest.main()
