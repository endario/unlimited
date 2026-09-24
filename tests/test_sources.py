"""Local sources, discovery and last-good retention. No network, no keychain."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from unlimited import cache, cli
from unlimited.adapters import anthropic, kimi, opencode, openai, xai, zai
from unlimited.credential import Credential
from unlimited.transport import Answer

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
UUID = "11111111-2222-3333-4444-555555555555"
RL = {"five_hour": {"used_percentage": 23.5, "resets_at": int((NOW + timedelta(hours=2)).timestamp())},
      "seven_day": {"used_percentage": 41.2, "resets_at": int((NOW + timedelta(days=2)).timestamp())}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.home.mkdir()
        p = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.tmp / "cache"), "HOME": str(self.home),
                                          "CLAUDE_GLM_ENV": "", "GLM_API_KEY": "",
                                          "CLAUDE_KIMI_ENV": "", "KIMI_API_KEY": ""})
        p.start()
        self.addCleanup(p.stop)
        # A real OPENCODE_2_API_KEY (or higher) on the machine running this suite would otherwise
        # leak into every opencode.discover() call below, however many the test expects.
        for k in [k for k in os.environ if opencode.ENV_KEY.match(k)]:
            del os.environ[k]
        self.calls = []

    def signed_in(self, name: str, uuid: str = UUID) -> Path:
        d = self.home / name
        d.mkdir()
        (d / ".claude.json").write_text(json.dumps({"oauthAccount": {"accountUuid": uuid}}))
        return d

    def up(self, answer: Answer):
        def get(url, headers, now):
            self.calls.append(url)
            return answer
        return get


class Statusline(Base):
    def claude(self):
        return SimpleNamespace(VENDOR="anthropic", local=anthropic.local, read=anthropic.read,
                               discover=lambda: [Credential(UUID, {"token": "t", "expires": None})])

    def test_a_fresh_capture_answers_without_asking_anthropic(self):
        d = self.signed_in(".claude-account2")
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(d)}):
            anthropic.capture({"rate_limits": RL}, NOW)
        got = cache.through(self.claude(), max_age=300, clock=lambda: NOW + timedelta(seconds=60),
                            get=self.up(Answer(None, 429, "http-429")))[0]
        self.assertEqual(self.calls, [], "the throttled endpoint must not be asked")
        self.assertEqual(got["source"], "statusline")
        by = {l["name"]: l["used_at_least"] for l in got["limits"]}
        self.assertAlmostEqual(by["five_hour"], 0.235)
        self.assertAlmostEqual(by["seven_day"], 0.412)

    def test_a_stale_capture_falls_back_to_the_api(self):
        d = self.signed_in(".claude-account2")
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(d)}):
            anthropic.capture({"rate_limits": RL}, NOW)
        body = {"five_hour": {"utilization": 30, "resets_at": (NOW + timedelta(hours=2)).isoformat()}}
        got = cache.through(self.claude(), max_age=300, clock=lambda: NOW + timedelta(minutes=10),
                            get=self.up(Answer(body, 200, None)))[0]
        self.assertEqual((self.calls, got["source"]), ([anthropic.URL, anthropic.PROFILE_URL], "api"))

    def test_capture_without_rate_limits_writes_nothing_and_the_cli_stays_silent(self):
        d = self.signed_in(".claude-account2")
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(d)}), \
             mock.patch("sys.stdin", io.StringIO(json.dumps({"model": {}}))), \
             mock.patch("sys.stdout", out):
            self.assertEqual(cli.main(["capture", "claude-statusline"]), 0)
            with mock.patch("sys.stdin", io.StringIO("not json")):
                self.assertEqual(cli.main(["capture", "claude-statusline"]), 0)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(anthropic.local(NOW), [])


class Discovery(Base):
    def test_one_credential_per_account_preferring_an_unexpired_token_and_skipping_unsigned_dirs(self):
        self.signed_in(".claude-a")
        self.signed_in(".claude-b")
        (self.home / ".claude-glm").mkdir()  # a wrapper dir: copied token, no oauthAccount
        live = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp() * 1000)
        recs = {".claude-a": [{"accessToken": "old", "expiresAt": 1}],
                ".claude-b": [{"accessToken": "new", "expiresAt": live}],
                ".claude-glm": [{"accessToken": "glm", "expiresAt": live}]}
        with mock.patch.object(anthropic, "_oauth", lambda d: recs.get(d.name, [])):
            creds = anthropic.discover()
        self.assertEqual([(c.account, c.secret["token"]) for c in creds], [(UUID, "new")])

    def test_session_window_usage_reset_and_lock_are_read(self):
        body = {"five_hour": {"utilization": 43.0, "resets_at": (NOW + timedelta(hours=2)).isoformat(),
                              "locked_reason": None},
                "seven_day": {"utilization": 100.0, "resets_at": (NOW + timedelta(hours=9)).isoformat(),
                              "locked_reason": "weekly_limit"}}
        got = anthropic.read(Credential(UUID, {"token": "t", "expires": None}), NOW, self.up(Answer(body, 200, None)))
        by = {l["name"]: l for l in got["limits"]}
        self.assertEqual((by["five_hour"]["used_at_least"], by["five_hour"]["resets_at"], by["five_hour"]["held"]),
                         (0.43, (NOW + timedelta(hours=2)).isoformat(), False))
        self.assertEqual((by["seven_day"]["held"], by["seven_day"]["held_why"]), (True, "weekly_limit"))

    def test_the_plan_is_the_organisations_current_tier_and_its_absence_costs_only_the_plan(self):
        usage = {"five_hour": {"utilization": 10.0, "resets_at": (NOW + timedelta(hours=2)).isoformat()}}

        def get(url, headers, now):
            if url == anthropic.PROFILE_URL:
                return Answer({"organization": {"rate_limit_tier": "default_claude_max_5x"}}, 200, None)
            return Answer(usage, 200, None)
        cred = Credential(UUID, {"token": "t", "expires": None})
        self.assertEqual(anthropic.read(cred, NOW, get)["plan"], "default_claude_max_5x")
        down = anthropic.read(cred, NOW, lambda u, h, n: Answer(usage, 200, None) if u == anthropic.URL
                              else Answer(None, 403, "http-403"))
        self.assertEqual((down["status"], down["plan"]), ("ok", None))

    SPEND = {"used": {"amount_minor": 15062, "currency": "SGD", "exponent": 2},
             "limit": {"amount_minor": 15000, "currency": "SGD", "exponent": 2},
             "balance": {"amount_minor": 4050, "currency": "SGD", "exponent": 2},
             "enabled": False, "disabled_reason": "org_level_disabled_until", "severity": "critical",
             "can_purchase_credits": False}

    def test_spend_past_the_windows_is_read_as_credits_in_major_units(self):
        body = {"five_hour": {"utilization": 100.0, "resets_at": (NOW + timedelta(hours=2)).isoformat()},
                "extra_usage": {"utilization": 100.0, "is_enabled": False}, "spend": self.SPEND}
        got = anthropic.read(Credential(UUID, {"token": "t", "expires": None}), NOW, self.up(Answer(body, 200, None)))
        self.assertEqual(got["credits"], {
            "taken_at": NOW.isoformat(), "enabled": False, "used": 150.62, "limit": 150.0, "balance": 40.5,
            "currency": "SGD", "severity": "critical", "disabled_reason": "org_level_disabled_until",
            "can_purchase": False})
        self.assertEqual([l["name"] for l in got["limits"] if l["name"] == "extra_usage"], [],
                         "spend is not a window; it must not also appear as a limit with no figure")

    def test_an_account_that_never_enabled_credits_has_none_to_spend_and_says_so(self):
        body = {"spend": {"used": {"amount_minor": 0, "currency": "USD", "exponent": 2}, "limit": None,
                          "balance": None, "enabled": False, "disabled_reason": None}}
        c = anthropic.read(Credential(UUID, {"token": "t", "expires": None}), NOW,
                           self.up(Answer(body, 200, None)))["credits"]
        self.assertEqual((c["enabled"], c["used"], c["limit"], c["balance"]), (False, 0.0, None, None))

    def test_a_reply_without_a_readable_spend_has_no_credits_rather_than_a_guess(self):
        for spend in (None, {}, {"enabled": "yes"}, []):
            got = anthropic.read(Credential(UUID, {"token": "t", "expires": None}), NOW,
                                 self.up(Answer({"spend": spend}, 200, None)))
            self.assertIsNone(got["credits"], spend)
        odd = dict(self.SPEND, used={"amount_minor": "15062", "currency": "SGD", "exponent": 2})
        c = anthropic.read(Credential(UUID, {"token": "t", "expires": None}), NOW,
                           self.up(Answer({"spend": odd}, 200, None)))["credits"]
        self.assertIsNone(c["used"], "a malformed amount is unknown, not zero")

    def test_a_fresh_capture_keeps_the_credits_the_api_named_with_their_own_age(self):
        d = self.signed_in(".claude-account2")
        claude = SimpleNamespace(VENDOR="anthropic", local=anthropic.local, read=anthropic.read,
                                 discover=lambda: [Credential(UUID, {"token": "t", "expires": None})])
        get = lambda url, headers, now: Answer({"spend": self.SPEND}, 200, None)
        cache.through(claude, max_age=300, clock=lambda: NOW, get=get)
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(d)}):
            anthropic.capture({"rate_limits": RL}, NOW + timedelta(seconds=30))
        got = cache.through(claude, max_age=300, clock=lambda: NOW + timedelta(seconds=40), get=get)[0]
        self.assertEqual((got["source"], got["credits"]["used"], got["credits"]["taken_at"]),
                         ("statusline", 150.62, NOW.isoformat()))

    def test_an_empty_limits_list_is_the_accounts_word_that_nothing_holds_it(self):
        cred = Credential(UUID, {"token": "t", "expires": None})
        said = anthropic.read(cred, NOW, self.up(Answer({"limits": []}, 200, None)))
        silent = anthropic.read(cred, NOW, self.up(Answer({}, 200, None)))
        self.assertEqual([(l["name"], l["held"], l["kind"]) for l in said["limits"]], [("limits:none", False, "none")])
        self.assertEqual(silent["limits"], [])

    def test_a_fresh_capture_keeps_the_plan_the_api_named(self):
        d = self.signed_in(".claude-account2")
        claude = SimpleNamespace(VENDOR="anthropic", local=anthropic.local, read=anthropic.read,
                                 discover=lambda: [Credential(UUID, {"token": "t", "expires": None})])
        def get(url, headers, now):
            return Answer({"organization": {"rate_limit_tier": "default_claude_max_5x"}} if url == anthropic.PROFILE_URL
                          else {"five_hour": {"utilization": 1, "resets_at": (NOW + timedelta(hours=1)).isoformat()}}, 200, None)
        cache.through(claude, max_age=300, clock=lambda: NOW, get=get)
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(d)}):
            anthropic.capture({"rate_limits": RL}, NOW + timedelta(seconds=30))
        got = cache.through(claude, max_age=300, clock=lambda: NOW + timedelta(seconds=40), get=get)[0]
        self.assertEqual((got["source"], got["plan"]), ("statusline", "default_claude_max_5x"))

    def test_the_accounts_own_severity_is_kept_verbatim(self):
        body = {"limits": [
            {"kind": "session", "group": "session", "percent": 44, "severity": "warning",
             "resets_at": (NOW + timedelta(hours=2)).isoformat(), "scope": None, "is_active": True},
            {"kind": "weekly_scoped", "group": "weekly", "percent": 0, "severity": "normal",
             "resets_at": (NOW + timedelta(days=2)).isoformat(),
             "scope": {"model": {"id": None, "display_name": "Fable"}}, "is_active": False}]}
        got = anthropic.read(Credential(UUID, {"token": "t", "expires": None}), NOW, self.up(Answer(body, 200, None)))
        by = {l["name"]: l for l in got["limits"]}
        self.assertEqual({k: (v["window_minutes"], v["severity"], v["active"]) for k, v in by.items()},
                         {"limits:session": (300, "warning", True),
                          "limits:weekly_scoped:Fable": (10080, "normal", False)})
        self.assertIsNone(by["limits:session"]["held"], "severity is the vendor's word; no threshold here")
        bare = anthropic.read(Credential(UUID, {"token": "t", "expires": None}), NOW, self.up(Answer(
            {"limits": [{"kind": "weekly_all", "percent": 88, "severity": "warning", "is_active": True}]}, 200, None)))
        self.assertAlmostEqual(bare["limits"][0]["used_at_least"], 0.88, msg="no reset is not a passed reset")

    def test_an_unstarted_session_window_is_zero_not_unknown(self):
        body = {"five_hour": {"utilization": 0.0, "resets_at": None}}
        got = anthropic.read(Credential(UUID, {"token": "t", "expires": None}), NOW, self.up(Answer(body, 200, None)))
        from unlimited.schema import settled
        l = settled(got, NOW + timedelta(days=1))["limits"][0]
        self.assertEqual((l["used_at_least"], l["resets_at"]), (0.0, None))

    def test_an_expired_token_is_not_sent(self):
        got = anthropic.read(Credential(UUID, {"token": "t", "expires": 1}), NOW,
                             self.up(Answer({}, 200, None)))
        self.assertEqual((got["status"], got["why"], self.calls), ("unread", "credential-expired", []))

    def test_an_account_with_no_oauth_record_reads_as_no_credential(self):
        self.signed_in(".claude-a")
        with mock.patch.object(anthropic, "_oauth", lambda d: []):
            [cred] = anthropic.discover()
        got = anthropic.read(cred, NOW, self.up(Answer({}, 200, None)))
        self.assertEqual((got["status"], got["why"], self.calls), ("unread", "no-credential", []))

    def test_a_tombstoned_account_reads_as_signed_out_not_as_no_credential(self):
        self.signed_in(".claude-a")
        tombstone = [{"accessToken": "", "refreshToken": "", "expiresAt": 0}]
        with mock.patch.object(anthropic, "_oauth", lambda d: tombstone):
            [cred] = anthropic.discover()
        got = anthropic.read(cred, NOW, self.up(Answer({}, 200, None)))
        self.assertEqual((got["status"], got["why"], self.calls), ("unread", "signed-out", []))


class OpenCodeGo(Base):
    BODY = {"usage": {
        "rolling": {"status": "ok", "percent": 37, "resetsAt": (NOW + timedelta(hours=2)).isoformat()},
        "weekly": {"status": "rate-limited", "percent": 100, "resetsAt": (NOW + timedelta(days=2)).isoformat()},
        "monthly": {"status": "ok", "percent": 61, "resetsAt": (NOW + timedelta(days=20)).isoformat()}}}

    def test_three_windows_with_usage_reset_and_the_vendors_hold(self):
        got = opencode.read(Credential("a", {"key": "k"}), NOW, self.up(Answer(self.BODY, 200, None)))
        by = {l["name"]: (l["window_minutes"], l["used_at_least"], l["held"]) for l in got["limits"]}
        self.assertEqual(by, {"five_hour": (300, 0.37, False), "seven_day": (10080, 1.0, True),
                              "month": (43200, 0.61, False)})
        self.assertEqual(got["limits"][0]["resets_at"], (NOW + timedelta(hours=2)).isoformat())

    def test_a_key_without_go_is_no_subscription_not_a_refusal(self):
        got = opencode.read(Credential("a", {"key": "k"}), NOW, self.up(Answer(None, 403, "http-403")))
        self.assertEqual((got["status"], got["why"]), ("unread", "no-subscription"))

    def test_the_go_key_is_found_in_opencodes_auth_file_and_never_shown(self):
        d = self.tmp / "data" / "opencode"
        d.mkdir(parents=True)
        (d / "auth.json").write_text(json.dumps({"openai": {"type": "oauth", "access": "x"},
                                                 "opencode-go": {"type": "api", "key": "go-secret"}}))
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.tmp / "data")}):
            (c,) = opencode.discover()
        self.assertEqual(c.secret["key"], "go-secret")
        self.assertNotIn("go-secret", repr(c) + c.account)

    def test_a_second_isolated_identity_is_a_second_go_account(self):
        default = self.tmp / "data" / "opencode"
        default.mkdir(parents=True)
        (default / "auth.json").write_text(json.dumps({"opencode-go": {"type": "api", "key": "key-one"}}))
        second = self.home / ".opencode-2" / "opencode"
        second.mkdir(parents=True)
        (second / "auth.json").write_text(json.dumps({"opencode-go": {"type": "api", "key": "key-two"}}))
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.tmp / "data")}):
            got = opencode.discover()
        self.assertEqual(sorted(c.secret["key"] for c in got), ["key-one", "key-two"])
        self.assertEqual({c.account for c in got}, {opencode.account_of("key-one"), opencode.account_of("key-two")})

    def test_a_numbered_launcher_key_is_also_an_account(self):
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.tmp / "data"),
                                          "OPENCODE_API_KEY": "key-one", "OPENCODE_2_API_KEY": "key-two"}):
            got = opencode.discover()
        self.assertEqual(sorted(c.secret["key"] for c in got), ["key-one", "key-two"])
        self.assertEqual({c.account for c in got}, {opencode.account_of("key-one"), opencode.account_of("key-two")})

    def test_slots_sort_numerically_past_nine_and_a_renamed_slot_is_ignored(self):
        for name in (".opencode-2", ".opencode-10", ".opencode-old"):
            (self.home / name).mkdir()
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.tmp / "data")}):
            got = opencode.data_homes()
        self.assertEqual([p.name for p in got[1:]], [".opencode-2", ".opencode-10"])

    def test_xdg_data_home_pointed_at_a_slot_is_not_listed_twice(self):
        slot = self.home / ".opencode-2"
        slot.mkdir()
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(slot)}):
            got = opencode.data_homes()
        self.assertEqual(got, [slot])


class Grok(Base):
    BODY = {"config": {"currentPeriod": {"type": "USAGE_PERIOD_TYPE_WEEKLY",
                                         "start": (NOW - timedelta(days=2)).isoformat(),
                                         "end": (NOW + timedelta(days=5)).isoformat()},
                       "creditUsagePercent": 28.0}}

    def test_the_weekly_period_with_usage_and_reset(self):
        got = xai.read(Credential("u", {"key": "k", "expires": None}), NOW, self.up(Answer(self.BODY, 200, None)))
        (l,) = got["limits"]
        self.assertEqual((l["name"], l["window_minutes"], l["used_at_least"], l["resets_at"]),
                         ("seven_day", 10080, 0.28, (NOW + timedelta(days=5)).isoformat()))

    def test_the_plan_is_the_tier_grok_names(self):
        def get(url, headers, now):
            self.calls.append(url)
            return Answer({"subscription_tier_display": "SuperGrok"} if url == xai.SETTINGS_URL else self.BODY, 200, None)
        got = xai.read(Credential("u", {"key": "k", "expires": None}), NOW, get)
        self.assertEqual(got["plan"], "SuperGrok")

    def test_an_expired_grok_token_is_not_sent(self):
        got = xai.read(Credential("u", {"key": "k", "expires": (NOW - timedelta(minutes=1)).isoformat()}),
                       NOW, self.up(Answer(self.BODY, 200, None)))
        self.assertEqual((got["why"], self.calls), ("credential-expired", []))


class Zai(Base):
    def env(self, name: str, key: str) -> Path:
        (self.home / ".config").mkdir(exist_ok=True)
        f = self.home / ".config" / name
        f.write_text(f'GLM_API_KEY="{key}"\nCLAUDE_CONFIG_DIR="$HOME/.claude-glm"\n')
        return f

    def test_every_wrappers_key_is_an_account_even_inside_one_glm_session(self):
        self.env("claude-glm.env", "key-one")
        second = self.env("claude-glm-2.env", "key-two")
        # A claude-glm-2 session exports its own file and key; the first account stays in view.
        with mock.patch.dict(os.environ, {"CLAUDE_GLM_ENV": str(second), "GLM_API_KEY": "key-two"}):
            got = zai.discover()
        self.assertEqual(sorted(c.secret["key"] for c in got), ["key-one", "key-two"])
        self.assertEqual({c.account for c in got}, {zai.account_of("key-one"), zai.account_of("key-two")})

    def test_a_token_limit_answered_as_a_percentage_is_a_usage_window(self):
        at = int((NOW + timedelta(days=3)).timestamp() * 1000)
        got = {l["window_minutes"]: l for l in zai.limits({"data": {"limits": [
            {"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "percentage": 0},
            {"type": "TOKENS_LIMIT", "unit": 6, "number": 1, "percentage": 9, "nextResetTime": at},
            {"type": "TIME_LIMIT", "unit": 5, "number": 1, "usage": 4000, "currentValue": 0,
             "percentage": 0, "nextResetTime": at}]}}, NOW)}
        self.assertEqual((got[10080]["name"], got[10080]["used_at_least"]), ("seven_day", 0.09))
        # Unopened: no reset time, but zero is what the account says, not unknown.
        self.assertEqual((got[300]["name"], got[300]["used_at_least"]), ("five_hour", 0.0))
        self.assertNotIn("seven_day", [l["name"] for l in got.values() if l["kind"] == "TIME_LIMIT"])


class Kimi(Base):
    def env(self, name: str, key: str) -> Path:
        (self.home / ".config").mkdir(exist_ok=True)
        f = self.home / ".config" / name
        f.write_text(f'KIMI_API_KEY="{key}"\nCLAUDE_CONFIG_DIR="$HOME/.claude-kimi"\n')
        return f

    def test_every_wrappers_key_is_an_account_even_inside_one_kimi_session(self):
        self.env("claude-kimi.env", "key-one")
        second = self.env("claude-kimi-2.env", "key-two")
        with mock.patch.dict(os.environ, {"CLAUDE_KIMI_ENV": str(second), "KIMI_API_KEY": "key-two"}):
            got = kimi.discover()
        self.assertEqual(sorted(c.secret["key"] for c in got), ["key-one", "key-two"])
        self.assertEqual({c.account for c in got}, {kimi.account_of("key-one"), kimi.account_of("key-two")})

    def test_the_real_counts_are_read_ahead_of_the_vendors_broken_ratio(self):
        # The real /usages shape: `usage`/`limits` give real, non-zero counts (as strings) for the
        # same two windows `usages.limit_5h/7d.used_ratio` also names — but confirmed live against
        # the account (and MoonshotAI/kimi-code#3951) to sit stuck at 0 regardless of real usage.
        # The counts must win.
        body = {"usages": {"limit_5h": {"used_ratio": 0, "reset_time": "2026-09-23T14:28:58Z"},
                           "limit_7d": {"used_ratio": 0, "reset_time": "2026-09-28T09:28:58Z"}},
                "usage": {"limit": "100", "used": "61", "resetTime": "2026-09-28T09:28:59.213744Z"},
                "limits": [{"window": {"duration": 300, "timeUnit": "TIME_UNIT_MINUTE"},
                           "detail": {"limit": "100", "used": "38", "resetTime": "2026-09-23T14:28:59.213744Z"}}]}
        got = {l["name"]: l for l in kimi.limits(body, NOW)}
        self.assertEqual(set(got), {"five_hour", "seven_day"})
        self.assertEqual((got["five_hour"]["used_at_least"], got["seven_day"]["used_at_least"]), (0.38, 0.61))

    def test_the_ratio_is_read_only_when_the_counts_are_entirely_absent(self):
        got = kimi.limits({"usages": {"limit_5h": {"used_ratio": 0.5, "reset_time": "2026-09-23T14:28:58Z"}}}, NOW)
        self.assertEqual([(l["name"], l["used_at_least"]) for l in got], [("five_hour", 0.5)])

    def test_the_string_typed_limit_and_used_fields_are_parsed_as_numbers(self):
        got = kimi.limits({"usage": {"limit": "100", "used": "61"}}, NOW)
        self.assertAlmostEqual(got[0]["used_at_least"], 0.61)

    def test_windowed_limits_are_named_from_their_duration_and_unit(self):
        at = (NOW + timedelta(days=3)).isoformat().replace("+00:00", "Z")
        body = {"limits": [
            {"window": {"duration": 5, "timeUnit": "HOUR"}, "detail": {"limit": 100, "used": 30}},
            {"window": {"duration": 7, "timeUnit": "DAY"}, "detail": {"limit": 1000, "used": 90, "resetTime": at}}]}
        got = {l["name"]: l for l in kimi.limits(body, NOW)}
        self.assertEqual((got["five_hour"]["window_minutes"], got["five_hour"]["used_at_least"]), (300, 0.3))
        self.assertEqual((got["seven_day"]["window_minutes"], got["seven_day"]["used_at_least"],
                          got["seven_day"]["resets_at"]), (10080, 0.09, at.replace("Z", "+00:00")))

    def test_the_top_level_usage_total_is_the_weekly_window(self):
        got = kimi.limits({"usage": {"limit": 1000, "used": 400}}, NOW)
        self.assertEqual([(l["name"], l["window_minutes"], l["used_at_least"]) for l in got],
                         [("seven_day", 10080, 0.4)])

    def test_a_seven_day_entry_in_limits_is_not_duplicated_by_the_usage_aggregate(self):
        # Both name the same window: `limits[]`'s own entry must win, and `usage` must not also
        # append a second "seven_day" row.
        body = {"limits": [{"window": {"duration": 7, "timeUnit": "DAY"}, "detail": {"limit": 1000, "used": 90}}],
                "usage": {"limit": 1000, "used": 400}}
        got = [l for l in kimi.limits(body, NOW) if l["name"] == "seven_day"]
        self.assertEqual([l["used_at_least"] for l in got], [0.09])

    def test_an_unrecognised_unit_string_does_not_substring_match_a_known_one(self):
        # "HOUR" must not match inside an unrelated word that happens to contain it.
        minutes = kimi._window_minutes({"duration": 5, "timeUnit": "SOMEHOURISH"})
        self.assertIsNone(minutes)

    def test_a_non_finite_value_is_read_as_unknown_not_a_crash(self):
        for bad in ("Infinity", "-Infinity", "NaN"):
            self.assertIsNone(kimi._float(bad), bad)
        got = kimi.limits({"limits": [{"window": {"duration": "Infinity", "timeUnit": "HOUR"},
                                       "detail": {"limit": 100, "used": 10}}]}, NOW)
        self.assertEqual([l["window_minutes"] for l in got], [None])

    def test_a_duration_whose_product_overflows_is_read_as_unknown_not_a_crash(self):
        # "1e308" is itself finite; only `duration * per_minute` (1440 for DAY) overflows.
        self.assertIsNone(kimi._window_minutes({"duration": "1e308", "timeUnit": "DAY"}))

    def test_a_remaining_figure_without_used_is_read_as_used(self):
        got = kimi.limits({"usage": {"limit": 100, "remaining": 70}}, NOW)
        self.assertEqual(got[0]["used_at_least"], 0.3)

    def test_a_relative_reset_in_seconds_is_read_against_now(self):
        got = kimi.limits({"usage": {"limit": 100, "used": 1, "reset_in": 3600}}, NOW)
        self.assertEqual(got[0]["resets_at"], (NOW + timedelta(hours=1)).isoformat())

    def test_a_404_falls_back_to_the_singular_usage_endpoint(self):
        def get(url, headers, now):
            self.calls.append(url)
            return Answer(None, 404, "http-404") if url == kimi.URL else Answer({"usage": {"limit": 10, "used": 1}}, 200, None)
        got = kimi.read(Credential("a", {"key": "k"}), NOW, get)
        self.assertEqual((self.calls, got["status"], got["limits"][0]["used_at_least"]),
                         ([kimi.URL, kimi.FALLBACK_URL], "ok", 0.1))

    def test_a_reply_with_no_readable_limits_is_unread_not_a_guess(self):
        got = kimi.read(Credential("a", {"key": "k"}), NOW, self.up(Answer({}, 200, None)))
        self.assertEqual((got["status"], got["why"]), ("unread", "no-limits"))


class LastGood(Base):
    def adapter(self, answer):
        return SimpleNamespace(VENDOR="x", discover=lambda: [Credential("a", {})],
                               read=lambda c, now, get: (
                                   {"schema": 1, "vendor": "x", "account": "a", "taken_at": now.isoformat(),
                                    "source": "api", "status": "ok", "why": None, "retry_until": None, "limits": []}
                                   if answer is None else
                                   {"schema": 1, "vendor": "x", "account": "a", "taken_at": now.isoformat(),
                                    "source": "api", "status": answer[0], "why": answer[1],
                                    "retry_until": None, "limits": []}))

    def test_a_network_failure_keeps_the_last_good_reading_with_its_age(self):
        cache.through(self.adapter(None), max_age=300, clock=lambda: NOW, get=None)
        got = cache.through(self.adapter(("unread", "unreachable")), max_age=300,
                            clock=lambda: NOW + timedelta(minutes=10), get=None)[0]
        self.assertEqual((got["status"], got["taken_at"]), ("ok", NOW.isoformat()))

    def test_an_auth_refusal_replaces_it(self):
        cache.through(self.adapter(None), max_age=300, clock=lambda: NOW, get=None)
        got = cache.through(self.adapter(("refused", "http-401")), max_age=300,
                            clock=lambda: NOW + timedelta(minutes=10), get=None)[0]
        self.assertEqual((got["status"], got["why"]), ("refused", "http-401"))


class CodexSessionLog(Base):
    def setUp(self):
        super().setUp()
        self.codex = self.tmp / "codex"
        (self.codex / "sessions" / "2026").mkdir(parents=True)
        (self.codex / "auth.json").write_text(json.dumps({"tokens": {"access_token": "t", "account_id": "acct"}}))
        os.utime(self.codex / "auth.json", (NOW.timestamp() - 3600,) * 2)
        p = mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex)})
        p.start()
        self.addCleanup(p.stop)

    def log(self, name, events):
        f = self.codex / "sessions" / "2026" / name
        f.write_text("\n".join(json.dumps({"timestamp": at.isoformat().replace("+00:00", "Z"),
                                           "type": "event_msg",
                                           "payload": {"type": "token_count", "rate_limits": snap}})
                               for at, snap in events))
        os.utime(f, (NOW.timestamp(),) * 2)

    def snap(self, limit_id, pct, name=None):
        return {"limit_id": limit_id, "limit_name": name,
                "primary": {"used_percent": pct, "window_minutes": 10080,
                            "resets_at": int((NOW + timedelta(days=3)).timestamp())}, "secondary": None}

    def test_limits_logged_in_separate_files_join_into_one_reading(self):
        self.log("a.jsonl", [(NOW - timedelta(minutes=5), self.snap("codex", 26.0))])
        self.log("b.jsonl", [(NOW - timedelta(minutes=1), self.snap("base_model_inference", 3.0, "gpt-reserve"))])
        (r,) = openai.local(NOW)
        self.assertEqual({l["name"]: l["used_at_least"] for l in r["limits"]}, {"codex": 0.26, "gpt-reserve": 0.03})
        self.assertEqual(r["taken_at"], (NOW - timedelta(minutes=5)).isoformat(), "as old as its oldest part")

    def test_only_the_tail_of_a_long_log_is_read_and_its_cut_line_is_dropped(self):
        events = [(NOW - timedelta(minutes=50 - i), self.snap("codex", float(i))) for i in range(40)]
        self.log("a.jsonl", events)
        size = (self.codex / "sessions" / "2026" / "a.jsonl").stat().st_size
        with mock.patch.object(openai, "TAIL", size // 4), \
             mock.patch.object(openai.json, "loads", wraps=json.loads) as parsed:
            (r,) = openai.local(NOW)
        self.assertEqual(r["limits"][0]["used_at_least"], 0.39)
        self.assertLess(parsed.call_count, 15, "the head of the log was never parsed")

    def test_no_reading_without_the_main_limit(self):
        self.log("b.jsonl", [(NOW - timedelta(minutes=1), self.snap("base_model_inference", 3.0, "gpt-reserve"))])
        self.assertEqual(openai.local(NOW), [])

    def test_events_from_before_the_current_sign_in_are_ignored(self):
        self.log("a.jsonl", [(NOW - timedelta(hours=2), self.snap("codex", 90.0))])
        self.assertEqual(openai.local(NOW), [])


if __name__ == "__main__":
    unittest.main()
