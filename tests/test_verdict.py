"""The account verdict (docs/choice.md): the ranking and exclusion rules, the model scope, the
vendor stop and the monthly bucket."""

from __future__ import annotations

import copy
import json
import math
import sys
import unittest
from datetime import datetime, timedelta, timezone

from unlimited import catalog, choice, incentives
from unlimited.verdict import verdict

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


if __name__ == "__main__":
    unittest.main()
