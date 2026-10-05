"""Opaque exclusion keys preserve factual identity and existing enforcement."""
import copy
import io
import json
import os
import unittest
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from unlimited import catalog, choice, cli, identity, incentives
from unlimited.verdict import verdict
import scratch

NOW = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)


def decide(reading, off):
    return verdict(reading, model_scope=None, now=NOW, work=timedelta(),
                   max_age=timedelta(), off=off)


class Admission(unittest.TestCase):
    def test_identity_string_and_dictionary_whitespace_and_duplicates(self):
        self.assertEqual(identity.names("  "), set())
        self.assertEqual(identity.names(" W "), {" W "})
        self.assertEqual(identity.ordered_names({"account": " ", "names": ["a", "a", "", None]}),
                         [" ", "a", "a"])

    def test_off_precedes_reading_admission_and_deadline_order(self):
        reading = {"vendor": "openai", "account": "W", "names": ["B", "A", "B"]}
        policy = {"openai": "vendor-deadline", "openai/W": "account-deadline",
                  "openai/A": "A-deadline", "openai/B": "B-deadline"}
        for key, until in (("openai", "vendor-deadline"), ("openai/W", "account-deadline"),
                           ("openai/B", "B-deadline")):
            self.assertEqual(decide(reading, policy), {"state": "excluded", "reason": "off", "until": until})
            del policy[key]

    def test_missing_and_nonstring_vendor_existing_verdict_behavior(self):
        for reading, key in (({"account": "W"}, "None/W"),
                             ({"vendor": 42, "account": "W"}, "42/W"),
                             ({"vendor": "", "account": "W"}, "/W")):
            self.assertEqual(decide(reading, {key: "deadline"}),
                             {"state": "excluded", "reason": "off", "until": "deadline"})
            self.assertEqual(decide(reading, {}), {"state": "unread", "reason": "not-ok"})

    def test_off_policy_registry_live_filter_and_last_write(self):
        path = Path(scratch.mkdtemp()) / "switches.json"
        catalog.write_switches([{"target": "openai", "account": " W ", "until": None},
                                {"target": "openai", "account": " W ", "until": (NOW + timedelta(hours=1)).isoformat()},
                                {"target": "codex", "account": "W", "until": None},
                                {"target": "unknown", "until": None},
                                {"target": "openai", "until": NOW.isoformat()}], path)
        self.assertEqual(catalog.off_policy(NOW, path), {"openai/ W ": "2026-10-05T13:00:00+00:00"})

    def test_choice_manual_provider_switch_keeps_explicit_binding_guards(self):
        cat = catalog.Catalog({"tiers": ["standard"],
                               "models": {"m": {"provider": "manual", "tiers": ["standard"]}},
                               "offerings": [{"id": "route", "model": "m", "vendor": "openai"}]},
                              off=[{"target": "manual", "account": "alias", "until": None}])
        candidate = {"model": "route"}
        for binding, expected in ((None, False), ("", False), (" ", False), ("alias", True),
                                  ({"account": "W", "names": ["alias"]}, True),
                                  ({"account": "other", "names": []}, False)):
            self.assertEqual(choice._account_off(cat, candidate, {"route": binding}, NOW), expected)
        self.assertFalse(choice._account_off(cat, {"model": "absent"}, {"absent": "alias"}, NOW))
        cat.off[0]["until"] = NOW.isoformat()
        self.assertFalse(choice._account_off(cat, candidate, {"route": "alias"}, NOW))


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

    def test_switch_keys_admit_usage_vendor_only_without_expiry_filter(self):
        self.assertTrue(callable(getattr(catalog, "switch_policy_keys", None)), "missing switch_policy_keys")
        self.assertEqual(catalog.switch_policy_keys({"target": "openai", "account": " W ", "until": NOW.isoformat()}),
                         ["openai/ W "])
        self.assertEqual(catalog.switch_policy_keys({"target": "openai"}), ["openai"])
        for target in ("unknown", "codex", "gpt-6.1-sol", "codex:gpt-6.1-sol"):
            self.assertEqual(catalog.switch_policy_keys({"target": target, "account": "W"}), [])


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

    def test_switch_projection_preserves_raw_clear_selector_and_ignores_forgery(self):
        self.assertTrue(callable(getattr(catalog, "project_switch", None)), "missing project_switch")
        raw = {"target": "openai", "account": "stored-alias", "until": None, "why": "note", "extra": 7}
        before = copy.deepcopy(raw)
        self.assertEqual(catalog.project_switch(raw), {**raw, "policy_projection": "unlimited-policy-keys-v1",
                                                      "policy_keys": ["openai/stored-alias"]})
        self.assertEqual(raw, before)
        self.assertEqual(catalog.project_switch({"target": "codex", "policy_keys": ["openai"],
                                                "policy_projection": "forged"})["policy_keys"], [])

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

    def test_cli_switch_json_list_mutations_and_exact_clear_do_not_persist_projection(self):
        root = Path(scratch.mkdtemp())
        def command(argv):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.main(argv)
            return code, json.loads(out.getvalue()) if out.getvalue() else None
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(root)}), \
                mock.patch.object(cli, "datetime") as clock:
            clock.now.return_value = NOW
            path = catalog.switches_path()
            catalog.write_switches([{"target": "codex", "until": None, "why": "route only"},
                                    {"target": "openai", "account": "expired", "until": NOW.isoformat()}])
            code, listed = command(["off", "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(listed, [{"target": "codex", "until": None, "why": "route only",
                                      "policy_projection": "unlimited-policy-keys-v1", "policy_keys": []}])
            code, changed = command(["off", "openai", "--account", "stored-alias", "--why", "raw note", "--for", "1h", "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(changed[-1], {"target": "openai", "account": "stored-alias", "why": "raw note",
                                          "until": "2026-10-05T13:00:00+00:00",
                                          "policy_projection": "unlimited-policy-keys-v1", "policy_keys": ["openai/stored-alias"]})
            stored = path.read_bytes()
            self.assertNotIn(b"policy_", stored)
            self.assertEqual(command(["on", "openai", "--account", "W", "--json"]), (1, None))
            self.assertEqual(path.read_bytes(), stored)
            self.assertEqual(command(["off", "unknown-target", "--json"]), (1, None))
            self.assertEqual(path.read_bytes(), stored)
            self.assertEqual(command(["on", "openai", "--account", "stored-alias", "--json"]), (0, listed))
            self.assertNotIn(b"policy_", path.read_bytes())


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

    def test_literal_intersection_matrix_matches_verdict_without_using_incoming_keys(self):
        reading = {"vendor": "openai", "account": "W", "names": ["source-A", "source-B"],
                   "policy_keys": ["anthropic", "openai/unmatched"], "policy_projection": "forged"}
        self.assertTrue(callable(getattr(identity, "project_reading", None)), "missing reading projection")
        projected = identity.project_reading(reading)
        self.assertEqual(projected["policy_keys"], ["openai", "openai/W", "openai/source-A", "openai/source-B"])
        for target, account, expected in (("openai", None, True), ("openai", "W", True),
                ("openai", "source-A", True), ("openai", "source-B", True),
                ("openai", "unmatched", False), ("anthropic", "W", False), ("codex", None, False)):
            switch = {"target": target, "account": account, "until": None}
            keys = catalog.project_switch(switch)["policy_keys"]
            self.assertEqual(bool(set(projected["policy_keys"]) & set(keys)), expected)
            off = {key: "literal-deadline" for key in catalog.switch_policy_keys(switch)}
            self.assertEqual(decide(reading, off), {"state": "excluded", "reason": "off", "until": "literal-deadline"}
                             if expected else {"state": "unread", "reason": "not-ok"})
        cat = catalog.Catalog({"tiers": ["standard"], "models": {"m": {"provider": "codex", "tiers": ["standard"]}},
                               "offerings": [{"id": "r", "model": "m", "vendor": "openai"}]},
                              off=[{"target": "codex", "until": None}])
        self.assertEqual(cat.routes(NOW), [])
        self.assertEqual(catalog.project_switch(cat.off[0])["policy_keys"], [])
        self.assertEqual(decide(reading, {}), {"state": "unread", "reason": "not-ok"})


if __name__ == "__main__":
    unittest.main()
