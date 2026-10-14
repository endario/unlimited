"""Rolling gateway-log observations remain durable and explicitly qualified."""

from __future__ import annotations

import json
import multiprocessing
import os
import stat
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from unlimited import outcomes, usage
from unlimited.schema import iso, reading

import scratch


NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)


def event(identity: str, at: datetime, input_tokens: int = 2851, output_tokens: int = 85,
          cache_read_tokens: int = 1088, cache_creation_tokens: int = 0, model: str | None = None) -> dict:
    return {"id": identity, "at": iso(at), "input_tokens": input_tokens, "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens, "cache_creation_tokens": cache_creation_tokens,
            **({"model": model} if model is not None else {})}


def import_in_process(state_home: str, identity: str) -> None:
    os.environ["XDG_STATE_HOME"] = state_home
    now = NOW
    r = dict(reading("woyo", "fixture", now, "ok"), _usage_snapshot={
        "events": [event(identity, now - HOUR, 10, 2, 0, 0)], "anchors": [identity], "empty": False,
        "unparsed": [],
    })
    usage.attach(r, now)


class Usage(unittest.TestCase):
    def setUp(self):
        self.root = Path(scratch.mkdtemp())
        self.p = self.root / "usage.json"

    def attach(self, snapshot: dict | None = None, *, now: datetime = NOW, status: str = "ok") -> dict:
        r = reading("woyo", "fixture", now, status)
        if snapshot is not None:
            r["_usage_snapshot"] = snapshot
        with mock.patch.object(usage, "path", return_value=self.p):
            return usage.attach(r, now)

    def snapshot(self, events: list[dict], anchors: list[str], *, empty: bool = False,
                 unparsed: list[dict] | None = None) -> dict:
        return {"events": events, "anchors": anchors, "empty": empty, "unparsed": unparsed or []}

    def test_snapshot_is_imported_once_and_exposes_literal_token_totals(self):
        snapshot = self.snapshot([event("hashed-a", NOW - HOUR)], ["hashed-a"])
        reading_with_snapshot = dict(reading("woyo", "fixture", NOW, "ok"), _usage_snapshot=snapshot)

        with mock.patch.object(usage, "path", return_value=self.p):
            first = usage.attach(reading_with_snapshot, NOW)
            second = usage.attach(reading_with_snapshot, NOW)

        self.assertEqual([p["total_tokens"] for p in second["usage"]["periods"]], [2936])
        self.assertNotIn("_usage_snapshot", first)
        self.assertEqual(first["usage"]["periods"][0]["coverage"], "partial")

    def test_uncaptured_usage_is_unknown_not_zero(self):
        got = self.attach()

        self.assertIsNone(got["usage"]["collected_at"])
        self.assertEqual(
            [(p["total_tokens"], p["coverage"], p["reasons"]) for p in got["usage"]["periods"]],
            [(None, "unknown", [])],
        )

    def test_valid_empty_sample_records_zero_logged_totals_with_partial_history(self):
        got = self.attach(self.snapshot([], [], empty=True))

        self.assertEqual(
            [(p["input_tokens"], p["output_tokens"], p["total_tokens"], p["coverage"], p["reasons"])
             for p in got["usage"]["periods"]],
            [(0, 0, 0, "partial", ["horizon-edge"])],
        )

    def test_first_observation_wins_when_later_snapshot_reuses_its_identity(self):
        self.attach(self.snapshot([event("same", NOW - HOUR, 17, 3, 4, 5)], ["same"]))
        got = self.attach(self.snapshot([event("same", NOW - HOUR, 900, 800, 700, 600)], ["same"]))

        self.assertEqual(
            [(p["input_tokens"], p["output_tokens"], p["total_tokens"], p["cache_read_tokens"],
              p["cache_creation_tokens"]) for p in got["usage"]["periods"]],
            [(17, 3, 20, 4, 5)],
        )

    def test_matching_reobservation_backfills_a_legacy_event_model_without_changing_totals(self):
        # Model identity can be recovered later, but the first measured token fact remains authoritative.
        self.attach(self.snapshot([event("same", NOW - HOUR, 17, 3, 4, 5)], ["same"]))
        got = self.attach(self.snapshot([event("same", NOW - HOUR, 17, 3, 4, 5, "glm-5.3")], ["same"]))

        stored = json.loads(self.p.read_text())["events"]["same"]
        self.assertEqual(stored["model"], "glm-5.3")
        self.assertEqual(got["usage"]["periods"][0]["total_tokens"], 20)

    def test_window_includes_its_lower_boundary_and_excludes_as_of(self):
        seven_days_ago = NOW - timedelta(days=7)
        got = self.attach(self.snapshot([
            event("lower", seven_days_ago, 11, 2, 0, 0),
            event("upper", NOW, 100, 50, 0, 0),
        ], ["lower", "upper"]))

        self.assertEqual(
            [(p["input_tokens"], p["output_tokens"], p["total_tokens"]) for p in got["usage"]["periods"]],
            [(11, 2, 13)],
        )

    def test_overlapping_page_anchors_bridge_monitoring_between_successes(self):
        earlier = NOW - timedelta(days=7)
        self.attach(self.snapshot([event("retained", earlier, 9, 1, 0, 0)], ["retained"]), now=earlier)
        got = self.attach(self.snapshot([event("retained", earlier, 9, 1, 0, 0)], ["retained"]), now=NOW)

        self.assertEqual([(p["coverage"], p["reasons"]) for p in got["usage"]["periods"]],
                         [("observed", [])])

    def test_failed_poll_does_not_break_a_later_overlap_bridge(self):
        earlier = NOW - timedelta(days=7)
        self.attach(self.snapshot([event("retained", earlier, 9, 1, 0, 0)], ["retained"]), now=earlier)
        self.attach(now=NOW - timedelta(days=3), status="unread")
        got = self.attach(self.snapshot([event("retained", earlier, 9, 1, 0, 0)], ["retained"]), now=NOW)

        self.assertEqual(got["usage"]["periods"][0]["coverage"], "observed")

    def test_disjoint_pages_leave_a_tail_gap(self):
        earlier = NOW - DAY
        self.attach(self.snapshot([event("old", earlier - HOUR, 5, 1, 0, 0)], ["old"]), now=earlier)
        got = self.attach(self.snapshot([event("new", NOW - HOUR, 7, 2, 0, 0)], ["new"]), now=NOW)

        self.assertIn("tail-gap", got["usage"]["periods"][0]["reasons"])
        self.assertEqual(got["usage"]["periods"][0]["coverage"], "partial")

    def test_two_empty_samples_bridge_monitoring(self):
        earlier = NOW - timedelta(days=7)
        self.attach(self.snapshot([], [], empty=True), now=earlier)
        got = self.attach(self.snapshot([], [], empty=True), now=NOW)

        self.assertEqual(got["usage"]["periods"][0]["coverage"], "observed")
        self.assertEqual(got["usage"]["periods"][0]["total_tokens"], 0)

    def test_cache_only_read_expires_events_without_extending_coverage(self):
        self.attach(self.snapshot([event("old", NOW - HOUR, 12, 4, 0, 0)], ["old"]))
        later = NOW + timedelta(days=31)
        got = self.attach(now=later)

        self.assertEqual(
            [(p["total_tokens"], p["coverage"], p["reasons"]) for p in got["usage"]["periods"]],
            [(0, "partial", ["tail-gap"])],
        )
        self.assertEqual(json.loads(self.p.read_text())["events"], {})

    def test_unparsed_interval_makes_intersecting_period_partial(self):
        interval = {"starts_at": iso(NOW - HOUR), "ends_at": iso(NOW - timedelta(minutes=30))}
        got = self.attach(self.snapshot([event("one", NOW - HOUR, 4, 2, 0, 0)], ["one"], unparsed=[interval]))

        self.assertIn("unparsed", got["usage"]["periods"][0]["reasons"])
        self.assertEqual(got["usage"]["periods"][0]["coverage"], "partial")

    def test_malformed_event_is_uncertain_not_imputed_as_zero(self):
        malformed = {"id": "bad", "at": iso(NOW - HOUR), "input_tokens": "broken"}
        got = self.attach(self.snapshot([malformed], ["bad"]))

        self.assertEqual(got["usage"]["periods"][0]["total_tokens"], 0)
        self.assertIn("unparsed", got["usage"]["periods"][0]["reasons"])

    def test_corrupt_state_stays_untouched_and_reports_unknown_usage(self):
        original = "not json\n"
        self.p.write_text(original)
        got = self.attach(self.snapshot([event("new", NOW - HOUR)], ["new"]))

        self.assertEqual(self.p.read_text(), original)
        self.assertEqual(got["usage"]["diagnostic"], "state-unreadable")
        self.assertIsNone(got["usage"]["periods"][0]["total_tokens"])

    def test_future_state_version_stays_untouched_and_reports_unknown_usage(self):
        original = json.dumps({"version": 2})
        self.p.write_text(original)
        got = self.attach()

        self.assertEqual(self.p.read_text(), original)
        self.assertEqual(got["usage"]["diagnostic"], "state-unreadable")
        self.assertIsNone(got["usage"]["periods"][0]["total_tokens"])

    def test_backward_clock_capture_resets_monitoring_origin_without_losing_events(self):
        future = NOW + timedelta(minutes=10)
        self.attach(self.snapshot([event("retained", NOW - HOUR, 4, 2, 0, 0)], ["retained"], empty=False),
                    now=future)
        corrected = self.attach(self.snapshot([], [], empty=True), now=NOW)
        cached = self.attach(now=NOW + timedelta(minutes=1))

        self.assertEqual([period["total_tokens"] for period in corrected["usage"]["periods"]], [6])
        self.assertEqual(corrected["usage"]["periods"][0]["coverage"], "partial")
        self.assertNotIn("diagnostic", cached["usage"])
        self.assertEqual([period["total_tokens"] for period in cached["usage"]["periods"]], [6])
        self.assertEqual(cached["usage"]["periods"][0]["coverage"], "partial")

    def test_collected_at_without_an_observation_origin_is_damaged_state(self):
        self.p.write_text(json.dumps({
            "version": 1, "events": {}, "anchors": [], "empty": False, "monitored": [], "unparsed": [],
            "collected_at": iso(NOW), "first_collected_at": None,
        }))

        got = self.attach()

        self.assertEqual(got["usage"]["diagnostic"], "state-unreadable")
        self.assertIsNone(got["usage"]["periods"][0]["total_tokens"])

    def test_overlap_at_the_end_of_a_large_page_bridges_monitoring(self):
        start = NOW - timedelta(days=8)
        anchors = [f"hash-{index}" for index in range(1500)]
        self.attach(self.snapshot([], anchors), now=start)

        got = self.attach(self.snapshot([], ["hash-1499"]), now=NOW)

        self.assertEqual((got["usage"]["periods"][0]["coverage"], got["usage"]["periods"][0]["reasons"]),
                         ("observed", []))

    def test_boolean_state_version_is_not_a_supported_version_number(self):
        self.p.write_text(json.dumps({
            "version": True, "events": {}, "anchors": [], "empty": False, "monitored": [], "unparsed": [],
            "collected_at": None, "first_collected_at": None,
        }))

        got = self.attach()

        self.assertEqual(got["usage"]["diagnostic"], "state-unreadable")

    def test_event_map_key_must_match_the_event_identity(self):
        self.p.write_text(json.dumps({
            "version": 1, "events": {"map-key": event("event-id", NOW - HOUR, 4, 2, 0, 0)},
            "anchors": [], "empty": False, "monitored": [], "unparsed": [],
            "collected_at": iso(NOW), "first_collected_at": iso(NOW),
        }))

        got = self.attach()

        self.assertEqual(got["usage"]["diagnostic"], "state-unreadable")

    def test_utc_conversion_overflow_is_damaged_state_and_leaves_the_file_intact(self):
        original = json.dumps({
            "version": 1, "events": {}, "anchors": [], "empty": False, "monitored": [], "unparsed": [],
            "collected_at": "0001-01-01T00:00:00+01:00",
            "first_collected_at": "0001-01-01T00:00:00+01:00",
        })
        self.p.write_text(original)

        got = self.attach()

        self.assertEqual(self.p.read_text(), original)
        self.assertEqual(got["usage"]["diagnostic"], "state-unreadable")
        self.assertIsNone(got["usage"]["periods"][0]["total_tokens"])

    def test_monitored_interval_beyond_latest_capture_is_damaged_state(self):
        original = json.dumps({
            "version": 1, "events": {}, "anchors": [], "empty": False,
            "monitored": [{"starts_at": iso(NOW - timedelta(days=8)), "ends_at": iso(NOW + timedelta(hours=2))}],
            "unparsed": [], "collected_at": iso(NOW), "first_collected_at": iso(NOW - timedelta(days=8)),
        })
        self.p.write_text(original)

        got = self.attach(now=NOW + HOUR)

        self.assertEqual(self.p.read_text(), original)
        self.assertEqual(got["usage"]["diagnostic"], "state-unreadable")
        self.assertIsNone(got["usage"]["periods"][0]["total_tokens"])

    def test_store_and_lock_are_private_even_under_a_public_umask(self):
        previous = os.umask(0o022)
        try:
            self.attach(self.snapshot([event("one", NOW - HOUR)], ["one"]))
        finally:
            os.umask(previous)

        self.assertEqual(stat.S_IMODE(self.p.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.p.with_name("usage.lock").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.p.parent.stat().st_mode), 0o700)

    def test_disjoint_concurrent_imports_are_both_retained(self):
        state_home = str(self.root / "state")
        processes = [multiprocessing.Process(target=import_in_process, args=(state_home, identity))
                     for identity in ("one", "two")]
        for process in processes:
            process.start()
        for process in processes:
            process.join(10)
            self.assertEqual(process.exitcode, 0)

        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": state_home}):
            got = usage.attach(reading("woyo", "fixture", NOW, "ok"), NOW)
        self.assertEqual([(p["input_tokens"], p["output_tokens"], p["total_tokens"])
                          for p in got["usage"]["periods"]], [(20, 4, 24)])


class StateDirectory(unittest.TestCase):
    def test_outcomes_keeps_its_existing_default_location(self):
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": "/tmp/unlimited-state"}):
            self.assertEqual(outcomes.path(), Path("/tmp/unlimited-state/unlimited/decisions.jsonl"))


if __name__ == "__main__":
    unittest.main()
