"""Model cards: a vendor's published figures for a route, beside what its runs here show."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from unlimited import catalog, cli, outcomes

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
CARD = ('[[cards]]\nvendor = "{vendor}"\nname = "GLM-5.3"\nmodels = ["glm-5.3"]\ntok_s = {tok_s}\n'
        'price = {{ input = 1.0, output = 4.0, cache_read = 0.25 }}\nsource = "s"\nas_of = 2026-09-26\n')


def load(text: str) -> catalog.Catalog:
    local = Path(tempfile.mkdtemp()) / "catalog.toml"
    local.write_text("schema = 1\n" + text)
    return catalog.load(local)


class Lookup(unittest.TestCase):
    def test_a_routes_own_vendor_card_is_expected_and_another_vendors_is_a_guideline(self):
        other = load(CARD.format(vendor="commandcode", tok_s=63))
        card, own = other.card("glm", "glm-5.3")
        self.assertEqual((card["vendor"], own), ("commandcode", False))
        both = load(CARD.format(vendor="commandcode", tok_s=63) + CARD.format(vendor="zai", tok_s=90))
        card, own = both.card("glm", "glm-5.3")
        self.assertEqual((card["vendor"], card["tok_s"], own), ("zai", 90, True), "glm spends Z.ai")

    def test_a_local_card_replaces_the_shipped_one_of_its_vendor_name_and_plan(self):
        shipped = catalog.load(Path(tempfile.mkdtemp()) / "none.toml").card("glm", "glm-5.3")[0]
        local = ('[[cards]]\nvendor = "commandcode"\nplan = "goat"\nname = "GLM-5.3"\nmodels = ["glm-5.3"]\n'
                 'tok_s = 1\nsource = "mine"\nas_of = 2026-09-27\n')
        c = load(local)
        self.assertEqual((c.card("glm", "glm-5.3")[0]["tok_s"], shipped["tok_s"]), (1, 63))
        self.assertEqual(sum(x["name"] == "GLM-5.3" for x in c.cards), 1)

    def test_a_card_without_its_source_or_date_is_refused(self):
        with self.assertRaises(catalog.CatalogError):
            load('[[cards]]\nvendor = "zai"\nname = "x"\nsource = "s"\n')
        with self.assertRaises(catalog.CatalogError):
            load('[[cards]]\nvendor = "zai"\nname = "x"\nsource = "s"\nas_of = 2026-09-26\nprice = { fee = 1.0 }\n')


class Observed(unittest.TestCase):
    def test_a_successful_runs_tokens_and_pace_are_its_observed_side(self):
        log = Path(tempfile.mkdtemp()) / "d.jsonl"
        for i, (out, mins) in enumerate(((6000, 4), (12000, 6), (9000, 5))):
            t0 = NOW - timedelta(hours=1 + i)
            aid = outcomes.start(provider="glm", model="glm-5.3", effort="high", task="example", account=None,
                                 decision=None, deadline=1800, now=t0, p=log)
            outcomes.end(aid, outcome="ok", now=t0 + timedelta(minutes=mins),
                         tokens={"in": 50000, "out": out, "cache": 400000}, p=log)
        records, _ = outcomes.read(log)
        s = outcomes.stats(outcomes.attempts(records, NOW), NOW)[("glm", "glm-5.3")]
        self.assertEqual((s["runs"], s["tokens"]["out"], s["tokens"]["cache"]), (3, 9000, 400000))
        self.assertAlmostEqual(s["tok_s"], 30.0)  # 9000 out in 5 minutes: the whole run's pace

    def test_cards_prices_a_run_at_its_cards_price(self):
        home, state = tempfile.mkdtemp(), tempfile.mkdtemp()
        Path(home, "unlimited").mkdir()
        Path(home, "unlimited", "catalog.toml").write_text("schema = 1\n" + CARD.format(vendor="zai", tok_s=90))
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": home, "XDG_STATE_HOME": state}):
            now = datetime.now(timezone.utc)
            aid = outcomes.start(provider="glm", model="glm-5.3", effort=None, task="example", account=None,
                                 decision=None, deadline=1800, now=now - timedelta(minutes=5))
            outcomes.end(aid, outcome="ok", now=now, tokens={"in": 100000, "out": 10000, "cache": 800000})
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(cli.main(["cards", "--tier", "heavy", "--json"]), 0)
        (glm,) = [r for r in json.loads(buf.getvalue()) if r["provider"] == "glm"]
        self.assertEqual((glm["expected"]["vendor"], glm["expected"]["own"]), ("zai", True))
        self.assertAlmostEqual(glm["observed"]["cost_per_run"], (100000 * 1.0 + 10000 * 4.0 + 800000 * 0.25) / 1e6)


if __name__ == "__main__":
    unittest.main()
