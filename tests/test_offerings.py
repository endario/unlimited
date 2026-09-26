"""One model, several vendors: offerings in the catalog (docs/catalog.md)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from unlimited import catalog

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
SECOND = ('schema = 2\n[[offerings]]\nid = "commandcode/deepseek/deepseek-v4.1-flash"\n'
          'model = "deepseek-v4-1-flash"\nvendor = "commandcode"\n')


def load(text: str, off: list | None = None) -> catalog.Catalog:
    d = Path(tempfile.mkdtemp())
    (d / "catalog.toml").write_text(text)
    if off:
        catalog.write_switches(off, d / "switches.json")
    return catalog.load(d / "catalog.toml")


class Routes(unittest.TestCase):
    def test_a_model_sold_by_two_vendors_is_two_routes_of_one_maker(self):
        c = load(SECOND)
        ds = [(r["id"], r["vendor"]) for r in c.routes(NOW, "standard") if r["model"] == "deepseek-v4-1-flash"]
        self.assertEqual(ds, [("opencode-go/deepseek-v4.1-flash", "opencode"),
                              ("commandcode/deepseek/deepseek-v4.1-flash", "commandcode")])
        self.assertEqual({c.provider_of(i) for i, _ in ds}, {"deepseek"})
        self.assertEqual([x.model for x in c.candidates("standard", NOW) if x.provider == "deepseek"],
                         [i for i, _ in ds], "every route is a candidate, none hidden")

    def test_switching_a_vendor_off_takes_only_its_routes(self):
        c = load(SECOND, off=[{"target": "commandcode"}])
        ids = [r["id"] for r in c.routes(NOW, "standard")]
        self.assertIn("opencode-go/deepseek-v4.1-flash", ids)
        self.assertFalse([i for i in ids if i.startswith("commandcode/")], ids)

    def test_a_local_offering_replaces_the_shipped_one_with_its_id(self):
        c = load('schema = 2\n[[offerings]]\nid = "opencode-go/deepseek-v4.1-flash"\n'
                 'model = "deepseek-v4-1-flash"\nvendor = "opencode"\nuntil = 2026-09-01\n')
        self.assertNotIn("opencode-go/deepseek-v4.1-flash", [r["id"] for r in c.routes(NOW)])

    def test_an_offering_id_is_one_route_and_its_model_must_exist(self):
        for text in (SECOND + SECOND.replace("schema = 2\n", ""),
                     'schema = 2\n[[offerings]]\nid = "x"\nmodel = "no-such-model"\nvendor = "v"\n'):
            with self.assertRaises(catalog.CatalogError, msg=text):
                load(text)

    def test_the_schema_1_view_names_one_route_per_provider_and_the_free_ones(self):
        j = load(SECOND).to_json(NOW)
        self.assertEqual((j["providers"]["deepseek"]["standard"], j["providers"]["deepseek"]["usage"]),
                         ("opencode-go/deepseek-v4.1-flash", "opencode"))
        self.assertEqual(len([o for o in j["offerings"] if o["model"] == "deepseek-v4-1-flash"]), 2)


    def test_a_schema_1_usage_alone_still_moves_a_providers_models_to_that_vendor(self):
        c = load('schema = 1\n[providers.codex]\nusage = "azure"\n')
        self.assertEqual({r["vendor"] for r in c.routes(NOW) if r["provider"] == "codex"}, {"azure"})

    def test_the_one_vendor_view_never_pairs_a_tier_with_another_vendors_usage(self):
        c = load('schema = 2\n[[offerings]]\nid = "sol-elsewhere"\nmodel = "gpt-6-sol"\nvendor = "azure"\n'
                 '[[offerings]]\nid = "gpt-6-sol"\nmodel = "gpt-6-sol"\nvendor = "openai"\nuntil = 2020-01-01\n')
        view = c.to_json(NOW)["providers"]["codex"]
        self.assertEqual((view["usage"], view.get("heavy")), ("openai", None), view)

    def test_a_schema_1_providers_own_keys_stay_in_the_view(self):
        c = load('schema = 1\n[providers.codex]\nlauncher = "mine"\n')
        self.assertEqual(c.to_json(NOW)["providers"]["codex"]["launcher"], "mine")

    def test_model_answers_as_the_view_does(self):
        c = load('schema = 2\n[[offerings]]\nid = "sol-elsewhere"\nmodel = "gpt-6-sol"\nvendor = "azure"\n'
                 '[[offerings]]\nid = "gpt-6-sol"\nmodel = "gpt-6-sol"\nvendor = "openai"\nuntil = 2020-01-01\n')
        self.assertIsNone(c.model("codex", "heavy", NOW))
        self.assertEqual(c.model("codex", "standard", NOW), c.to_json(NOW)["providers"]["codex"]["standard"])

    def test_a_route_on_another_vendor_is_not_priced_by_the_providers_quota(self):
        from unlimited import choice
        c = load(SECOND)
        got = choice.choose(c, tier="standard", candidates=["deepseek"], quota={"deepseek": 0.2}, deadline=600,
                            now=NOW, log=Path(tempfile.mkdtemp()) / "d.jsonl")
        rho = {x["model"]: x["rho"] for x in got["candidates"]}
        self.assertEqual((rho["opencode-go/deepseek-v4.1-flash"], rho["commandcode/deepseek/deepseek-v4.1-flash"]),
                         (0.2, None))

    def test_a_routes_own_projection_prices_it(self):
        from unlimited import choice
        got = choice.choose(load(SECOND), tier="standard", candidates=["deepseek"], deadline=600, now=NOW,
                            quota={"deepseek": 0.2, "commandcode/deepseek/deepseek-v4.1-flash": 0.5},
                            log=Path(tempfile.mkdtemp()) / "d.jsonl")
        self.assertEqual({x["model"]: x["rho"] for x in got["candidates"]},
                         {"opencode-go/deepseek-v4.1-flash": 0.2, "commandcode/deepseek/deepseek-v4.1-flash": 0.5})

    def test_a_route_the_caller_excludes_is_not_a_candidate(self):
        from unlimited import choice
        got = choice.choose(load(SECOND), tier="standard", candidates=["deepseek"], deadline=600, now=NOW, quota={},
                            exclude={"opencode-go/deepseek-v4.1-flash": "account used up"},
                            log=Path(tempfile.mkdtemp()) / "d.jsonl")
        self.assertEqual([x["model"] for x in got["candidates"]], ["commandcode/deepseek/deepseek-v4.1-flash"])
        self.assertEqual(got["request"]["exclude"], {"opencode-go/deepseek-v4.1-flash": "account used up"},
                         "the caller's reason is recorded")

    def test_a_route_or_a_model_can_be_named_as_well_as_a_provider(self):
        from unlimited import choice
        c = load(SECOND)
        named = lambda *names: [x["model"] for x in choice.named(c, "heavy", list(names), NOW)]
        self.assertEqual(named("commandcode/deepseek/deepseek-v4.1-flash"), ["commandcode/deepseek/deepseek-v4.1-flash"],
                         "an offering id names that route, whatever the tier")
        self.assertEqual(named("deepseek-v4-1-flash", "opencode-go/deepseek-v4.1-flash"),
                         ["opencode-go/deepseek-v4.1-flash", "commandcode/deepseek/deepseek-v4.1-flash"],
                         "a model names each of its routes, each once")
        self.assertEqual(named("deepseek"), [], "a provider names its routes at the tier only")
        self.assertEqual(named("no-such-thing"), [])

    def test_a_route_that_debits_twice_as_much_is_overflow_for_its_sibling(self):
        from unlimited import choice
        dear = SECOND + "debit = 2\n"
        cc, go = "commandcode/deepseek/deepseek-v4.1-flash", "opencode-go/deepseek-v4.1-flash"

        def pick(quota):
            got = choice.choose(load(dear), tier="standard", candidates=["deepseek"], deadline=600, now=NOW,
                                quota=quota, log=Path(tempfile.mkdtemp()) / "d.jsonl")
            return got["candidates"][got["pick"]]["model"], {x["model"]: x["debit"] for x in got["candidates"]}

        self.assertEqual(pick({go: 0.5, cc: 0.5}), (go, {go: 1, cc: 2}))
        # ln 2 / 5 ≈ 0.14: the cheaper plan keeps the work until its account is that much more used.
        self.assertEqual(pick({go: 0.6, cc: 0.5})[0], go)
        self.assertEqual(pick({go: 0.7, cc: 0.5})[0], cc)
        self.assertEqual(pick({})[0], go, "with no projection at all, the debit alone decides")

    def test_a_promotion_costs_nothing_whatever_it_debits(self):
        from unlimited import choice
        got = choice.choose(load(SECOND + "free = true\ndebit = 5\n"), tier="standard", candidates=["deepseek"],
                            deadline=600, now=NOW, quota={}, log=Path(tempfile.mkdtemp()) / "d.jsonl")
        self.assertEqual({x["model"]: x["pi"] for x in got["candidates"]}["commandcode/deepseek/deepseek-v4.1-flash"], 0)

    def test_a_debit_must_be_a_positive_finite_number(self):
        for bad in ("0", "-1", '"2"', "inf", "true"):
            with self.assertRaises(catalog.CatalogError, msg=bad):
                load(SECOND + f"debit = {bad}\n")

    def test_malformed_or_ambiguous_files_are_catalog_errors(self):
        for text in ('schema = 2\nofferings = ["x"]\n',
                     'schema = 1\n[providers.codex]\nstandard = "shared"\n[providers.meta]\nstandard = "shared"\n',
                     'schema = 1\n[providers.orphan]\nusage = 5\n',
                     'schema = 2\n[[offerings]]\nid = "x"\nmodel = "opus"\nvendor = "v"\n_of = "codex"\n'):
            with self.assertRaises(catalog.CatalogError, msg=text):
                load(text)

if __name__ == "__main__":
    unittest.main()
