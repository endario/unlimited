"""Each Xiaomi MiMo team key's seat allowance, read on the platform's sign-in cookies. No network, no
keychain, no Chrome."""

from __future__ import annotations

import os
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from unlimited import chrome_cookies, show
from unlimited.adapters import mimo
from unlimited.credential import Credential, account_of
from unlimited.transport import Answer

import scratch

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
SEAT = {"code": 0, "message": "", "data": {
    "projectId": "PROJ-1", "planCode": "team_max", "planName": "Max", "currentPeriodEnd": "2026-11-15T23:59:59",
    "settlementType": "ONE_TIME", "autoRenew": False, "expired": False,
    "seat": {"seatId": "ST-CELFOG93N", "seatStatus": "ASSIGNED", "creditsTotal": 82000000000,
             "creditsUsed": 5178060, "historyCreditsUsed": 0, "usedPercent": 0.01,
             "historyUsedPercent": 0.0, "nextResetTime": None}}}
OWNER_KEY = "ttp-AAAA" + "x" * 38 + "owner1"
MEMBER_KEY = "ttp-AAAA" + "y" * 38 + "membr2"
APIKEY = {"code": 0, "data": {"apiKey": None, "redactedApiKey": "ttp-AAAA" + "*" * 36 + "owner1"}}
OWNED = {"code": 0, "data": {"projects": [{"projectId": "PROJ-1", "projectNameEn": "My Space", "role": "OWNER"}]}}
DETAIL = {"code": 0, "data": {"projectId": "PROJ-1", "planName": "Max", "currentPeriodEnd": "2026-11-15T23:59:59",
                              "expired": False, "seatCount": 2}}
MEMBERS = {"code": 0, "data": [
    {"memberId": 256, "userId": "42", "displayName": "当前账号", "role": "OWNER",
     "seat": {"seatId": "ST-1", "seatStatus": "ASSIGNED", "creditsTotal": 82000000000, "creditsUsed": 5178060}},
    {"memberId": 259, "userId": "7", "displayName": "Ren", "role": "MEMBER",
     "seat": {"seatId": "ST-2", "seatStatus": "ASSIGNED", "creditsTotal": 82000000000, "creditsUsed": 41000000000}}]}
JAR = {"api-platform_serviceToken": "tok", "api-platform_ph": "ph", "api-platform_slh": "slh", "userId": "42"}


def key(k):
    return Credential(account_of(k), {"key": k})


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
    def setUp(self):
        self.logins = [{"cookies": JAR}]
        self.keys = [key(OWNER_KEY), key(MEMBER_KEY)]
        for name, fake in (("_logins", lambda: tuple(self.logins)), ("discover", lambda: self.keys)):
            patch = mock.patch.object(mimo, name, side_effect=fake)
            patch.start()
            self.addCleanup(patch.stop)

    def read(self, k, **answers):
        return mimo.read(key(k), NOW, api(**answers))

    def team(self, **over):
        return dict(dict(projects=ok(OWNED), apiKey=ok(APIKEY), seat=ok(SEAT), detail=ok(DETAIL),
                         members=ok(MEMBERS)), **over)

    def test_a_key_is_the_seat_whose_redacted_key_it_fits_a_monthly_window_resetting_at_period_end_utc(self):
        get = api(**self.team())
        r = mimo.read(key(OWNER_KEY), NOW, get)
        self.assertEqual((r["status"], r["plan"], r["account"]), ("ok", "Max", account_of(OWNER_KEY)))
        (w,) = r["limits"]
        self.assertEqual((w["name"], w["window_minutes"], w["role"], w["scope"]), ("month", 43200, "month", "My Space"))
        self.assertAlmostEqual(w["used_at_least"], 5178060 / 82000000000)
        self.assertEqual(w["resets_at"], "2026-11-15T23:59:59+00:00")
        self.assertIn("/project/PROJ-1/teamTokenPlan/my/seat", get.calls[-1][0])
        self.assertEqual(get.calls[0][1]["Cookie"],
                         "api-platform_serviceToken=tok; api-platform_ph=ph; api-platform_slh=slh; userId=42")

    def test_the_vendors_next_reset_wins_over_the_period_end(self):
        seat = {"code": 0, "data": dict(SEAT["data"], seat=dict(SEAT["data"]["seat"], nextResetTime="2026-10-20T00:00:00"))}
        r = self.read(OWNER_KEY, **self.team(seat=ok(seat)))
        self.assertEqual(r["limits"][0]["resets_at"], "2026-10-20T00:00:00+00:00")

    def test_the_one_key_no_sign_in_holds_is_the_one_seat_no_sign_in_holds(self):
        r = self.read(MEMBER_KEY, **self.team())
        (w,) = r["limits"]
        self.assertEqual((w["scope"], w["used_at_least"], r["plan"]), ("Ren", 0.5, "Max"))

    def test_elimination_refuses_to_guess_between_two_loose_keys(self):
        self.keys.append(key("ttp-AAAA" + "z" * 38 + "third3"))
        r = self.read(MEMBER_KEY, **self.team())
        self.assertEqual((r["status"], r["why"]), ("unread", "no-seat-for-key"))

    def test_elimination_refuses_to_guess_between_two_free_seats(self):
        third = dict(MEMBERS["data"][1], userId="8", displayName="Third")
        r = self.read(MEMBER_KEY, **self.team(members=ok({"code": 0, "data": MEMBERS["data"] + [third]})))
        self.assertEqual((r["status"], r["why"]), ("unread", "no-seat-for-key"))

    def test_a_member_sees_no_other_seat_so_a_loose_key_is_not_its_own(self):
        member = {"code": 0, "data": {"projects": [dict(OWNED["data"]["projects"][0], role="MEMBER")]}}
        r = self.read(MEMBER_KEY, **self.team(projects=ok(member)))
        self.assertEqual((r["status"], r["why"]), ("unread", "no-seat-for-key"))

    def test_a_key_that_only_shares_the_head_is_not_the_seats(self):
        self.assertFalse(mimo._fits("ttp-AAAA" + "x" * 38 + "other1", mimo._redacted(APIKEY["data"]["redactedApiKey"])))
        self.assertFalse(mimo._fits("ttp-AAAAowner1", ("ttp-AAAAo", "owner1")))

    def test_an_individual_or_pay_as_you_go_key_is_unread_and_asks_nothing(self):
        for k in ("tp-" + "a" * 40, "sk-" + "a" * 40):
            get = api()
            r = mimo.read(key(k), NOW, get)
            self.assertEqual((r["status"], r["why"], get.calls), ("unread", "not-team-key", []))

    def test_no_sign_in_and_an_undecryptable_one_are_told_apart(self):
        self.logins = []
        self.assertEqual(self.read(OWNER_KEY)["why"], "no-sign-in")
        self.logins = [{"cookies": None}]
        self.assertEqual(self.read(OWNER_KEY)["why"], "no-credential")

    def test_an_http_refusal_is_a_refusal(self):
        r = self.read(OWNER_KEY, projects=Answer(None, 401, "http-401"))
        self.assertEqual(r["status"], "refused")

    def test_a_refusal_behind_the_sign_in_is_still_a_refusal(self):
        # A blind endpoint must keep its status for backoff, not pass for a key with no seat.
        down = Answer(None, 500, "http-500")
        for over, k in ((dict(seat=down), OWNER_KEY), (dict(detail=down), MEMBER_KEY),
                        (dict(members=down), MEMBER_KEY), (dict(apiKey=down), OWNER_KEY)):
            with self.subTest(over=list(over)):
                self.assertEqual(self.read(k, **self.team(**over))["status"], "refused")

    def test_a_team_that_cannot_be_seen_stops_elimination(self):
        # The unseen team might hold a second free seat; one visible free seat proves nothing.
        two = {"code": 0, "data": {"projects": OWNED["data"]["projects"] + [
            {"projectId": "PROJ-2", "projectNameEn": "Other", "role": "OWNER"}]}}
        answers = self.team(projects=ok(two))
        answers["PROJ-2__teamTokenPlan__detail"] = Answer(None, 500, "http-500")
        answers = {"PROJ-2__teamTokenPlan__detail": answers.pop("PROJ-2__teamTokenPlan__detail"), **answers}
        self.assertEqual(self.read(MEMBER_KEY, **answers)["status"], "refused")

    def test_a_nonzero_code_is_the_vendors_error_not_an_empty_login(self):
        r = self.read(OWNER_KEY, projects=ok({"code": 401, "message": "login", "data": {}}))
        self.assertEqual((r["status"], r["why"]), ("unread", "vendor-error"))

    def test_a_matched_key_whose_seat_is_unassigned_says_so(self):
        unseated = {"code": 0, "data": dict(SEAT["data"], seat=None)}
        r = self.read(OWNER_KEY, **self.team(seat=ok(unseated)))
        self.assertEqual((r["status"], r["why"]), ("unread", "no-seat"))

    def test_an_expired_plan_holds_its_window(self):
        seat = {"code": 0, "data": dict(SEAT["data"], expired=True)}
        (w,) = self.read(OWNER_KEY, **self.team(seat=ok(seat)))["limits"]
        self.assertEqual((w["held"], w["held_why"]), (True, "expired"))

    def test_whoami_is_the_keys_seat(self):
        self.assertEqual(mimo.whoami(key(OWNER_KEY), NOW, api(**self.team()))[0], "ST-CELFOG93N")
        self.assertEqual(mimo.whoami(key(MEMBER_KEY), NOW, api(**self.team()))[0], "ST-2")

    def test_it_shows_as_mimo(self):
        r = self.read(OWNER_KEY, **self.team())
        self.assertIn("MiMo", show.render([dict(r, names=["claude-mimo-1"])], NOW))


class Keys(unittest.TestCase):
    def test_each_numbered_or_plain_key_file_is_its_own_account_named_by_its_stem(self):
        home = Path(scratch.mkdtemp())
        (home / ".config").mkdir()
        for stem, line in (("claude-mimo-1", f"MIMO_1_API_KEY={OWNER_KEY}"), ("claude-mimo-2", f"MIMO_2_API_KEY={MEMBER_KEY}"),
                           ("claude-mimo", f"MIMO_API_KEY={OWNER_KEY}"), ("claude-mimo-x", "MIMO_X_API_KEY=nope")):
            (home / ".config" / f"{stem}.env").write_text(line + "\n")
        with mock.patch.object(Path, "home", return_value=home), mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(mimo.names(), {account_of(OWNER_KEY): ["claude-mimo-1", "claude-mimo"],
                                            account_of(MEMBER_KEY): ["claude-mimo-2"]})
            self.assertEqual(sorted(c.secret["key"] for c in mimo.discover()), sorted([OWNER_KEY, MEMBER_KEY]))


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

    def test_finding_sign_ins_asks_no_keychain_and_a_missing_cookie_reads_none(self):
        p = self.store()
        mimo._logins.cache_clear()
        self.addCleanup(mimo._logins.cache_clear)
        with mock.patch.object(chrome_cookies, "profiles", return_value=[p]), \
                mock.patch.object(mimo.sys, "platform", "darwin"), \
                mock.patch.object(chrome_cookies, "password", side_effect=AssertionError("keychain")):
            (jar,) = mimo._logins()
        with mock.patch.object(chrome_cookies, "decrypt", side_effect=lambda b, v: None if b == b"v10userId" else "x"):
            self.assertIsNone(jar["cookies"])


if __name__ == "__main__":
    unittest.main()
