"""The model catalog: the shipped file, a machine's override, promotions and bans."""

from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from unlimited import catalog, cli

import scratch

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


class Catalog(unittest.TestCase):
    def setUp(self):
        self.local = Path(scratch.mkdtemp()) / "catalog.toml"

    def load(self, text: str | None = None) -> catalog.Catalog:
        if text is not None:
            self.local.write_text(text)
        return catalog.load(self.local)

    def test_the_shipped_catalog_loads_from_the_package_without_a_local_file(self):
        c = self.load()
        self.assertEqual(c.model("codex", "heavy"), "gpt-6-sol")
        self.assertIsNone(c.model("grok", "heavy"))

    def test_a_local_provider_key_replaces_only_that_key(self):
        c = self.load('schema = 1\n[providers.codex]\nstandard = "gpt-7"\n')
        self.assertEqual((c.model("codex", "standard"), c.model("codex", "heavy")), ("gpt-7", "gpt-6-sol"))

    def test_a_local_promotions_list_replaces_the_shipped_one_whole(self):
        c = self.load('schema = 1\npromotions = []\n')
        self.assertFalse(any(x.promoted for x in c.candidates("standard", NOW)))

    def test_promotions_come_first_in_file_order_then_providers_in_file_order(self):
        c = self.load('schema = 1\n'
                      '[[promotions]]\nprovider = "meta"\nmodel = "m-promo"\ntiers = ["heavy"]\n'
                      '[[promotions]]\nprovider = "stealth"\nmodel = "s-promo"\ntiers = ["heavy"]\n')
        got = [(x.provider, x.model, x.promoted) for x in c.candidates("heavy", NOW)]
        self.assertEqual(got, [("meta", "m-promo", True), ("stealth", "s-promo", True),
                               ("codex", "gpt-6-sol", False), ("claude", "opus", False), ("glm", "glm-5.3", False)])

    def test_a_promotion_is_live_through_the_end_of_its_day_in_utc_and_forever_without_one(self):
        c = self.load('schema = 1\n'
                      '[[promotions]]\nprovider = "stealth"\nmodel = "dated"\ntiers = ["standard"]\nuntil = 2026-09-25\n'
                      '[[promotions]]\nprovider = "stealth"\nmodel = "open"\ntiers = ["standard"]\n')
        live = lambda t: [x.model for x in c.candidates("standard", t) if x.promoted]
        self.assertEqual(live(datetime(2026, 9, 25, 23, 59, tzinfo=timezone.utc)), ["dated", "open"])
        self.assertEqual(live(datetime(2026, 9, 26, 0, 0, tzinfo=timezone.utc)), ["open"])

    def test_a_banned_model_is_never_a_candidate(self):
        c = self.load('schema = 1\nbanned = ["opencode-go/muse-spark-1.3-contributor", "opencode-go/space-bunny-free"]\n')
        models = [x.model for x in c.candidates("standard", NOW)]
        self.assertNotIn("opencode-go/muse-spark-1.3-contributor", models)
        self.assertNotIn("opencode-go/space-bunny-free", models)
        self.assertIn("commandcode/meta/muse-spark-1.3-contributor", models, "banning one route leaves the model's others")
        whole = [x.model for x in self.load('schema = 1\nbanned = ["muse-spark-1-3-contributor"]\n').candidates("standard", NOW)]
        self.assertFalse([m for m in whole if "muse-spark-1.3" in m], "banning the model takes every route")
        self.assertIn("opencode-go/muse-spark-1.2-contributor", whole, "and leaves its maker's other models")
        # A banned model still has a maker: independence is not the ban's business.
        self.assertEqual(c.provider_of("opencode-go/muse-spark-1.3-contributor"), "meta")

    def test_a_local_file_that_is_not_utf8_refuses(self):
        self.local.write_text("schema = 1\n", encoding="utf-16")
        with self.assertRaises(catalog.CatalogError):
            catalog.load(self.local)

    def test_tie_preference_ships_cheapest_first_and_a_local_list_replaces_it(self):
        self.assertEqual(self.load().tie_preference, ["meta", "deepseek", "stealth"])
        self.assertEqual(self.load('schema = 1\ntie_preference = ["deepseek"]\n').tie_preference, ["deepseek"])
        with self.assertRaises(catalog.CatalogError):
            self.load('schema = 1\ntie_preference = ["nobody"]\n')

    def test_a_promoted_model_belongs_to_its_provider(self):
        self.assertEqual(self.load().provider_of("commandcode/stealth/space-bunny-alpha"), "stealth")
        self.assertIsNone(self.load().provider_of("unknown-model"))

    def test_a_broken_or_unversioned_local_file_refuses_rather_than_falling_back(self):
        for text in ("schema = 1\n[providers\n", "[providers.codex]\nstandard = 'x'\n", "schema = 3\n",
                     'schema = 1\n[[promotions]]\nprovider = "nobody"\nmodel = "m"\ntiers = ["standard"]\n',
                     'schema = 1\n[[promotions]]\nprovider = "stealth"\nmodel = "m"\ntiers = ["huge"]\n',
                     'schema = 1\nbanned = "opencode-go/muse-spark-1.3-contributor"\n', "schema = 1\nproviders = 5\n",
                     'schema = 1\n[providers]\ncodex = "x"\n',
                     'schema = 1\n[providers.meta]\nstandard = "sonnet"\n',
                     'schema = 1\n[[promotions]]\nprovider = ["stealth"]\nmodel = "m"\ntiers = ["standard"]\n'):
            with self.subTest(text=text), self.assertRaises(catalog.CatalogError):
                self.load(text)


class Switches(unittest.TestCase):
    """What `unlimited off` writes: the shipped catalog stays the default, the switch only subtracts."""
    SPACE_BUNNY = "commandcode/stealth/space-bunny-alpha"

    def load(self, *off: dict) -> catalog.Catalog:
        local = Path(scratch.mkdtemp()) / "catalog.toml"
        catalog.write_switches(list(off), catalog.switches_path(local))
        return catalog.load(local)

    def models(self, c: catalog.Catalog, tier: str, now=NOW) -> list[tuple[str, str]]:
        return [(x.provider, x.model) for x in c.candidates(tier, now)]

    def test_a_provider_switched_off_drops_its_promotions_and_tier_models(self):
        c = self.load({"target": "stealth"}, {"target": "codex"})
        got = self.models(c, "standard")
        self.assertFalse([p for p, _ in got if p in ("stealth", "codex")], got)
        self.assertIsNone(c.model("codex", "heavy", NOW))
        self.assertIn(("claude", "sonnet"), got)
        j = c.to_json(NOW)
        self.assertEqual((j["promotions"], "heavy" in j["providers"]["codex"]), ([], False))
        # The provider stays defined: a caller still needs its usage vendor.
        self.assertEqual(j["providers"]["codex"]["usage"], "openai")

    def test_a_model_or_a_pair_switched_off_leaves_the_rest_of_its_provider(self):
        for target in (self.SPACE_BUNNY, f"stealth:{self.SPACE_BUNNY}"):
            got = self.models(self.load({"target": target}), "standard")
            self.assertNotIn(("stealth", self.SPACE_BUNNY), got, target)
            self.assertIn(("meta", "opencode-go/muse-spark-1.3-contributor"), got, target)
        wrong_pair = self.load({"target": f"meta:{self.SPACE_BUNNY}"})
        self.assertIn(("stealth", self.SPACE_BUNNY), self.models(wrong_pair, "standard"))

    def test_a_switch_lapses_at_its_time_and_one_without_a_time_holds(self):
        c = self.load({"target": "stealth", "until": "2026-09-25T13:00:00+00:00"}, {"target": "glm"})
        self.assertNotIn("stealth", [p for p, _ in self.models(c, "standard", NOW)])
        later = datetime(2026, 9, 25, 13, 0, tzinfo=timezone.utc)
        self.assertIn("stealth", [p for p, _ in self.models(c, "standard", later)])
        self.assertNotIn("glm", [p for p, _ in self.models(c, "standard", later)])

    def test_off_policy_is_the_live_policy_and_drops_lapsed(self):
        local = Path(scratch.mkdtemp()) / "catalog.toml"
        until = "2026-09-25T13:00:00+00:00"
        catalog.write_switches([{"target": "kimi"},                  # a usage vendor, no end
                                {"target": "stealth"},               # a provider only
                                {"target": "sonnet"},                # an offering id only
                                {"target": "zai", "until": until}],  # a vendor, until not yet passed
                               catalog.switches_path(local))
        self.assertEqual(catalog.off_policy(NOW, catalog.switches_path(local)),
                         {"kimi": None, "zai": until})
        later = datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc)
        self.assertEqual(catalog.off_policy(later, catalog.switches_path(local)), {"kimi": None})

    def test_the_switches_file_is_private_as_soon_as_it_exists(self):
        local = Path(scratch.mkdtemp()) / "catalog.toml"
        catalog.write_switches([{"target": "kimi"}], catalog.switches_path(local))
        self.assertEqual(catalog.switches_path(local).stat().st_mode & 0o777, 0o600)

    def test_a_switches_file_an_older_release_left_readable_is_made_private_on_read(self):
        local = Path(scratch.mkdtemp()) / "catalog.toml"
        path = catalog.switches_path(local)
        catalog.write_switches([{"target": "kimi"}], path)
        path.chmod(0o644)  # what 0.1.0's writer left behind
        catalog.read_switches(path)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_a_mode_that_cannot_be_repaired_does_not_stop_the_read(self):
        local = Path(scratch.mkdtemp()) / "catalog.toml"
        path = catalog.switches_path(local)
        catalog.write_switches([{"target": "kimi"}], path)
        path.chmod(0o644)  # a file on a mount, or of an owner, that refuses chmod
        with mock.patch.object(catalog.os, "chmod", side_effect=PermissionError):
            self.assertEqual([x["target"] for x in catalog.read_switches(path)], ["kimi"])

    def test_a_stale_tmp_from_an_older_release_cannot_leak_the_new_file(self):
        local = Path(scratch.mkdtemp()) / "catalog.toml"
        path = catalog.switches_path(local)
        path.with_suffix(".tmp").write_text("{}")  # what 0.1.0 left at the umask
        path.with_suffix(".tmp").chmod(0o644)
        catalog.write_switches([{"target": "kimi", "why": "note"}], path)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_switch_flips_one_target_and_returns_what_it_wrote(self):
        local = Path(scratch.mkdtemp()) / "catalog.toml"
        path = catalog.switches_path(local)
        catalog.write_switches([{"target": "kimi"},
                                {"target": "zai", "until": "2020-01-01T00:00:00+00:00"}], path)
        # The lapsed zai drops; what comes back is exactly what was written.
        self.assertEqual(catalog.switch("stealth", now=NOW, path=path),
                         [{"target": "kimi", "until": None, "why": None},
                          {"target": "stealth", "until": None, "why": None}])
        self.assertEqual(catalog.switch("kimi", on=True, now=NOW, path=path),
                         [{"target": "stealth", "until": None, "why": None}])
        self.assertEqual([x["target"] for x in catalog.read_switches(path)], ["stealth"])

    def test_on_of_a_target_that_is_not_off_writes_nothing(self):
        local = Path(scratch.mkdtemp()) / "catalog.toml"
        path = catalog.switches_path(local)
        catalog.write_switches([{"target": "kimi"}], path)
        with self.assertRaises(catalog.NotOff):
            catalog.switch("zai", on=True, now=NOW, path=path)
        self.assertEqual([x["target"] for x in catalog.read_switches(path)], ["kimi"])

    def test_a_switch_can_name_one_account_of_a_vendor(self):
        local = Path(scratch.mkdtemp()) / "catalog.toml"
        path = catalog.switches_path(local)
        catalog.write_switches([{"target": "zai", "account": "claude-glm-2"},
                                {"target": "kimi"}], path)
        # on of the vendor alone refuses (no vendor switch) and leaves the account switch alone
        with self.assertRaises(catalog.NotOff):
            catalog.switch("zai", on=True, now=NOW, path=path)
        self.assertEqual([(x["target"], x.get("account")) for x in catalog.read_switches(path)],
                         [("zai", "claude-glm-2"), ("kimi", None)])
        self.assertEqual(catalog.switch("zai", account="claude-glm-2", on=True, now=NOW, path=path),
                         [{"target": "kimi", "until": None, "why": None}])
        with self.assertRaises(catalog.NotOff):
            catalog.switch("zai", account="claude-glm-1", on=True, now=NOW, path=path)

    def test_off_policy_keys_vendor_and_account_switches(self):
        local = Path(scratch.mkdtemp()) / "catalog.toml"
        path = catalog.switches_path(local)
        catalog.write_switches([{"target": "kimi"},
                                {"target": "zai", "account": "claude-glm-2"},
                                {"target": "stealth"},                       # a provider only
                                {"target": "zai", "until": "2020-01-01T00:00:00+00:00"}], path)
        self.assertEqual(catalog.off_policy(NOW, path),
                         {"kimi": None, "zai/claude-glm-2": None})

    def test_an_account_switch_never_blocks_the_vendors_catalog_routes(self):
        # An account switch binds verdict alone (like a route-less vendor): the vendor's other
        # accounts still serve its routes.
        c = self.load({"target": "zai", "account": "claude-glm-2"})
        self.assertIn("glm", [p for p, _ in self.models(c, "standard")])
        vendor_off = self.load({"target": "zai"})
        self.assertNotIn("glm", [p for p, _ in self.models(vendor_off, "standard")])

    def test_a_blank_account_is_refused_not_written(self):
        local = Path(scratch.mkdtemp()) / "catalog.toml"
        path = catalog.switches_path(local)
        catalog.write_switches([{"target": "zai"}], path)  # a live whole-vendor switch
        for blank in ("", "   "):
            with self.assertRaises(catalog.CatalogError):
                catalog.switch("zai", account=blank, now=NOW, path=path)
        self.assertEqual(catalog.read_switches(path), [{"target": "zai"}])  # untouched

    def test_a_write_waits_for_the_lock(self):
        import subprocess, sys
        local = Path(scratch.mkdtemp()) / "catalog.toml"
        path = catalog.switches_path(local)
        catalog.write_switches([{"target": "kimi"}], path)
        src = str(Path(__file__).resolve().parents[1] / "src")
        child = (f"import sys; sys.path[:0] = [{src!r}]\n"
                 "from datetime import datetime, timezone\n"
                 "from pathlib import Path\n"
                 "from unlimited import catalog\n"
                 f"catalog.switch('stealth', now=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc), path=Path({str(path)!r}))")
        with catalog._flock(path):
            proc = subprocess.Popen([sys.executable, "-c", child])
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass  # what the lock is for: the child is still waiting
            else:
                proc.kill()
                self.fail("a second writer ran while the lock was held")
        self.assertEqual(proc.wait(timeout=10), 0)  # released: it completes
        self.assertEqual([x["target"] for x in catalog.read_switches(path)], ["kimi", "stealth"])

    def test_a_switch_that_cannot_read_the_file_is_a_message_not_a_traceback(self):
        home = Path(scratch.mkdtemp())
        def run(*args):
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(home)}), redirect_stdout(out), redirect_stderr(err):
                return cli.main(list(args)), err.getvalue()
        with mock.patch.object(catalog, "switch", side_effect=catalog.CatalogError("x: broken")):
            code, err = run("off", "kimi")
        self.assertEqual((code, "unlimited: catalog: x: broken" in err), (2, True))

    def test_a_switches_file_that_does_not_parse_stops_the_load(self):
        local = Path(scratch.mkdtemp()) / "catalog.toml"
        for text in ('{"off": "stealth"}', '{"off": [{"target": "stealth", "until": "tomorrow"}]}'):
            catalog.switches_path(local).write_text(text)
            with self.assertRaises(catalog.CatalogError, msg=text):
                catalog.load(local)


class Cli(unittest.TestCase):
    def run_models(self, *args: str, local: str | None = None) -> tuple[int, str, str]:
        home = Path(scratch.mkdtemp())
        if local is not None:
            (home / "unlimited").mkdir()
            (home / "unlimited" / "catalog.toml").write_text(local)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(home)}), redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["models", *args])
        return code, out.getvalue(), err.getvalue()

    def test_off_on_round_trip_and_a_typo_is_refused(self):
        import json
        home = Path(scratch.mkdtemp())
        def run(*args):
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(home)}), redirect_stdout(out), redirect_stderr(err):
                return cli.main(list(args)), out.getvalue()
        def candidates():
            return [x["provider"] for x in json.loads(run("models", "--json")[1])]
        # A promotion of its own: the shipped ones lapse on a date, and this is not about them.
        (home / "unlimited").mkdir()
        (home / "unlimited" / "catalog.toml").write_text(
            'schema = 1\n[[promotions]]\nprovider = "stealth"\nmodel = "open"\ntiers = ["standard"]\n')
        self.assertIn("stealth", candidates())
        self.assertEqual(run("off", "stealth", "--for", "1d", "--why", "slow")[0], 0)
        self.assertNotIn("stealth", candidates())
        code, out = run("off")
        self.assertEqual((code, out.split()[0], out.split()[-1]), (0, "stealth", "(slow)"))
        self.assertEqual(run("off", "stealht")[0], 1)
        self.assertEqual(run("off", "meta:commandcode/stealth/space-bunny-alpha")[0], 1)
        self.assertEqual(run("on", "stealth")[0], 0)
        self.assertIn("stealth", candidates())
        self.assertEqual(run("on", "stealth")[0], 1)

    def test_off_accepts_a_usage_vendor_with_no_catalog_route(self):
        import json
        home = Path(scratch.mkdtemp())
        def run(*args):
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(home)}), redirect_stdout(out), redirect_stderr(err):
                return cli.main(list(args)), out.getvalue(), err.getvalue()
        self.assertEqual(run("off", "kimi")[0], 0)  # no catalog route on vendor kimi anywhere
        switches = json.loads((home / "unlimited" / "switches.json").read_text())["off"]
        self.assertEqual(switches, [{"target": "kimi", "until": None, "why": None}])
        self.assertEqual(run("on", "kimi")[0], 0)
        self.assertEqual(run("off", "kimij")[0], 1)  # an unknown name is still refused

    def test_off_lists_live_switches_as_json(self):
        import json
        home = Path(scratch.mkdtemp())
        def run(*args):
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(home)}), redirect_stdout(out), redirect_stderr(err):
                return cli.main(list(args)), out.getvalue()
        run("off", "kimi", "--for", "1h", "--why", "banned")
        # A switch whose time has passed is not listed.
        (home / "unlimited" / "switches.json").write_text(json.dumps(
            {"off": [{"target": "zai", "until": "2020-01-01T00:00:00+00:00"},
                     {"target": "kimi", "until": "2999-01-01T00:00:00+00:00", "why": "banned"}]}))
        code, out = run("off", "--json")
        self.assertEqual(code, 0)
        got = json.loads(out)
        self.assertEqual([(x["target"], x["why"]) for x in got], [("kimi", "banned")])
        self.assertIsNotNone(got[0]["until"])

    def test_off_can_name_one_account_of_a_usage_vendor(self):
        import json
        home = Path(scratch.mkdtemp())
        def run(*args):
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(home)}), redirect_stdout(out), redirect_stderr(err):
                return cli.main(list(args)), out.getvalue(), err.getvalue()
        self.assertEqual(run("off", "zai", "--account", "claude-glm-2")[0], 0)
        got = json.loads((home / "unlimited" / "switches.json").read_text())["off"]
        self.assertEqual(got, [{"target": "zai", "account": "claude-glm-2", "until": None, "why": None}])
        code, out, _ = run("off")
        self.assertEqual((code, out.split()[0]), (0, "zai/claude-glm-2"))
        code, out, _ = run("on", "zai", "--account", "claude-glm-2", "--json")
        self.assertEqual((code, json.loads(out)), (0, []))
        code, _, err = run("off", "sonnet", "--account", "x")  # only a usage vendor
        self.assertEqual((code, "--account applies only to a usage vendor" in err), (1, True))

    def test_a_hand_written_blank_account_in_the_file_is_an_error(self):
        local = Path(scratch.mkdtemp()) / "catalog.toml"
        path = catalog.switches_path(local)
        path.write_text('{"off": [{"target": "zai", "account": "   "}]}')
        with self.assertRaises(catalog.CatalogError):
            catalog.read_switches(path)

    def test_off_with_a_target_and_json_prints_what_is_off_after(self):
        import json
        home = Path(scratch.mkdtemp())
        def run(*args):
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(home)}), redirect_stdout(out), redirect_stderr(err):
                return cli.main(list(args)), out.getvalue()
        code, out = run("off", "kimi", "--json")
        self.assertEqual(code, 0)
        self.assertEqual([x["target"] for x in json.loads(out)], ["kimi"])
        code, out = run("on", "kimi", "--json")
        self.assertEqual((code, json.loads(out)), (0, []))

    def test_verdict_marks_a_switched_off_vendor_and_survives_a_broken_switches_file(self):
        import json
        home = Path(scratch.mkdtemp())
        (home / "unlimited").mkdir()
        (home / "unlimited" / "switches.json").write_text("{")
        def run():
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(home)}), redirect_stdout(out), redirect_stderr(err):
                return cli.main(["verdict", "--work", "900"]), out.getvalue(), err.getvalue()
        with mock.patch("unlimited.cache.through", return_value=[]):
            code, out, err = run()
        self.assertEqual((code, "switches.json" in err), (2, True))  # a message, not a traceback
        # The broken file is caught before any vendor is read: the reads are not wasted.
        def boom(**kw):
            raise AssertionError("a vendor was read")
        (home / "unlimited" / "switches.json").write_text("{")
        with mock.patch("unlimited.cache.through", side_effect=boom):
            code, _, err = run()
        self.assertEqual((code, "switches.json" in err), (2, True))
        (home / "unlimited" / "switches.json").write_text('{"off": [{"target": "kimi"}]}')
        with mock.patch("unlimited.cache.through",
                        return_value=[{"vendor": "kimi", "account": "k", "names": [], "status": "ok"}]):
            code, out, _ = run()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)[0]["verdict"],
                         {"state": "excluded", "reason": "off", "until": None})

    def test_one_providers_model_or_exit_1_when_it_has_none_at_the_tier(self):
        self.assertEqual(self.run_models("--provider", "codex", "--tier", "heavy")[:2], (0, "gpt-6-sol\n"))
        self.assertEqual(self.run_models("--provider", "grok", "--tier", "heavy")[:2], (1, ""))

    def test_the_whole_catalog_carries_providers_and_only_live_promotions(self):
        import json
        local = ('schema = 1\n[[promotions]]\nprovider = "stealth"\nmodel = "gone"\ntiers = ["standard"]\nuntil = 2020-01-01\n'
                 '[[promotions]]\nprovider = "stealth"\nmodel = "open"\ntiers = ["standard"]\n')
        code, out, _ = self.run_models("--catalog", local=local)
        got = json.loads(out)
        self.assertEqual((code, got["providers"]["deepseek"]["usage"]), (0, "opencode"))
        self.assertEqual([p["model"] for p in got["promotions"]], ["open"])

    def test_json_lists_candidates_and_a_broken_catalog_exits_2(self):
        import json
        code, out, _ = self.run_models("--tier", "heavy", "--json")
        self.assertEqual((code, json.loads(out)[0]), (0, {"provider": "codex", "model": "gpt-6-sol", "promoted": False}))
        code, out, err = self.run_models("--json", local="schema = 3\n")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("catalog", err)


if __name__ == "__main__":
    unittest.main()
