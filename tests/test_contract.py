"""The slice-1 cache contract, against recorded vendor shapes. No network."""

from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from unlimited import cache, cli, transport
from unlimited.adapters import openai, zai
from unlimited.transport import Answer

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
OPENAI_SECRET = "fixture-secret-openai-token"
ZAI_SECRET = "fixture-secret-zai-key"

# Shapes as answered on 2026-09-18 (2mw2lt accounts_test.py), identities removed.
WHAM = {"plan_type": "prolite",
        "rate_limit": {"allowed": True, "limit_reached": False,
                       "primary_window": {"used_percent": 44, "limit_window_seconds": 604800,
                                          "reset_at": int((NOW + timedelta(days=4)).timestamp())},
                       "secondary_window": None}}
QUOTA = {"code": 200, "success": True, "data": {"level": "max", "limits": [
    {"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "usage": 28000, "currentValue": 4195,
     "nextResetTime": int((NOW + timedelta(hours=3)).timestamp() * 1000)},
    {"type": "CREDIT_LIMIT", "unit": 6, "number": 1, "usage": 140000, "currentValue": 58723,
     "nextResetTime": int((NOW + timedelta(days=3)).timestamp() * 1000)}]}}


class Upstream:
    """A fake vendor side that records every request and can be told to refuse."""

    def __init__(self, delay: float = 0.0):
        self.calls: list[str] = []
        self.refuse: int | None = None
        self.retry_after: timedelta | None = timedelta(seconds=120)
        self.delay = delay
        self._lock = threading.Lock()

    def __call__(self, url, headers, now):
        with self._lock:
            self.calls.append(url)
        time.sleep(self.delay)
        if self.refuse:
            return Answer(None, self.refuse, f"http-{self.refuse}",
                          now + self.retry_after if self.retry_after else None)
        return Answer(WHAM if url == openai.URL else QUOTA, 200, None)


class Contract(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "codex").mkdir()
        (self.tmp / "codex" / "auth.json").write_text(json.dumps(
            {"tokens": {"access_token": OPENAI_SECRET, "account_id": "acct-fixture"}}))
        env = {"CODEX_HOME": str(self.tmp / "codex"), "GLM_API_KEY": ZAI_SECRET,
               "XDG_CACHE_HOME": str(self.tmp / "cache")}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.now = NOW
        self.up = Upstream()

    def read(self, vendor, max_age=300.0):
        return cache.through(vendor, max_age=max_age, clock=lambda: self.now, get=self.up)

    def test_cli_reads_the_selected_vendors_as_json(self):
        buf = io.StringIO()
        with mock.patch.object(transport, "get", self.up), \
             mock.patch.object(cli, "datetime") as dt, redirect_stdout(buf):
            dt.now.return_value = NOW
            cli.main(["read", "--vendor", "openai", "--vendor", "zai", "--max-age", "300", "--json"])
        out = json.loads(buf.getvalue())
        self.assertEqual({r["vendor"] for r in out}, {"openai", "zai"})
        self.assertTrue(all(r["status"] == "ok" and r["schema"] == 1 for r in out))
        weekly = {r["vendor"]: [l for l in r["limits"] if l["window_minutes"] == 10080] for r in out}
        self.assertAlmostEqual(weekly["openai"][0]["used_at_least"], 0.44)
        self.assertAlmostEqual(weekly["zai"][0]["used_at_least"], 58723 / 140000)
        # Only the selected vendors' endpoints are asked; nothing else (e.g. Anthropic's
        # throttled usage endpoint) is touched by a runner-shaped read.
        self.assertEqual(sorted(self.up.calls), sorted([openai.URL, zai.URL]))

    def test_concurrent_identical_reads_make_one_upstream_request(self):
        self.up.delay = 0.3
        threads = [threading.Thread(target=self.read, args=(zai,)) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(self.up.calls, [zai.URL])

    def test_a_reading_older_than_max_age_is_asked_again(self):
        self.read(openai)
        self.now = NOW + timedelta(seconds=299)
        self.read(openai)
        self.assertEqual(len(self.up.calls), 1, "young reading must be served from cache")
        self.now = NOW + timedelta(seconds=301)
        self.read(openai)
        self.assertEqual(len(self.up.calls), 2, "stale reading must be re-asked")

    def test_a_reading_dated_after_the_clock_is_not_fresh(self):
        self.now = NOW + timedelta(minutes=10)
        self.read(openai)
        self.now = NOW  # the clock stepped back: that reading claims to come from the future
        self.read(openai)
        self.assertEqual(len(self.up.calls), 2)

    def test_a_different_sign_in_is_read_afresh_and_the_old_account_is_not_served(self):
        self.read(openai)
        (self.tmp / "codex" / "auth.json").write_text(json.dumps(
            {"tokens": {"access_token": OPENAI_SECRET, "account_id": "acct-other"}}))
        got = self.read(openai)
        self.assertEqual(([r["account"] for r in got], len(self.up.calls)), (["acct-other"], 2))

    def test_a_window_past_its_reset_has_no_used_figure_even_from_cache(self):
        self.read(zai)
        self.now = NOW + timedelta(hours=4)  # five-hour window has reset; weekly has not
        got = self.read(zai, max_age=10**9)[0]
        self.assertEqual(len(self.up.calls), 1, "served from cache, not re-asked")
        by = {l["window_minutes"]: l["used_at_least"] for l in got["limits"]}
        self.assertIsNone(by[300], "a pre-reset figure must not survive its reset")
        self.assertIsNotNone(by[10080])

    def test_a_429_deadline_suppresses_retries_for_every_adapter(self):
        self.up.refuse = 429
        for vendor in (openai, zai):
            with self.subTest(vendor=vendor.VENDOR):
                self.up.calls.clear()
                first = self.read(vendor)[0]
                self.assertEqual((first["status"], first["why"]), ("refused", "http-429"))
                self.assertIsNotNone(first["retry_until"])
                self.now = NOW + timedelta(seconds=60)
                self.read(vendor, max_age=0)  # a caller demanding fresh data still waits
                self.assertEqual(len(self.up.calls), 1)
                self.now = NOW + cache.MIN_BACKOFF + timedelta(seconds=1)
                self.up.refuse = None
                self.assertEqual(self.read(vendor, max_age=0)[0]["status"], "ok")
                self.assertEqual(len(self.up.calls), 2)
                self.now, self.up.refuse = NOW, 429

    def test_a_429_keeps_the_last_good_reading_and_holds_off_even_without_retry_after(self):
        good = self.read(zai)[0]
        self.up.refuse = 429
        self.up.retry_after = None
        self.now = NOW + timedelta(minutes=10)
        got = self.read(zai)[0]
        self.assertEqual((got["status"], got["taken_at"]), ("ok", good["taken_at"]))
        self.assertEqual(got["retry_until"], (self.now + cache.MIN_BACKOFF).isoformat())
        self.now += timedelta(minutes=1)
        self.read(zai, max_age=0)
        self.assertEqual(len(self.up.calls), 2, "the deadline binds even a caller asking for fresh data")

    def test_a_newer_local_reading_keeps_the_throttle_deadline(self):
        from types import SimpleNamespace
        from unlimited.schema import reading
        fresh = [reading("zai", None, NOW, "ok")]
        adapter = SimpleNamespace(VENDOR="zai", discover=zai.discover, read=lambda c, n, g: zai.read(c, n, self.up),
                                  local=lambda now: [dict(r, account=zai.discover()[0].account) for r in fresh])
        self.up.refuse = 429
        self.now = NOW + timedelta(minutes=1)
        fresh[0] = reading("zai", None, self.now - timedelta(minutes=30), "ok")
        self.read(adapter, max_age=0)
        fresh[0] = reading("zai", None, self.now + timedelta(minutes=1), "ok")
        self.now += timedelta(minutes=2)
        self.read(adapter)
        self.read(adapter, max_age=0)
        self.assertEqual(len(self.up.calls), 1, "a capture landing mid-backoff must not erase the deadline")

    def test_a_server_fault_keeps_the_last_good_reading_too(self):
        good = self.read(zai)[0]
        self.up.refuse, self.now = 503, NOW + timedelta(minutes=10)
        self.assertEqual(self.read(zai)[0]["taken_at"], good["taken_at"])

    def test_a_refused_account_keeps_its_deadline_while_a_stale_sibling_is_re_asked(self):
        from types import SimpleNamespace
        from unlimited.credential import Credential
        from unlimited.schema import reading
        asked = []
        two = SimpleNamespace(VENDOR="zai", discover=lambda: [
            Credential("a", {"key": "k-a"}), Credential("b", {"key": "k-b"})],
            read=lambda cred, now, get: (asked.append(cred.account), zai.read(cred, now, self.up))[1])
        taken = NOW - timedelta(seconds=60)
        seed = [reading("zai", "a", taken, "refused", why="http-429",
                        retry_until=NOW + timedelta(seconds=60)),
                reading("zai", "b", taken, "ok")]
        d = self.tmp / "cache" / "unlimited"
        d.mkdir(parents=True)
        (d / "zai.json").write_text(json.dumps({"readings": seed}))
        self.read(two, max_age=30)
        self.assertEqual(asked, ["b"], "the stale sibling is re-asked; the refused one waits")

    def test_no_secret_reaches_output_or_cache(self):
        for vendor in (openai, zai):
            self.read(vendor)
        self.up.refuse = 401
        for vendor in (openai, zai):
            self.read(vendor, max_age=0)
        dumped = json.dumps([self.read(v, max_age=10**9) for v in (openai, zai)])
        dumped += "".join(p.read_text() for p in (self.tmp / "cache" / "unlimited").glob("*.json"))
        dumped += repr(openai.discover()) + repr(zai.discover())
        for secret in (OPENAI_SECRET, ZAI_SECRET):
            self.assertNotIn(secret, dumped)

    def test_cache_files_are_private(self):
        self.read(zai)
        mode = (self.tmp / "cache" / "unlimited" / "zai.json").stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_every_request_names_this_tool_not_python_urllib(self):
        seen = {}

        class Body(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def urlopen(req, timeout):
            seen.update(req.headers)
            return Body(b"{}")
        with mock.patch.object(transport.urllib.request, "urlopen", urlopen):
            transport.get("https://example.test", {"Authorization": "Bearer x"}, NOW)
        self.assertEqual(seen.get("User-agent"), transport.USER_AGENT)

    def test_retry_after_accepts_http_date(self):
        self.assertEqual(transport.retry_until("Sat, 19 Sep 2026 12:02:00 GMT", NOW),
                         NOW + timedelta(minutes=2))
        self.assertEqual(transport.retry_until("30", NOW), NOW + timedelta(seconds=30))
        self.assertIsNone(transport.retry_until("soon", NOW))
        self.assertEqual(transport.retry_until(str(10**9), NOW), NOW + transport.MAX_BACKOFF,
                         "a huge Retry-After must not block reads for years")

    def test_a_zai_time_limit_is_not_named_as_a_usage_window(self):
        body = {"success": True, "data": {"limits": [
            {"type": "TIME_LIMIT", "unit": 3, "number": 5, "usage": 100, "currentValue": 5,
             "nextResetTime": int((NOW + timedelta(hours=3)).timestamp() * 1000)}]}}
        (l,) = zai.limits(body, NOW)
        self.assertEqual((l["name"], l["kind"]), ("time_limit 3x5", "TIME_LIMIT"))

    def test_an_empty_zai_answer_is_unread_not_zero(self):
        from unlimited.credential import Credential
        got = zai.read(Credential("a", {"key": "k"}), NOW,
                       lambda u, h, n: Answer({"success": True, "data": {}}, 200, None))
        self.assertEqual((got["status"], got["why"]), ("unread", "no-limits"))


if __name__ == "__main__":
    unittest.main()
