"""Canonical source-local policy export, separate from quota evidence."""

import copy
import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import unlimited
from unlimited import catalog, cli, incentives, schema, transport
from unlimited.credential import Credential

import scratch

NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
ROUTE = "gpt-6.1-sol"


def setting(target="openai", factor=10, *, account=None, hours=2, activated=None):
    return {"target": target, "multiplier": factor,
            "activated_at": (activated or NOW).isoformat(),
            "until": (NOW + timedelta(hours=hours)).isoformat(),
            **({"account": account} if account is not None else {})}


def binding(account="a", hours=1, *, vendor="openai", names=()):
    return {"vendor": vendor, "account": account, "names": list(names),
            "until": (NOW + timedelta(hours=hours)).isoformat()}


def reset_group(bindings, *, account=None):
    return {"target": "openai", "multiplier": 10, "activated_at": NOW.isoformat(),
            "bindings": bindings, **({"account": account} if account is not None else {})}


class Context(unittest.TestCase):
    def setUp(self):
        self.cat = catalog.load_metadata(Path(scratch.mkdtemp()) / "absent.toml")
        self.reading = dict(schema.reading("openai", "a", NOW - timedelta(minutes=5), "ok", limits=[
            schema.limit("seven_day", window_minutes=10080, used_at_least=.25,
                         resets_at=NOW + timedelta(hours=3), held=False)]), names=["private-alias"])

    def export(self, groups, *, reading=None, now=NOW):
        return incentives.overlay([self.reading if reading is None else reading], self.cat, groups, now)[0]["steering"]

    def test_export_preserves_activation_order_identical_rules_and_canonical_selector(self):
        groups = [setting(ROUTE, 2),
                  setting("codex:" + ROUTE, 3, activated=NOW + timedelta(seconds=1)),
                  setting(ROUTE, 2, activated=NOW + timedelta(seconds=2)),
                  setting(ROUTE, 2, activated=NOW + timedelta(seconds=2)),
                  setting("openai", 1, account="private-alias", hours=1)]
        before = copy.deepcopy((self.reading, groups))
        ctx = self.export(groups)
        self.assertEqual((ctx.get("v"), ctx.get("vendor"), ctx.get("account")), (1, "openai", "a"))
        self.assertEqual(ctx["observed_at"], NOW.isoformat())
        self.assertEqual([s["target"] for s in ctx["settings"]],
                         [ROUTE, "codex:" + ROUTE, ROUTE, ROUTE, "openai"])
        self.assertEqual([s["activated_at"] for s in ctx["settings"]],
                         [g["activated_at"] for g in groups])
        self.assertEqual(ctx["settings"][-1]["account"], "a")
        self.assertEqual({r["account"] for r in ctx["routes"]}, {"a"})
        self.assertNotIn("private-alias", json.dumps(ctx))
        self.assertEqual((self.reading, groups), before)

    def test_losing_settings_and_neutral_override_survive_future_expiry(self):
        groups = [setting("openai", 10, hours=3), setting(ROUTE, 5, hours=2),
                  setting("openai", 1, account="private-alias", hours=1)]
        ctx = self.export(groups)
        self.assertEqual([s["multiplier"] for s in ctx["settings"]], [10, 5, 1])
        self.assertEqual([s.get("activated_at") for s in ctx["settings"]], [NOW.isoformat()] * 3)
        for minutes, want in ((0, 1), (59, 1), (60, 5), (119, 5), (120, 10), (179, 10), (180, 1)):
            with self.subTest(minutes=minutes):
                now = NOW + timedelta(minutes=minutes)
                got = incentives.resolve(self.cat, ctx["settings"], {ROUTE: {"account": "a", "names": []}}, now)
                self.assertEqual(got.factors[ROUTE], want)
        self.assertEqual(ctx["settings"][-1]["activated_at"], NOW.isoformat())

    def test_duplicate_live_export_horizon_is_same_vendor_canonical_account_only(self):
        groups = [reset_group([binding(hours=1, names=["private-alias"]),
                               binding(hours=3, names=["different-alias"]),
                               binding("b", 8, names=["private-alias"]),
                               binding(hours=9, vendor="anthropic", names=["private-alias"])])]
        before = copy.deepcopy(groups)
        ctx = self.export(groups)
        self.assertEqual(ctx["settings"][0]["until"], (NOW + timedelta(hours=3)).isoformat())
        self.assertIsNone(ctx["settings"][0].get("account"))
        self.assertNotIn("bindings", ctx["settings"][0])
        self.assertEqual({r["until"] for r in ctx["routes"]}, {(NOW + timedelta(hours=3)).isoformat()})
        raw = incentives.resolve(self.cat, groups, {ROUTE: {"account": "a", "names": []}}, NOW)
        self.assertEqual(raw.winners[ROUTE]["until"], (NOW + timedelta(hours=1)).isoformat())
        for minutes, want in ((0, 10), (59, 10), (60, 10), (61, 10), (179, 10), (180, 1), (181, 1)):
            with self.subTest(minutes=minutes):
                now = NOW + timedelta(minutes=minutes)
                for policy in (groups, incentives._current(groups, now), ctx["settings"]):
                    got = incentives.resolve(self.cat, policy, {ROUTE: {"account": "a", "names": []}}, now)
                    self.assertEqual(got.factors[ROUTE], want)
        self.assertEqual(groups, before)
        encoded = json.dumps(ctx)
        for secret in ("private-alias", "different-alias", '"b"', "anthropic", "bindings"):
            self.assertNotIn(secret, encoded)

    def test_duplicate_horizon_keeps_original_explicit_alias_constraint(self):
        groups = [reset_group([binding(hours=1, names=["private-alias"]),
                               binding(hours=3, names=["different-alias"]),
                               binding("b", 8, names=["private-alias"])], account="private-alias")]
        ctx = self.export(groups)
        self.assertEqual(ctx["settings"][0]["until"], (NOW + timedelta(hours=1)).isoformat())
        self.assertEqual(ctx["settings"][0]["account"], "a")
        self.assertEqual({r["account"] for r in ctx["routes"]}, {"a"})
        for minutes, want in ((0, 10), (59, 10), (60, 1), (61, 1), (179, 1), (180, 1), (181, 1)):
            with self.subTest(minutes=minutes):
                now = NOW + timedelta(minutes=minutes)
                for policy, names in ((groups, ["private-alias"]),
                                      (incentives._current(groups, now), ["private-alias"]),
                                      (ctx["settings"], [])):
                    got = incentives.resolve(self.cat, policy, {ROUTE: {"account": "a", "names": names}}, now)
                    self.assertEqual(got.factors[ROUTE], want)
        self.assertNotIn("private-alias", json.dumps(ctx))
        self.assertNotIn("different-alias", json.dumps(ctx))

    def test_fixed_expiry_is_not_renewed_by_a_later_export(self):
        groups = [setting(hours=3), reset_group([binding(hours=1), binding(hours=2)])]
        first = self.export(groups)
        later = self.export(groups, now=NOW + timedelta(minutes=61))
        self.assertEqual([s["until"] for s in first["settings"]],
                         [(NOW + timedelta(hours=3)).isoformat(), (NOW + timedelta(hours=2)).isoformat()])
        self.assertEqual(later["settings"], first["settings"])
        self.assertEqual(later["observed_at"], (NOW + timedelta(minutes=61)).isoformat())
        self.assertEqual(self.reading["taken_at"], (NOW - timedelta(minutes=5)).isoformat())
        self.assertEqual(self.export(groups, now=NOW + timedelta(hours=3))["settings"], [])

    def test_null_identity_exports_only_unbound_duration_even_with_aliases(self):
        groups = [setting(), setting(account="private-alias"),
                  reset_group([binding(names=["private-alias"])])]
        for account in (None, "", "  ", True):
            with self.subTest(account=account):
                ctx = self.export(groups, reading=dict(self.reading, account=account))
                self.assertIsNone(ctx.get("account"))
                self.assertEqual(ctx["settings"], [{**setting(), "account": None}])
                self.assertTrue(all(r["account"] is None for r in ctx["routes"]))
                self.assertNotIn("private-alias", json.dumps(ctx))

    def test_reused_alias_does_not_export_another_accounts_reset_binding(self):
        ctx = self.export([reset_group([binding("old", names=["private-alias"])])])
        self.assertEqual(ctx["settings"], [])
        self.assertEqual(ctx["routes"], [])
        self.assertEqual(ctx.get("account"), "a")

    def test_unknown_targets_are_applicability_filtered_without_private_labels(self):
        groups = [setting(), setting("unknown-unbound"),
                  setting("unknown-local", account="private-alias"),
                  setting("unknown-other", account="private-other"),
                  {**reset_group([binding(names=["private-alias"])]), "target": "unknown-reset-local"},
                  {**reset_group([binding("b", names=["private-alias"])]), "target": "unknown-reset-other"},
                  {**reset_group([binding(vendor="anthropic")]), "target": "unknown-other-vendor"},
                  setting("expired-unknown", hours=-1)]
        ctx = self.export(groups)
        self.assertEqual(ctx.get("unresolved"), ["unknown-unbound", "unknown-local", "unknown-reset-local"])
        self.assertEqual([s["target"] for s in ctx["settings"]], ["openai"])
        for private in ("private-alias", "private-other", "unknown-other", "unknown-reset-other",
                        "unknown-other-vendor", "expired-unknown"):
            self.assertNotIn(private, json.dumps(ctx))

    def test_shared_account_readings_keep_each_vendor_policy_separate(self):
        rows = incentives.overlay([self.reading, dict(self.reading, vendor="anthropic")], self.cat,
                                  [setting(), setting("anthropic", 2)], NOW)
        self.assertEqual([r["steering"]["settings"][0]["multiplier"] for r in rows], [10, 2])
        self.assertEqual([r["steering"].get("vendor") for r in rows], ["openai", "anthropic"])

    def annotate(self, readings, *, now=NOW):
        self.assertTrue(callable(getattr(incentives, "annotate", None)), "shared annotation API is missing")
        return incentives.annotate(readings, now=now)

    def test_public_annotation_returns_plain_list_for_success_empty_and_failure(self):
        for groups in ([setting()], []):
            for readings in ([self.reading], []):
                with self.subTest(groups=groups, readings=readings), \
                        mock.patch.object(incentives, "read", return_value=groups), \
                        mock.patch.object(catalog, "load_metadata", return_value=self.cat):
                    rows = self.annotate(readings)
                    self.assertIs(type(rows), list)
                    self.assertEqual([self.facts_only(row) for row in rows], readings)
        for loader in ("policy", "catalog"):
            for readings in ([self.reading], []):
                with self.subTest(loader=loader, readings=readings), \
                        mock.patch.object(incentives, "read", **(
                            {"side_effect": catalog.CatalogError("private-policy")} if loader == "policy" else
                            {"return_value": [setting()]})), \
                        mock.patch.object(catalog, "load_metadata", side_effect=catalog.CatalogError("private-catalog")):
                    rows = self.annotate(readings)
                    self.assertIs(type(rows), list)
                    self.assertEqual([self.facts_only(row) for row in rows], readings)

    def test_empty_policy_annotation_does_not_need_catalog(self):
        with mock.patch.object(incentives, "read", return_value=[]), \
                mock.patch.object(catalog, "load_metadata", side_effect=AssertionError("no policy")):
            row = self.annotate([self.reading])[0]
        self.assertEqual(row["steering"]["settings"], [])
        self.assertEqual(row["steering"]["account"], "a")
        self.assertNotIn("error", row["steering"])
        self.assertEqual(self.facts_only(row), self.reading)

    @staticmethod
    def facts_only(row):
        return {k: v for k, v in row.items() if k != "steering"}

    def test_policy_load_error_is_sanitized_and_keeps_facts_and_identity(self):
        with mock.patch.object(incentives, "read", side_effect=catalog.CatalogError("/private/policy-path secret")):
            row = self.annotate([self.reading])[0]
        ctx = row.pop("steering")
        self.assertEqual(row, self.reading)
        self.assertEqual(ctx["account"], "a")
        self.assertEqual(ctx["error"], "policy-unavailable")
        self.assertEqual(ctx["settings"], [])
        self.assertNotIn("/private", json.dumps(ctx))
        self.assertNotIn("secret", json.dumps(ctx))
        self.assertNotIn("steering", self.reading)

    def test_catalog_load_error_keeps_quota_and_null_identity(self):
        reading = dict(self.reading, account=None, status="unread")
        with mock.patch.object(incentives, "read", return_value=[setting()]), \
                mock.patch.object(catalog, "load_metadata", side_effect=catalog.CatalogError("/private/catalog-path")):
            row = self.annotate([reading])[0]
        ctx = row.pop("steering")
        self.assertEqual(row, reading)
        self.assertIsNone(ctx["account"])
        self.assertEqual(ctx["error"], "catalog-unavailable")
        self.assertNotIn("/private", json.dumps(ctx))

    def test_shipped_catalog_filesystem_failures_keep_facts_and_bounded_diagnostics(self):
        read_text = Path.read_text
        for failure in ("missing", "unreadable"):
            with self.subTest(failure=failure):
                fixture = Path(scratch.mkdtemp())
                shipped = fixture / "catalog.toml"
                if failure == "unreadable":
                    shipped.write_text('schema = 2\ntiers = ["standard"]\n')

                def fixture_read(path, *args, **kwargs):
                    if path == shipped and failure == "unreadable":
                        raise PermissionError(13, "permission denied", str(path))
                    return read_text(path, *args, **kwargs)

                with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": scratch.mkdtemp()}), \
                        mock.patch.object(catalog.resources, "files", return_value=fixture), \
                        mock.patch.object(Path, "read_text", new=fixture_read):
                    incentives.write([setting()])
                    try:
                        rows = self.annotate([self.reading])
                        empty, diagnostics = incentives._annotate([], now=NOW)
                    except OSError as error:
                        self.fail(f"catalog filesystem failure discarded quota facts: {error}")
                    self.assertIs(type(rows), list)
                    self.assertEqual(self.facts_only(rows[0]), self.reading)
                    context = rows[0]["steering"]
                    self.assertEqual((context["account"], context["error"], context["settings"]),
                                     ("a", "catalog-unavailable", []))
                    self.assertIs(type(empty), list)
                    self.assertEqual((empty, diagnostics), ([], ["catalog-unavailable"]))
                    self.assertNotIn(str(fixture), json.dumps(context))
                    out, err = io.StringIO(), io.StringIO()
                    with mock.patch.object(cli.cache, "through", return_value=[self.reading]), \
                            mock.patch.object(cli, "datetime") as clock, \
                            redirect_stdout(out), redirect_stderr(err):
                        clock.now.return_value = NOW
                        self.assertEqual(cli.main(["read", "--vendor", "openai", "--json"]), 0)
                    self.assertEqual(json.loads(out.getvalue()), rows)
                    self.assertIn("catalog-unavailable", err.getvalue())
                    self.assertNotIn(str(fixture), err.getvalue())

    def colon_catalog(self):
        local = Path(scratch.mkdtemp()) / "catalog.toml"
        local.write_text('schema = 2\n[models.m]\nprovider = "p"\ntiers = ["standard"]\n'
                         '[[offerings]]\nid = "api:m"\nmodel = "m"\nvendor = "openai"\n'
                         '[[offerings]]\nid = "sibling"\nmodel = "m"\nvendor = "openai"\n')
        return catalog.load_metadata(local)

    def test_exact_colon_offering_export_preserves_raw_factor_and_scope(self):
        cat = self.colon_catalog()
        self.assertEqual(cat.route("api:m")["provider"], "p")
        groups = [setting("api:m", 10)]
        context = incentives.overlay([self.reading], cat, groups, NOW)[0]["steering"]
        self.assertEqual(context["unresolved"], [])
        self.assertEqual([s["target"] for s in context["settings"]], ["api:m"])
        self.assertEqual([r["id"] for r in context["routes"]], ["api:m"])
        accounts = {oid: {"account": "a", "names": []} for oid in ("api:m", "sibling")}
        for minutes, factor in ((0, 10), (119, 10), (120, 1), (121, 1)):
            with self.subTest(minutes=minutes):
                now = NOW + timedelta(minutes=minutes)
                raw = incentives.resolve(cat, groups, accounts, now)
                exported = incentives.resolve(cat, context["settings"], accounts, now)
                self.assertEqual((raw.factors["api:m"], exported.factors["api:m"]), (factor, factor))
                self.assertEqual((raw.factors["sibling"], exported.factors["sibling"]), (1, 1))

    def test_exact_colon_offering_shared_target_and_setter_accept_the_catalog_id(self):
        cat = self.colon_catalog()
        self.assertTrue(catalog.target_known(cat, "api:m"))
        self.assertTrue(catalog.target_known(cat, "p:m"))
        self.assertTrue(catalog.target_known(cat, "p:api:m"))
        self.assertFalse(catalog.target_known(cat, "unknown:m"))
        self.assertFalse(catalog.target_known(cat, "api:m", account="a"))
        group = incentives.set_incentive(cat, "api:m", 3, now=NOW, duration=timedelta(hours=1))
        stored = incentives.read(incentives.incentives_path(cat.local), NOW)
        self.assertEqual(stored, [group])
        self.assertEqual((group["target"], group["multiplier"]), ("api:m", 3))
        resolved = incentives.resolve(cat, stored, {"api:m": {"account": "a"}}, NOW)
        self.assertEqual(resolved.factors["api:m"], 3)

    def test_real_policy_library_and_cli_annotate_after_cached_factual_read(self):
        tmp = Path(scratch.mkdtemp())
        config, cached = tmp / "config", tmp / "cache" / "unlimited"
        cached.mkdir(parents=True)
        fact = schema.reading("openai", "a", NOW - timedelta(seconds=60), "ok", limits=[
            schema.limit("seven_day", window_minutes=10080, used_at_least=.25,
                         resets_at=NOW + timedelta(hours=3), held=False)])
        cache_file = cached / "openai.json"
        cache_file.write_text(json.dumps({"readings": [fact], "history": {}}))
        adapter = SimpleNamespace(VENDOR="openai", discover=lambda: [Credential("a", {"key": "fixture-secret"})],
                                  names=lambda: {"a": ["private-alias"]},
                                  read=lambda *args: self.fail("fresh cache must not read a vendor"))
        groups = [setting("openai", 2), setting(ROUTE, 1, account="private-alias", hours=1)]
        groups[0].update(secret="fixture-secret", credential_path="/private/not-exported")
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config), "XDG_CACHE_HOME": str(tmp / "cache")}), \
                mock.patch.dict(unlimited.REGISTRY, {"openai": adapter}), \
                mock.patch.object(unlimited, "datetime") as library_clock, \
                mock.patch.object(cli, "datetime") as cli_clock, \
                mock.patch.object(transport, "get", side_effect=AssertionError("vendor request")):
            library_clock.now.return_value = NOW
            cli_clock.now.return_value = NOW
            incentives.write(groups)
            facts = unlimited.read(["openai"])
            self.assertNotIn("steering", facts[0])
            cached_before = cache_file.read_text()
            rows = self.annotate(facts)
            self.assertEqual(self.facts_only(rows[0]), facts[0])
            self.assertEqual(rows[0]["steering"]["observed_at"], NOW.isoformat())
            self.assertNotEqual(rows[0]["steering"]["observed_at"], rows[0]["taken_at"])
            self.assertEqual([s["multiplier"] for s in rows[0]["steering"]["settings"]], [2, 1])
            encoded = json.dumps(rows[0]["steering"])
            for secret in ("private-alias", "fixture-secret", "/private", "credential_path"):
                self.assertNotIn(secret, encoded)
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(cli.main(["read", "--vendor", "openai", "--json"]), 0)
            self.assertEqual(json.loads(out.getvalue()), rows)
            self.assertEqual(err.getvalue(), "")
            for argv in (["status", "--vendor", "openai"], ["--vendor", "openai"]):
                with self.subTest(argv=argv), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(cli.main(argv), 0)
            self.assertEqual(unlimited.read(["openai"]), facts)
            self.assertEqual(cache_file.read_text(), cached_before)
            self.assertNotIn("steering", cache_file.read_text())

    def test_no_readings_still_report_loader_errors_without_fabricating_a_row(self):
        for broken, expected in (("policy", "policy-unavailable"), ("catalog", "catalog-unavailable")):
            with self.subTest(broken=broken):
                tmp = Path(scratch.mkdtemp())
                out, err = io.StringIO(), io.StringIO()
                with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(tmp)}), \
                        mock.patch.object(cli.cache, "through", return_value=[]), \
                        mock.patch.object(cli, "datetime") as clock, \
                        redirect_stdout(out), redirect_stderr(err):
                    clock.now.return_value = NOW
                    if broken == "policy":
                        path = incentives.incentives_path()
                        path.parent.mkdir(parents=True)
                        path.write_text("invalid-json")
                    else:
                        incentives.write([setting()])
                        catalog.local_path().write_text("invalid-toml = [")
                    self.assertEqual(self.annotate([]), [])
                    self.assertEqual(cli.main(["read", "--vendor", "openai", "--json"]), 0)
                self.assertEqual(json.loads(out.getvalue()), [])
                self.assertIn(expected, err.getvalue())
                self.assertNotIn(str(tmp), err.getvalue())

    def test_read_cli_reports_real_annotation_error_without_discarding_facts(self):
        for broken, expected in (("policy", "policy-unavailable"), ("catalog", "catalog-unavailable"),
                                 ("policy-integer", "policy-unavailable"), ("catalog-array", "catalog-unavailable")):
            with self.subTest(broken=broken):
                tmp = Path(scratch.mkdtemp())
                out, err = io.StringIO(), io.StringIO()
                with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(tmp)}), \
                        mock.patch.object(cli.cache, "through", return_value=[self.reading]), \
                        mock.patch.object(cli, "datetime") as clock, \
                        redirect_stdout(out), redirect_stderr(err):
                    clock.now.return_value = NOW
                    if broken.startswith("policy"):
                        path = incentives.incentives_path()
                        path.parent.mkdir(parents=True)
                        path.write_text("invalid-json" if broken == "policy" else
                                        json.dumps({"incentives": [setting(factor=10**400)]}))
                    else:
                        incentives.write([setting()])
                        catalog.local_path().write_text("invalid-toml = [" if broken == "catalog" else
                                                       'schema = 2\n[[offerings]]\nid = "bad"\n'
                                                       'model = ["gpt-6.1-sol"]\nvendor = "openai"\n')
                    try:
                        self.assertEqual(cli.main(["read", "--vendor", "openai", "--json"]), 0)
                    except (OverflowError, TypeError) as error:
                        self.fail(f"advisory {broken} error discarded quota facts: {error}")
                row = json.loads(out.getvalue())[0]
                self.assertEqual(row["steering"].get("error"), expected)
                self.assertEqual(row["steering"]["account"], "a")
                self.assertEqual(self.facts_only(row), self.reading)
                self.assertIn(expected, err.getvalue())
                self.assertNotIn(str(tmp), err.getvalue())
                self.assertNotIn(str(tmp), json.dumps(row["steering"]))

