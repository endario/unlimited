"""Opaque policy keys preserve factual identity."""
import copy
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from unlimited import catalog, cli, identity, incentives
import scratch

NOW = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)


class Admission(unittest.TestCase):
    def test_identity_string_and_dictionary_whitespace_and_duplicates(self):
        self.assertEqual(identity.names("  "), set())
        self.assertEqual(identity.names(" W "), {" W "})
        self.assertEqual(identity.ordered_names({"account": " ", "names": ["a", "a", "", None]}),
                         [" ", "a", "a"])

class Keys(unittest.TestCase):
    def test_shared_constructors_keep_literal_order_and_raw_selectors(self):
        self.assertTrue(callable(getattr(identity, "policy_keys", None)), "missing shared policy_keys")
        self.assertTrue(callable(getattr(identity, "policy_key", None)), "missing shared policy_key")
        self.assertEqual(identity.POLICY_PROJECTION, "unlimited-policy-keys-v1")
        self.assertEqual(identity.policy_keys("openai", {"account": "W", "names": ["A", "B", "A"]}),
                         ["openai", "openai/W", "openai/A", "openai/B", "openai/A"])
        self.assertEqual(identity.policy_keys("openai", None), ["openai"])
        self.assertEqual(identity.policy_keys("openai", " "), ["openai"])
        self.assertEqual(identity.policy_keys("openai", " W "), ["openai", "openai/ W "])
        self.assertEqual(identity.policy_keys("openai", {"account": " ", "names": ["", None, "A"]}),
                         ["openai", "openai/ ", "openai/A"])
        self.assertEqual(identity.policy_key("openai", "stored-alias"), "openai/stored-alias")
        self.assertEqual(identity.policy_key("openai"), "openai")

class Output(unittest.TestCase):
    def test_reading_projection_recomputes_forged_fields_without_changing_facts(self):
        self.assertTrue(callable(getattr(identity, "project_reading", None)), "missing project_reading")
        raw = {"schema": 1, "vendor": "openai", "account": "W", "names": ["A", "B"],
               "status": "unread", "limits": [], "extra": {"raw": True},
               "policy_projection": "forged", "policy_keys": ["other"]}
        before = copy.deepcopy(raw)
        shown = identity.project_reading(raw)
        self.assertEqual(shown["policy_projection"], "unlimited-policy-keys-v1")
        self.assertEqual(shown["policy_keys"], ["openai", "openai/W", "openai/A", "openai/B"])
        self.assertEqual({k: v for k, v in shown.items() if not k.startswith("policy_")},
                         {k: v for k, v in before.items() if not k.startswith("policy_")})
        self.assertEqual(raw, before)
        self.assertIsNot(shown, raw)
        for vendor in (None, "", " ", 42, [], {}):
            with self.subTest(vendor=vendor):
                self.assertEqual(identity.project_reading({"vendor": vendor, "account": "W"})["policy_keys"], [])
        self.assertEqual(identity.project_reading({"account": "W"})["policy_keys"], [])
        self.assertEqual(identity.project_reading({"vendor": "custom", "account": None})["policy_keys"], ["custom"])

    def test_annotation_projection_survives_advisory_loading(self):
        rows = [{"vendor": "openai", "account": "W", "names": ["A"], "status": "unread"},
                {"vendor": "openai", "account": None, "names": []}]
        before = copy.deepcopy(rows)
        cat = catalog.Catalog({"tiers": [], "offerings": []})
        group = {"target": "openai", "multiplier": 2, "activated_at": NOW.isoformat(),
                 "until": (NOW + timedelta(hours=1)).isoformat()}
        for mode in ("empty", "success", "policy", "catalog"):
            with self.subTest(mode=mode), mock.patch.object(incentives, "read", **(
                    {"side_effect": catalog.CatalogError("private")} if mode == "policy" else
                    {"return_value": [] if mode == "empty" else [group]})), \
                    mock.patch.object(catalog, "load_metadata", **(
                        {"side_effect": catalog.CatalogError("private")} if mode == "catalog" else {"return_value": cat})):
                shown = incentives.annotate(rows, now=NOW)
                self.assertEqual([r.get("policy_keys") for r in shown],
                                 [["openai", "openai/W", "openai/A"], ["openai"]])
                self.assertTrue(all(r.get("policy_projection") == "unlimited-policy-keys-v1" for r in shown))
                self.assertEqual([{k: v for k, v in r.items() if k not in ("steering", "policy_keys", "policy_projection")}
                                  for r in shown], before)
                self.assertEqual(incentives.annotate([], now=NOW), [])
                if mode in ("policy", "catalog"):
                    self.assertEqual(shown[0]["steering"]["error"], mode + "-unavailable")
                if mode == "success":
                    self.assertEqual(shown[0]["steering"]["settings"][0]["multiplier"], 2)
        self.assertEqual(rows, before)

    def test_explicit_unavailable_catalog_keeps_projection_and_facts(self):
        raw = {"vendor": "openai", "account": "W", "names": ["A"], "status": "unread"}
        before = copy.deepcopy(raw)
        group = {"target": "openai", "multiplier": 2, "activated_at": NOW.isoformat(),
                 "until": (NOW + timedelta(hours=1)).isoformat()}
        with mock.patch.object(incentives, "read", return_value=[group]), \
                mock.patch.object(catalog, "load_metadata", side_effect=AssertionError("must not reload")):
            shown, warnings = incentives._annotate([raw], now=NOW, cat=None)
        self.assertEqual(shown[0].get("policy_projection"), "unlimited-policy-keys-v1")
        self.assertEqual(shown[0].get("policy_keys"), ["openai", "openai/W", "openai/A"])
        self.assertEqual(warnings, ["catalog-unavailable"])
        self.assertEqual(shown[0]["steering"]["error"], "catalog-unavailable")
        self.assertEqual({k: v for k, v in shown[0].items()
                          if k not in {"steering", "policy_projection", "policy_keys"}}, before)
        self.assertEqual(raw, before)

class CacheAcceptance(unittest.TestCase):
    def test_real_translation_cache_and_cli_projection_remain_transient(self):
        from unlimited import cache, transport
        from unlimited.adapters import openai
        from unlimited.credential import Credential
        root = Path(scratch.mkdtemp())
        cred = Credential("W", {"account_id": "W", "access_token": "fixture-token"})
        body = {"account_id": "W", "plan_type": "fixture", "rate_limit": {
            "allowed": True, "limit_reached": False, "primary_window": {
                "used_percent": 25, "limit_window_seconds": 604800,
                "reset_at": int((NOW + timedelta(days=4)).timestamp())}}}
        answer = transport.Answer(body, 200, None)
        calls = []
        def get(url, headers, now):
            calls.append(now)
            self.assertEqual(headers, {"Authorization": "Bearer fixture-token", "ChatGPT-Account-Id": "W"})
            return answer
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(root / "cache"),
                                          "XDG_CONFIG_HOME": str(root / "config")}), \
                mock.patch.object(openai, "discover", return_value=[cred]), \
                mock.patch.object(openai, "names", return_value={"W": ["source-A", "source-B"]}, create=True), \
                mock.patch.object(openai, "local", return_value=[]):
            def read(now, age=300):
                raw = cache.through(openai, max_age=age, clock=lambda: now, get=get)
                self.assertNotIn("policy_keys", raw[0])
                self.assertNotIn("policy_projection", raw[0])
                return raw
            first = read(NOW)
            self.assertEqual((first[0]["account"], first[0]["names"], first[0]["plan"]),
                             ("W", ["source-A", "source-B"], "fixture"))
            raw_rows = [first, read(NOW + timedelta(seconds=1))]
            self.assertEqual(len(calls), 1)
            answer = transport.Answer(None, None, "unreachable")
            raw_rows.append(read(NOW + timedelta(seconds=301)))
            self.assertEqual(raw_rows[-1][0]["taken_at"], NOW.isoformat())
            answer = transport.Answer(None, 429, "http-429", NOW + timedelta(seconds=303))
            raw_rows.append(read(NOW + timedelta(seconds=302)))
            self.assertEqual(raw_rows[-1][0]["retry_until"], "2026-10-05T12:10:02+00:00")
            raw_rows.append(read(NOW + timedelta(seconds=303), 0))
            self.assertEqual(len(calls), 3)
            for raw in raw_rows:
                with mock.patch.object(incentives, "read", return_value=[]):
                    shown = incentives.annotate(raw, now=NOW + timedelta(seconds=303))[0]
                self.assertEqual(shown.get("policy_projection"), "unlimited-policy-keys-v1")
                self.assertEqual(shown.get("policy_keys"), ["openai", "openai/W", "openai/source-A", "openai/source-B"])
                self.assertEqual({k: v for k, v in shown.items() if k not in ("steering", "policy_projection", "policy_keys")}, raw[0])
            snapshot = json.loads((root / "cache" / "unlimited" / "openai.json").read_text())
            self.assertEqual(snapshot["readings"][0]["account"], "W")
            self.assertTrue(snapshot["history"])
            self.assertNotIn("policy_projection", json.dumps(snapshot))
            self.assertNotIn("policy_keys", json.dumps(snapshot))
            self.assertEqual(snapshot["readings"][0]["names"], [])
            with mock.patch.object(cli, "datetime") as clock, \
                    mock.patch.object(transport, "get", side_effect=AssertionError("backoff must avoid request")), \
                    redirect_stdout(io.StringIO()) as out:
                clock.now.return_value = NOW + timedelta(seconds=303)
                self.assertEqual(cli.main(["read", "--vendor", "openai", "--json", "--max-age", "0"]), 0)
            self.assertEqual(json.loads(out.getvalue())[0]["policy_keys"],
                             ["openai", "openai/W", "openai/source-A", "openai/source-B"])

if __name__ == "__main__":
    unittest.main()
