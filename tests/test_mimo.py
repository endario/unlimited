"""Xiaomi MiMo's seat allowance and balance on the platform's sign-in cookies. No network, no
keychain, no Chrome."""

from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from unlimited import chrome_cookies, show
from unlimited.adapters import mimo
from unlimited.credential import Credential
from unlimited.transport import Answer

import scratch

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
PROJECTS = {"code": 0, "message": "", "data": {"projects": [
    {"projectId": "PROJ-1", "projectNameEn": "My Space", "role": "OWNER"}]}}
SEAT = {"code": 0, "message": "", "data": {
    "projectId": "PROJ-1", "planCode": "team_max", "planName": "Max", "currentPeriodEnd": "2026-11-15T23:59:59",
    "settlementType": "ONE_TIME", "autoRenew": False, "expired": False,
    "seat": {"seatId": "ST-CELFOG93N", "seatStatus": "ASSIGNED", "creditsTotal": 82000000000,
             "creditsUsed": 5178060, "historyCreditsUsed": 0, "usedPercent": 0.01,
             "historyUsedPercent": 0.0, "nextResetTime": None}}}
BALANCE = {"code": 0, "message": "", "data": {"balance": "12.50", "currency": "CNY"}}
JAR = {"api-platform_serviceToken": "tok", "api-platform_ph": "ph", "api-platform_slh": "slh", "userId": "42"}
CRED = Credential("chrome:Default:abcd", {"cookies": JAR})


def api(**answers):
    calls = []

    def get(url, headers, now):
        calls.append((url, headers))
        for tail, ans in answers.items():
            if url.endswith(tail.replace("__", "/")):
                return ans
        raise AssertionError(url)
    get.calls = calls
    return get


def ok(body):
    return Answer(body, 200, None)


class Read(unittest.TestCase):
    def test_the_seat_is_a_monthly_window_used_by_its_spent_share_resetting_at_period_end_beijing(self):
        get = api(projects=ok(PROJECTS), seat=ok(SEAT), balance=ok(BALANCE))
        r = mimo.read(CRED, NOW, get)
        self.assertEqual((r["status"], r["plan"]), ("ok", "Max"))
        (w,) = r["limits"]
        self.assertEqual((w["name"], w["window_minutes"], w["role"], w["scope"]), ("month", 43200, "month", "My Space"))
        self.assertAlmostEqual(w["used_at_least"], 5178060 / 82000000000)
        self.assertEqual(w["resets_at"], "2026-11-15T15:59:59+00:00")
        self.assertIn("/project/PROJ-1/teamTokenPlan/my/seat", get.calls[1][0])
        self.assertEqual(get.calls[0][1]["Cookie"],
                         "api-platform_serviceToken=tok; api-platform_ph=ph; api-platform_slh=slh; userId=42")

    def test_the_vendors_next_reset_wins_over_the_period_end(self):
        seat = {"code": 0, "data": dict(SEAT["data"], seat=dict(SEAT["data"]["seat"], nextResetTime="2026-10-20T00:00:00"))}
        r = mimo.read(CRED, NOW, api(projects=ok(PROJECTS), seat=ok(seat), balance=ok(BALANCE)))
        self.assertEqual(r["limits"][0]["resets_at"], "2026-10-19T16:00:00+00:00")

    def test_the_pay_as_you_go_balance_is_credit_in_its_currency(self):
        r = mimo.read(CRED, NOW, api(projects=ok(PROJECTS), seat=ok(SEAT), balance=ok(BALANCE)))
        self.assertEqual((r["credits"]["balance"], r["credits"]["currency"], r["credits"]["enabled"]),
                         (12.5, "CNY", True))

    def test_no_cookie_is_unread_and_asks_nothing(self):
        get = api()
        r = mimo.read(Credential("chrome:Default:x", {"cookies": None}), NOW, get)
        self.assertEqual((r["status"], r["why"]), ("unread", "no-credential"))
        self.assertEqual(get.calls, [])

    def test_an_http_refusal_is_a_refusal(self):
        r = mimo.read(CRED, NOW, api(projects=Answer(None, 401, "http-401")))
        self.assertEqual(r["status"], "refused")

    def test_a_nonzero_code_is_the_vendors_error_not_an_empty_login(self):
        r = mimo.read(CRED, NOW, api(projects=ok({"code": 401, "message": "login", "data": {}})))
        self.assertEqual((r["status"], r["why"]), ("unread", "vendor-error"))

    def test_a_login_with_no_seat_and_no_balance_says_so(self):
        unseated = {"code": 0, "data": dict(SEAT["data"], seat=None)}
        r = mimo.read(CRED, NOW, api(projects=ok(PROJECTS), seat=ok(unseated), balance=ok({"code": 1})))
        self.assertEqual((r["status"], r["why"]), ("unread", "no-seat"))

    def test_an_expired_plan_holds_its_window(self):
        seat = {"code": 0, "data": dict(SEAT["data"], expired=True)}
        (w,) = mimo.read(CRED, NOW, api(projects=ok(PROJECTS), seat=ok(seat), balance=ok(BALANCE)))["limits"]
        self.assertEqual((w["held"], w["held_why"]), (True, "expired"))

    def test_a_second_seated_project_is_its_own_window(self):
        two = {"code": 0, "data": {"projects": PROJECTS["data"]["projects"] + [{"projectId": "PROJ-2", "projectNameEn": "Team"}]}}
        r = mimo.read(CRED, NOW, api(projects=ok(two), seat=ok(SEAT), balance=ok(BALANCE)))
        self.assertEqual([(w["name"], w["scope"]) for w in r["limits"]], [("month", "My Space"), ("month:2", "Team")])

    def test_whoami_is_the_logins_user_id_cookie_without_a_request(self):
        self.assertEqual(mimo.whoami(CRED, NOW, api()), ("42", None))

    def test_it_shows_as_mimo(self):
        r = mimo.read(CRED, NOW, api(projects=ok(PROJECTS), seat=ok(SEAT), balance=ok(BALANCE)))
        out = show.render([dict(r, names=[])], NOW)
        self.assertIn("MiMo", out)
        self.assertIn("CNY 12.50 balance", out)


class Discover(unittest.TestCase):
    def store(self) -> Path:
        root = Path(scratch.mkdtemp())
        (root / "Default").mkdir()
        con = sqlite3.connect(root / "Default" / "Cookies")
        con.execute("create table cookies (host_key text, name text, encrypted_value blob)")
        con.execute("create table meta (key text, value text)")
        con.execute("insert into meta values ('version', '24')")
        for host, name in mimo.COOKIES:
            con.execute("insert into cookies values (?, ?, ?)", (host, name, b"v10" + name.encode()))
        con.execute("insert into cookies values ('.other.com', 'userId', x'00')")
        con.commit()
        con.close()
        return root / "Default"

    def test_every_cookie_is_found_on_its_own_host(self):
        got, ver = mimo._cookies(self.store())
        self.assertEqual((sorted(got), ver), (sorted(n for _, n in mimo.COOKIES), 24))
        self.assertEqual(got["userId"], b"v10userId")

    def test_discovery_asks_no_keychain_and_a_missing_cookie_reads_none(self):
        p = self.store()
        with mock.patch.object(chrome_cookies, "profiles", return_value=[p]), \
                mock.patch.object(mimo.sys, "platform", "darwin"), \
                mock.patch.object(chrome_cookies, "password", side_effect=AssertionError("keychain")):
            (cred,) = mimo.discover()
        self.assertTrue(cred.account.startswith("chrome:Default:"))
        with mock.patch.object(chrome_cookies, "decrypt", side_effect=lambda b, v: None if b == b"v10userId" else "x"):
            self.assertIsNone(cred.secret["cookies"])


if __name__ == "__main__":
    unittest.main()
