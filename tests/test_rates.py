"""Time-of-day rates: the catalog declares them, a reading carries the spans they resolve to."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from unlimited import catalog, incentives, rates

import scratch

SGT = timezone(timedelta(hours=8))
THU_1300 = datetime(2026, 10, 15, 13, 0, tzinfo=SGT)
PEAK = {"vendor": "zai", "meter": "CREDIT_LIMIT", "days": ["mon", "tue", "wed", "thu", "fri"],
        "from": "14:00", "to": "18:00", "tz": "+08:00", "factor": 2}


def reading(kind: str = "CREDIT_LIMIT", vendor: str = "zai") -> dict:
    return {"vendor": vendor, "account": "a1", "limits": [{"name": "five_hour", "kind": kind}]}


class Spans(unittest.TestCase):
    def test_weekday_hours_resolve_to_utc_spans_and_skip_the_weekend(self):
        got = rates.spans([PEAK], reading(), THU_1300)
        self.assertEqual(got[0], {"offerings": None, "from": "2026-10-15T06:00:00+00:00",
                                  "to": "2026-10-15T10:00:00+00:00", "factor": 2})
        days = [datetime.fromisoformat(s["from"]).astimezone(SGT).strftime("%a") for s in got]
        # Thu, Fri, then Mon to Thu: the weekend is cheap, and next Friday's peak is past the horizon.
        self.assertEqual(days, ["Thu", "Fri", "Mon", "Tue", "Wed", "Thu"])

    def test_a_span_already_open_starts_now(self):
        now = THU_1300 + timedelta(hours=2)
        got = rates.spans([PEAK], reading(), now)[0]
        self.assertEqual(datetime.fromisoformat(got["from"]), now)
        self.assertEqual(got["to"], "2026-10-15T10:00:00+00:00")

    def test_a_rate_binds_only_its_vendor_and_meter(self):
        self.assertEqual(rates.spans([PEAK], reading(kind="TOKENS_LIMIT"), THU_1300), [],
                         "a legacy token plan is not charged the credits plan's rate")
        self.assertEqual(rates.spans([PEAK], reading(vendor="anthropic"), THU_1300), [])
        unmetered = {k: v for k, v in PEAK.items() if k != "meter"}
        self.assertTrue(rates.spans([unmetered], reading(kind="TOKENS_LIMIT"), THU_1300))

    def test_a_window_to_midnight_and_every_day_by_default(self):
        night = {"vendor": "x", "from": "08:00", "to": "24:00", "tz": "+08:00", "factor": 1.25,
                 "offerings": ["x/model"]}
        got = rates.spans([night], reading(vendor="x"), THU_1300)
        self.assertEqual(got[0]["from"], THU_1300.astimezone(timezone.utc).isoformat())
        self.assertEqual(got[0]["to"], "2026-10-15T16:00:00+00:00")
        self.assertEqual(got[0]["offerings"], ["x/model"])
        self.assertEqual(len(got), 9, "today's remainder, seven whole days, and the horizon's last part")


class Catalog(unittest.TestCase):
    def test_the_shipped_rates_load_and_validate(self):
        cat = catalog.load(Path(scratch.mkdtemp()) / "catalog.toml")
        factors = {r["meter"]: r["factor"] for r in cat.rates if r["vendor"] == "zai"}
        self.assertEqual(factors, {"CREDIT_LIMIT": 2, "TOKENS_LIMIT": 3})

    def test_the_shipped_deepseek_rates_bind_its_peak_windows(self):
        cat = catalog.load(Path(scratch.mkdtemp()) / "catalog.toml")
        windows = [("01:00", "04:00", 2), ("06:00", "10:00", 2)]
        direct = [r for r in cat.rates if r["vendor"] == "deepseek"]
        self.assertEqual([(r["from"], r["to"], r["factor"]) for r in direct], windows)
        got = rates.spans(direct, {"vendor": "deepseek", "limits": []},
                          datetime(2026, 10, 15, 2, tzinfo=timezone.utc))
        self.assertEqual(got[0], {"offerings": None, "from": "2026-10-15T02:00:00+00:00",
                                  "to": "2026-10-15T04:00:00+00:00", "factor": 2})
        self.assertTrue({datetime.fromisoformat(s["from"]).strftime("%a") for s in got}
                        <= {"Mon", "Tue", "Wed", "Thu", "Fri"}, got)
        for route in ("opencode-go/deepseek-v4.1-flash", "commandcode/deepseek/deepseek-v4.1-flash"):
            rows = [r for r in cat.rates if route in r.get("offerings", [])]
            self.assertEqual([(r["from"], r["to"], r["factor"]) for r in rows], windows)

    def test_a_malformed_rate_is_refused_with_why(self):
        for bad, why in [({**PEAK, "from": "18:00", "to": "14:00"}, "from is before to"),
                         ({**PEAK, "tz": "Asia/Singapore"}, "fixed offset"),
                         ({**PEAK, "tz": "+24:00"}, "fixed offset"),
                         ({**PEAK, "tz": "+08:60"}, "fixed offset"),
                         ({**PEAK, "days": ["thursday"]}, "days"),
                         ({**PEAK, "factor": 0}, "positive")]:
            self.assertIn(why, rates.problem(bad))

    def test_a_local_rates_list_that_is_not_valid_leaves_the_shipped_one(self):
        local = Path(scratch.mkdtemp()) / "catalog.toml"
        local.write_text('schema = 2\n[[rates]]\nvendor = "zai"\n')
        self.assertEqual(catalog.load_rates(local), catalog.load_rates(local.with_name("absent.toml")))
        local.write_text("schema = 2\nrates = []\n")
        self.assertEqual(catalog.load_rates(local), [])

    def test_a_local_list_replaces_the_shipped_one(self):
        local = Path(scratch.mkdtemp()) / "catalog.toml"
        local.write_text("schema = 2\nrates = []\n")
        self.assertEqual(catalog.load(local).rates, [])


class Annotate(unittest.TestCase):
    def test_rates_ride_the_context_even_with_no_incentive_set(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": scratch.mkdtemp()}), \
                mock.patch.object(catalog, "load_rates", return_value=[PEAK]):
            out, errors = incentives._annotate([reading()], now=THU_1300)
        self.assertEqual(errors, [])
        self.assertEqual(out[0]["steering"]["rates"][0]["factor"], 2)
        self.assertEqual(out[0]["steering"]["routes"], [])

    def test_a_catalog_that_cannot_be_read_never_stops_tracking(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": scratch.mkdtemp()}), \
                mock.patch.object(catalog.resources, "files", side_effect=FileNotFoundError("catalog.toml")):
            out, errors = incentives._annotate([reading()], now=THU_1300)
        self.assertEqual(errors, [])
        self.assertEqual(out[0]["limits"], reading()["limits"])
        self.assertNotIn("rates", out[0]["steering"])

    def test_a_reading_no_rate_binds_carries_no_rates(self):
        cat = catalog.load(Path(scratch.mkdtemp()) / "catalog.toml")
        cat.rates = [PEAK]
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": scratch.mkdtemp()}):
            out, _ = incentives._annotate([reading(vendor="anthropic")], now=THU_1300, cat=cat)
        self.assertNotIn("rates", out[0]["steering"])


if __name__ == "__main__":
    unittest.main()
