"""The model catalog: the shipped file, a machine's override, promotions and bans."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from unlimited import catalog

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


class Cli(unittest.TestCase):
    def run_models(self, *args: str, local: str | None = None) -> tuple[int, str, str]:
        import io
        import os
        from contextlib import redirect_stderr, redirect_stdout
        from unittest import mock

        from unlimited import cli
        home = Path(tempfile.mkdtemp())
        if local is not None:
            (home / "unlimited").mkdir()
            (home / "unlimited" / "catalog.toml").write_text(local)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(home)}), redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["models", *args])
        return code, out.getvalue(), err.getvalue()

    def test_one_providers_model_or_exit_1_when_it_has_none_at_the_tier(self):
        self.assertEqual(self.run_models("--provider", "codex", "--tier", "heavy")[:2], (0, "gpt-6-sol\n"))
        self.assertEqual(self.run_models("--provider", "grok", "--tier", "heavy")[:2], (1, ""))

    def test_the_whole_catalog_carries_providers_and_only_live_promotions(self):
        import json
        local = ('schema = 1\n[[promotions]]\nprovider = "stealth"\nmodel = "gone"\ntiers = ["standard"]\nuntil = 2020-01-01\n'
                 '[[promotions]]\nprovider = "stealth"\nmodel = "open"\ntiers = ["standard"]\n')
        code, out, _ = self.run_models("--catalog", local=local)
        got = json.loads(out)
        self.assertEqual((code, got["providers"]["deepseek"]["harness"]), (0, "opencode"))
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
