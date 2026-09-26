"""The model catalog: the shipped file, a machine's override, promotions and bans."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from unlimited import catalog, cli

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


class Catalog(unittest.TestCase):
    def setUp(self):
        self.local = Path(tempfile.mkdtemp()) / "catalog.toml"

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
        self.assertIsNone(c.model("meta", "standard"))
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
        for text in ("schema = 1\n[providers\n", "[providers.codex]\nstandard = 'x'\n", "schema = 2\n",
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
        local = Path(tempfile.mkdtemp()) / "catalog.toml"
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

    def test_a_switches_file_that_does_not_parse_stops_the_load(self):
        local = Path(tempfile.mkdtemp()) / "catalog.toml"
        for text in ('{"off": "stealth"}', '{"off": [{"target": "stealth", "until": "tomorrow"}]}'):
            catalog.switches_path(local).write_text(text)
            with self.assertRaises(catalog.CatalogError, msg=text):
                catalog.load(local)


class Cli(unittest.TestCase):
    def run_models(self, *args: str, local: str | None = None) -> tuple[int, str, str]:
        home = Path(tempfile.mkdtemp())
        if local is not None:
            (home / "unlimited").mkdir()
            (home / "unlimited" / "catalog.toml").write_text(local)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(home)}), redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["models", *args])
        return code, out.getvalue(), err.getvalue()

    def test_off_on_round_trip_and_a_typo_is_refused(self):
        import json
        home = Path(tempfile.mkdtemp())
        def run(*args):
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(home)}), redirect_stdout(out), redirect_stderr(err):
                return cli.main(list(args)), out.getvalue()
        def candidates():
            return [x["provider"] for x in json.loads(run("models", "--json")[1])]
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
        code, out, err = self.run_models("--json", local="schema = 2\n")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("catalog", err)


if __name__ == "__main__":
    unittest.main()
