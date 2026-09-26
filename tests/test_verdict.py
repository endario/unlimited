"""The account verdict (docs/choice.md): the ranking and exclusion rules, the model scope, the
vendor stop and the monthly bucket."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

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
