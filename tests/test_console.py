"""Anthropic API credits on the Console's sign-in cookie. No network, no keychain, no Chrome."""

from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from unlimited import identities, show
from unlimited.adapters import anthropic, anthropic_console as console
from unlimited.credential import Credential
from unlimited.transport import Answer

import scratch

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
ACCOUNT, ORG = "f230fcc1-0000-0000-0000-000000000000", "ef187d50-0000-0000-0000-000000000000"
BOOT = {"account": {"uuid": ACCOUNT, "memberships": [
    {"organization": {"uuid": "chat-org", "capabilities": ["claude_max"]}},
    {"organization": {"uuid": ORG, "capabilities": ["api", "api_individual"]}}]}}
CREDITS = {"balance": {"money": None, "credits": {"amount_minor": 19950, "exponent": 2}}, "tranches": [],
           "promo_tranches": [
               {"remaining_amount_minor_units": 19950, "expires_at": "2026-11-04T00:00:00Z"},
               # A spent grant's expiry says nothing about the credit still held.
               {"remaining_amount_minor_units": 0, "expires_at": "2026-10-12T00:00:00Z"}]}
CRED = Credential("chrome:Default", {"session": "sk-ant-sid02-x"})


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
    def test_the_api_organisations_balance_is_read_on_the_cookie_with_its_expiry(self):
        get = api(bootstrap=ok(BOOT), prepaid__credits=ok(CREDITS))
        r = console.read(CRED, NOW, get)
        self.assertEqual(r["status"], "ok")
        self.assertEqual((r["credits"]["balance"], r["credits"]["currency"], r["credits"]["enabled"]),
                         (199.5, "USD", True))
        self.assertEqual(r["credits"]["expires_at"], "2026-11-04T00:00:00+00:00")
        # The organisation asked is the API one, not the chat subscription's.
        self.assertIn(f"/organizations/{ORG}/prepaid/credits", get.calls[1][0])
        self.assertEqual(get.calls[1][1], {"Cookie": "sessionKey=sk-ant-sid02-x"})

    def test_no_cookie_is_unread_and_asks_nothing(self):
        get = api()
        r = console.read(Credential("chrome:Default", {"session": None}), NOW, get)
        self.assertEqual((r["status"], r["why"]), ("unread", "no-credential"))
        self.assertEqual(get.calls, [])

    def test_an_expired_session_is_a_refusal_not_a_zero_balance(self):
        r = console.read(CRED, NOW, api(bootstrap=Answer(None, 403, "http-403")))
        self.assertEqual((r["status"], r["credits"]), ("refused", None))

    def test_a_login_with_no_api_organisation_says_so(self):
        boot = {"account": {"uuid": ACCOUNT, "memberships": BOOT["account"]["memberships"][:1]}}
        r = console.read(CRED, NOW, api(bootstrap=ok(boot)))
        self.assertEqual((r["status"], r["why"]), ("unread", "no-api-organization"))

    def test_whoami_is_the_logins_own_id(self):
        self.assertEqual(console.whoami(CRED, NOW, api(bootstrap=ok(BOOT)))[0], ACCOUNT)

    def test_a_balance_shows_with_its_expiry_and_no_empty_windows_line(self):
        r = console.read(CRED, NOW, api(bootstrap=ok(BOOT), prepaid__credits=ok(CREDITS)))
        out = show.render([dict(r, names=["account1"])], NOW)
        self.assertIn("USD 199.50 balance · on · expires in 24d 12h", out)
        self.assertNotIn("no usage windows reported", out)


class Linking(unittest.TestCase):
    def test_the_cookie_takes_the_names_of_the_claude_code_sign_in_to_the_same_login(self):
        d = Path(scratch.mkdtemp())
        identities.path(console.VENDOR, d).parent.mkdir(parents=True)
        identities.path(console.VENDOR, d).write_text('{"chrome:Default": {"account": "%s"}}' % ACCOUNT)
        with mock.patch.object(anthropic, "names", return_value={ACCOUNT: ["account1"], "other": ["account2"]}), \
                mock.patch.object(identities, "default_dir", return_value=d):
            self.assertEqual(console.names(), {"chrome:Default": ["account1"]})


class Decrypt(unittest.TestCase):
    def encrypt(self, plain: bytes) -> bytes:
        key = hashlib.pbkdf2_hmac("sha1", b"pw", b"saltysalt", 1003, 16)
        out = subprocess.run(["openssl", "enc", "-aes-128-cbc", "-K", key.hex(), "-iv", "20" * 16],
                             input=plain, capture_output=True, check=True)
        return b"v10" + out.stdout

    def test_a_current_store_drops_the_host_digest_before_the_value(self):
        blob = self.encrypt(hashlib.sha256(b".platform.claude.com").digest() + b"sk-ant-sid02-x")
        with mock.patch.object(console, "_password", return_value="pw"):
            self.assertEqual(console._decrypt(blob, 24), "sk-ant-sid02-x")

    def test_an_older_store_has_no_digest(self):
        with mock.patch.object(console, "_password", return_value="pw"):
            self.assertEqual(console._decrypt(self.encrypt(b"sk-ant-sid02-x"), 23), "sk-ant-sid02-x")

    def test_a_refused_keychain_reads_no_cookie(self):
        with mock.patch.object(console, "_password", return_value=None):
            self.assertIsNone(console._decrypt(self.encrypt(b"x"), 24))

    def test_the_cookie_is_found_in_a_profiles_store(self):
        root = Path(scratch.mkdtemp())
        (root / "Default").mkdir()
        con = sqlite3.connect(root / "Default" / "Cookies")
        con.execute("create table cookies (host_key text, name text, encrypted_value blob)")
        con.execute("create table meta (key text, value text)")
        con.execute("insert into meta values ('version', '24')")
        con.execute("insert into cookies values ('.platform.claude.com', 'sessionKey', x'763130')")
        con.execute("insert into cookies values ('.claude.ai', 'sessionKey', x'00')")
        con.commit()
        con.close()
        self.assertEqual(console._row(root / "Default"), (b"v10", 24))


if __name__ == "__main__":
    unittest.main()
