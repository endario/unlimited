"""Woyo gateway-log source and its cache integration, with no live credentials or HTTP."""

from __future__ import annotations

import json
import os
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from unlimited import cache
from unlimited.credential import Credential, account_of
from unlimited.transport import Answer

import scratch

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
REPLAY = Path(__file__).parents[1] / "docs" / "woyo-usage" / "log-replay.json"


class WoyoSource(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(scratch.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.environ = mock.patch.dict(os.environ, {"HOME": str(self.home), "XDG_STATE_HOME": str(self.tmp / "state"),
                                                    "XDG_CACHE_HOME": str(self.tmp / "cache-home"), "FY_API_KEY": ""}, clear=False)
        self.environ.start()
        self.addCleanup(self.environ.stop)

    def test_read_extracts_allowlisted_consumption_snapshot_without_state(self):
        # Removing `type == 2` or cache-token inclusion would lose this measured token fact.
        from unlimited.adapters import woyo

        replay = json.loads(REPLAY.read_text())
        replay["data"].reverse()

        def get(url, headers, now):
            self.assertEqual(url, woyo.URL)
            self.assertEqual(headers, {"Authorization": "Bearer fixture-key"})
            return Answer(replay, 200, None)

        r = woyo.read(Credential("fixture", {"key": "fixture-key"}), NOW, get)

        self.assertEqual((r["status"], r["limits"]), ("ok", []))
        snapshot = r["_usage_snapshot"]
        self.assertEqual(sorted(e["input_tokens"] for e in snapshot["events"]), [23, 2851])
        self.assertEqual(sorted(e["output_tokens"] for e in snapshot["events"]), [8, 85])
        self.assertEqual(len(snapshot["anchors"]), 2)
        self.assertNotIn("sample-anthropic", json.dumps(snapshot))
        self.assertNotIn("other", json.dumps(snapshot))

    def test_discovery_uses_the_credential_variable_across_matching_env_files(self):
        # Changing the allowed key variable or file glob would miss a caller-independent credential.
        from unlimited.adapters import woyo

        config = self.home / ".config"
        config.mkdir()
        (config / "gateway.env").write_text("FY_API_KEY=second-key\n")
        (config / "same.env").write_text("FY_API_KEY=first-key\n")
        (config / "ignored.env").write_text("OTHER_KEY=not-a-credential\n")
        with mock.patch.dict(os.environ, {"FY_API_KEY": "first-key"}):
            got = woyo.discover()
        self.assertEqual({c.secret["key"] for c in got}, {"first-key", "second-key"})
        self.assertEqual(woyo.names()[account_of("second-key")], ["gateway"])

    def test_registry_exposes_woyo(self):
        # Omitting the registry entry makes an otherwise-valid source unreachable from the CLI.
        from unlimited.adapters import REGISTRY, woyo

        self.assertIs(REGISTRY["woyo"], woyo)

    def test_malformed_discriminators_are_uncertainty_not_non_consumption_anchors(self):
        # Treating a malformed type as an error anchor permits clean coverage while its tokens disappear.
        from unlimited.adapters import woyo

        for kind in (None, "2", {}):
            with self.subTest(kind=kind):
                row = {"request_id": "invalid-type", "created_at": 1791892800, "type": kind,
                       "prompt_tokens": 200, "completion_tokens": 10, "other": "{}"}
                r = woyo.read(Credential("fixture", {"key": "fixture-key"}), NOW,
                              lambda *args: Answer({"success": True, "data": [row]}, 200, None))
                snapshot = r["_usage_snapshot"]
                self.assertEqual((snapshot["events"], snapshot["anchors"]), ([], []))
                self.assertEqual(snapshot["unparsed"], [{"starts_at": "2026-10-13T12:00:00+00:00",
                                                          "ends_at": "2026-10-13T12:00:01+00:00"}])

    def test_overlapping_malformed_type_pages_retain_unparsed_coverage(self):
        # A repeated malformed page must not turn unknown token use into a clean observed zero.
        from unlimited.adapters import woyo

        cred = Credential("fixture", {"key": "fixture-key"})
        row = {"request_id": "invalid-type", "created_at": int(NOW.timestamp()) - 60,
               "prompt_tokens": 200, "completion_tokens": 10, "other": "{}"}
        directory = self.tmp / "invalid-type-cache"
        with mock.patch.object(woyo, "discover", lambda: [cred]):
            for minute in (0, 1):
                cache.through(woyo, max_age=0, clock=lambda minute=minute: NOW.replace(minute=minute),
                              get=lambda *args: Answer({"success": True, "data": [row]}, 200, None),
                              directory=directory)
        state = next(self.tmp.glob("state/unlimited/usage/*.json"))
        reading = json.loads((directory / "woyo.json").read_text())["readings"][0]
        self.assertEqual(reading["usage"]["periods"][0]["total_tokens"], 0)
        self.assertIn("unparsed", reading["usage"]["periods"][0]["reasons"])
        self.assertTrue(state.exists())

    def test_unencodable_identity_and_max_timestamp_malformed_rows_preserve_valid_siblings(self):
        # A malformed row must not abort the whole public read or discard independently valid consumption.
        from unlimited.adapters import woyo

        rows = [
            {"request_id": "valid", "created_at": 1791892800, "type": 2, "prompt_tokens": 200,
             "completion_tokens": 10, "other": "{}"},
            {"request_id": "\ud800", "created_at": 1791892801, "type": 2, "prompt_tokens": 1,
             "completion_tokens": 2, "other": "{}"},
            {"request_id": "max-time", "created_at": 253402300799, "type": 2, "prompt_tokens": "bad",
             "completion_tokens": 2, "other": "{}"},
        ]
        r = woyo.read(Credential("fixture", {"key": "fixture-key"}), NOW,
                      lambda *args: Answer({"success": True, "data": rows}, 200, None))
        snapshot = r["_usage_snapshot"]
        self.assertEqual([(event["input_tokens"], event["output_tokens"]) for event in snapshot["events"]], [(200, 10)])
        self.assertEqual(len(snapshot["unparsed"]), 2)
        self.assertEqual(snapshot["unparsed"][1], {})

    def test_consumption_only_pages_overlap_on_their_hashed_request_identity(self):
        # Omitting consumption IDs from anchors leaves every successful page separated by a tail gap.
        from unlimited.adapters import woyo

        cred = Credential("fixture", {"key": "fixture-key"})
        row = {"type": 2, "request_id": "synthetic-consumption", "created_at": int(NOW.timestamp()) - 60,
               "prompt_tokens": 200, "completion_tokens": 10, "other": '{"cache_tokens":80}'}
        directory = self.tmp / "consumption-cache"
        with mock.patch.object(woyo, "discover", lambda: [cred]):
            cache.through(woyo, max_age=0, clock=lambda: NOW,
                          get=lambda *args: Answer({"success": True, "data": [row]}, 200, None), directory=directory)
            [later] = cache.through(woyo, max_age=0, clock=lambda: NOW.replace(minute=1),
                                   get=lambda *args: Answer({"success": True, "data": [row]}, 200, None), directory=directory)
        seven_days = later["usage"]["periods"][0]
        self.assertEqual(seven_days["reasons"], ["horizon-edge"])
        self.assertEqual((seven_days["input_tokens"], seven_days["output_tokens"], seven_days["cache_read_tokens"]),
                         (200, 10, 80))

    def test_openai_cache_tokens_are_recorded_without_double_counting_input(self):
        # Zeroing this subset loses observed cache usage; adding it makes input tokens too large.
        from unlimited.adapters import woyo

        row = {"request_id": "openai-cache", "created_at": 1791892800, "type": 2, "prompt_tokens": 23,
               "completion_tokens": 8, "other": '{"cache_tokens":7,"cache_creation_tokens":3}'}
        r = woyo.read(Credential("fixture", {"key": "fixture-key"}), NOW,
                      lambda url, headers, now: Answer({"success": True, "data": [row]}, 200, None))
        (event,) = r["_usage_snapshot"]["events"]
        self.assertEqual((event["input_tokens"], event["output_tokens"], event["cache_read_tokens"],
                          event["cache_creation_tokens"]), (23, 8, 7, 3))

    def test_unsuccessful_log_body_is_not_an_empty_observation(self):
        # Treating an explicit failed response as empty would create false coverage and zero use.
        from unlimited.adapters import woyo

        r = woyo.read(Credential("fixture", {"key": "fixture-key"}), NOW,
                      lambda url, headers, now: Answer({"success": False, "data": []}, 200, None))
        self.assertEqual((r["status"], r["why"]), ("unread", "no-log-data"))
        self.assertNotIn("_usage_snapshot", r)

    def test_a_malformed_consumption_sibling_still_makes_the_identity_ambiguous(self):
        # Counting the valid sibling would pick an arbitrary fact for a duplicated consumption identity.
        from unlimited.adapters import woyo

        rows = [
            {"request_id": "same", "created_at": 1791892800, "type": 2, "prompt_tokens": 1,
             "completion_tokens": 2, "other": "{}"},
            {"request_id": "same", "created_at": 1791892801, "type": 2, "prompt_tokens": 3,
             "completion_tokens": "bad", "other": "{}"},
        ]
        r = woyo.read(Credential("fixture", {"key": "fixture-key"}), NOW,
                      lambda url, headers, now: Answer({"success": True, "data": rows}, 200, None))
        self.assertEqual(r["_usage_snapshot"]["events"], [])
        self.assertEqual(len(r["_usage_snapshot"]["unparsed"]), 2)

    def test_malformed_and_ambiguous_consumption_become_uncertainty_not_zero_events(self):
        # Accepting a duplicated ID or malformed cached count would fabricate a durable token fact.
        from unlimited.adapters import woyo

        rows = [
            {"request_id": "duplicate", "created_at": 1791892800, "type": 2, "prompt_tokens": 1,
             "completion_tokens": 2, "other": "{}"},
            {"request_id": "duplicate", "created_at": 1791892801, "type": 2, "prompt_tokens": 3,
             "completion_tokens": 4, "other": "{}"},
            {"request_id": "bad-cache", "created_at": 1791892802, "type": 2, "prompt_tokens": 5,
             "completion_tokens": 6, "other": '{"usage_semantic":"anthropic","cache_tokens":"seven"}'},
        ]
        r = woyo.read(Credential("fixture", {"key": "fixture-key"}), NOW,
                      lambda url, headers, now: Answer({"success": True, "data": rows}, 200, None))
        snapshot = r["_usage_snapshot"]
        self.assertEqual(snapshot["events"], [])
        self.assertEqual(snapshot["unparsed"], [
            {"starts_at": "2026-10-13T12:00:02+00:00", "ends_at": "2026-10-13T12:00:03+00:00"},
            {"starts_at": "2026-10-13T12:00:00+00:00", "ends_at": "2026-10-13T12:00:01+00:00"},
            {"starts_at": "2026-10-13T12:00:01+00:00", "ends_at": "2026-10-13T12:00:02+00:00"},
        ])

    def test_cache_retains_measured_totals_on_restart_hit_and_auth_refusal(self):
        # Losing the durable facts on cache lifecycle or a refusal would turn observed use into unknown.
        from unlimited.adapters import woyo

        replay = json.loads(REPLAY.read_text())
        replay["data"][0]["other"] = '{"usage_semantic":"anthropic","cache_tokens":1088,"private-marker":"private-body"}'
        cred = Credential("fixture", {"key": "fixture-key"})
        calls = []

        def get(url, headers, now):
            calls.append(now)
            return Answer(replay, 200, None)

        directory = self.tmp / "cache"
        with mock.patch.object(woyo, "discover", lambda: [cred]):
            [first] = cache.through(woyo, max_age=300, clock=lambda: NOW, get=get, directory=directory)
            self.assertEqual(first["usage"]["periods"][0]["total_tokens"], 2967)
            (directory / "woyo.json").unlink()
            [restarted] = cache.through(woyo, max_age=300, clock=lambda: NOW.replace(minute=1), get=get, directory=directory)
            [hit] = cache.through(woyo, max_age=300, clock=lambda: NOW.replace(minute=2),
                                  get=lambda *args: self.fail("fresh cache must not fetch"), directory=directory)
            [refused] = cache.through(woyo, max_age=0, clock=lambda: NOW.replace(minute=3),
                                      get=lambda *args: Answer(None, 401, "http-401"), directory=directory)

        self.assertEqual(len(calls), 2)
        self.assertEqual(restarted["usage"]["periods"][0]["total_tokens"], 2967)
        self.assertEqual(hit["usage"]["collected_at"], restarted["usage"]["collected_at"])
        self.assertEqual((refused["status"], refused["usage"]["periods"][0]["total_tokens"]), ("refused", 2967))
        dumped = "".join(p.read_text() for p in [directory / "woyo.json", *self.tmp.glob("state/unlimited/usage/*.json")])
        self.assertNotIn("fixture-key", dumped)
        self.assertNotIn("private-body", dumped)
        self.assertNotIn("_usage_snapshot", dumped)

    def test_transient_and_throttled_reads_keep_measured_totals(self):
        # A transport failure or backoff must retain observed facts rather than manufacture a fresh sample.
        from unlimited.adapters import woyo

        replay = json.loads(REPLAY.read_text())
        cred = Credential("fixture", {"key": "fixture-key"})
        directory = self.tmp / "failure-cache"
        with mock.patch.object(woyo, "discover", lambda: [cred]):
            cache.through(woyo, max_age=0, clock=lambda: NOW,
                          get=lambda *args: Answer(replay, 200, None), directory=directory)
            [transient] = cache.through(woyo, max_age=0, clock=lambda: NOW.replace(minute=1),
                                        get=lambda *args: Answer(None, None, "unreachable"), directory=directory)
            [throttled] = cache.through(woyo, max_age=0, clock=lambda: NOW.replace(minute=2),
                                        get=lambda *args: Answer(None, 429, "http-429"), directory=directory)
            [paused] = cache.through(woyo, max_age=0, clock=lambda: NOW.replace(minute=3),
                                     get=lambda *args: self.fail("backoff must prevent a fetch"), directory=directory)
        self.assertEqual((transient["status"], transient["usage"]["periods"][0]["total_tokens"]), ("ok", 2967))
        self.assertEqual((throttled["status"], throttled["usage"]["periods"][0]["total_tokens"]), ("ok", 2967))
        self.assertEqual(paused["retry_until"], throttled["retry_until"])

    def test_concurrent_woyo_reads_share_one_upstream_fetch(self):
        # Dropping the vendor lock would duplicate a sensitive upstream request for simultaneous callers.
        from unlimited.adapters import woyo

        replay = json.loads(REPLAY.read_text())
        cred = Credential("fixture", {"key": "fixture-key"})
        calls = []
        start = threading.Barrier(2)
        results = []

        def get(url, headers, now):
            calls.append((url, headers))
            time.sleep(.05)
            return Answer(replay, 200, None)

        def read():
            start.wait()
            results.append(cache.through(woyo, max_age=300, clock=lambda: NOW, get=get,
                                         directory=self.tmp / "concurrent-cache"))

        with mock.patch.object(woyo, "discover", lambda: [cred]):
            threads = [threading.Thread(target=read) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(len(calls), 1)
        self.assertEqual([r[0]["usage"]["periods"][0]["total_tokens"] for r in results], [2967, 2967])

    def test_cache_attaches_and_strips_a_private_snapshot_before_persisting(self):
        # Omitting the hook leaks raw source facts into the cache and skips retained aggregation.
        from types import SimpleNamespace

        seen = []

        def attach(reading, now):
            seen.append(("_usage_snapshot" in reading, now))
            return dict({k: v for k, v in reading.items() if k != "_usage_snapshot"}, usage={"as_of": now.isoformat()})

        adapter = SimpleNamespace(
            VENDOR="woyo", discover=lambda: [Credential("fixture", {"key": "fixture-key"})],
            read=lambda cred, now, get: {"schema": 1, "vendor": "woyo", "account": cred.account,
                                         "taken_at": now.isoformat(), "source": "api", "status": "ok",
                                         "why": None, "retry_until": None, "limits": [],
                                         "_usage_snapshot": {"events": [], "anchors": [], "empty": True, "unparsed": []}},
            attach=attach,
        )
        directory = self.tmp / "cache"
        first = cache.through(adapter, max_age=300, clock=lambda: NOW, get=None, directory=directory)
        later = NOW.replace(minute=1)
        second = cache.through(adapter, max_age=300, clock=lambda: later, get=None, directory=directory)

        self.assertEqual([r["usage"]["as_of"] for r in (first[0], second[0])], [NOW.isoformat(), later.isoformat()])
        self.assertEqual([had_snapshot for had_snapshot, _ in seen], [True, False])
        persisted = (directory / "woyo.json").read_text()
        self.assertNotIn("_usage_snapshot", persisted)
        self.assertNotIn("fixture-key", persisted)


if __name__ == "__main__":
    unittest.main()
