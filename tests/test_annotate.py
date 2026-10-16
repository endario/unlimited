"""`unlimited annotate --json`: the steering a caller's own readings are given, through the CLI
alone, as a caller in another language runs it."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from unlimited import incentives

import scratch

SRC = str(Path(__file__).resolve().parents[1] / "src")


def run(stdin: str, config: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=SRC, XDG_CONFIG_HOME=config)
    return subprocess.run([sys.executable, "-c", "import sys; from unlimited.cli import main; sys.exit(main())",
                           "annotate", "--json"], input=stdin, capture_output=True, text=True, env=env, timeout=60)


class Annotate(unittest.TestCase):
    def setUp(self):
        self.config = scratch.mkdtemp()
        now = datetime.now(timezone.utc)
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.config}):
            incentives.write([{"target": "neuralwatt", "multiplier": 2, "activated_at": now.isoformat(),
                               "until": (now + timedelta(hours=1)).isoformat()}])

    def test_each_reading_comes_back_at_its_index_with_its_evidence_and_its_steering(self):
        limits = [{"role": "weekly", "window_minutes": 10080, "used": 0.4,
                   "resets_at": "2026-10-03T12:00:00+00:00"}]
        readings = [{"vendor": "openai", "account": "a", "names": ["first"], "status": "ok", "limits": limits},
                    {"vendor": "neuralwatt", "account": "b", "names": [], "status": "ok", "limits": limits},
                    {"vendor": "mimo", "account": "c", "names": [], "status": "unauthorized", "limits": []}]
        done = run(json.dumps(readings), self.config)
        self.assertEqual(done.returncode, 0, done.stderr)
        rows = json.loads(done.stdout)
        self.assertEqual([(r["vendor"], r["account"]) for r in rows], [(r["vendor"], r["account"]) for r in readings],
                         "a caller joins its rows back by index, so order and identity are the contract")
        for given, row in zip(readings, rows):
            self.assertEqual({k: row[k] for k in given}, given, "the evidence is never changed")
            self.assertIsInstance(row["steering"], dict)
            self.assertNotIn("error", row["steering"])
        self.assertEqual([[s["target"] for s in r["steering"]["settings"]] for r in rows], [[], ["neuralwatt"], []],
                         "the machine's steering reaches only the reading it targets")

    def test_no_readings_are_an_empty_array(self):
        done = run("[]", self.config)
        self.assertEqual((done.returncode, json.loads(done.stdout)), (0, []), done.stderr)

    def test_input_that_is_not_an_array_of_readings_is_refused_with_nothing_printed(self):
        for stdin in ("", "{}", "[1]", "not json"):
            done = run(stdin, self.config)
            self.assertEqual((done.returncode, done.stdout), (2, ""), stdin)
            self.assertIn("unlimited: annotate:", done.stderr)

    def test_a_policy_that_cannot_be_loaded_is_stamped_on_each_row_not_a_failure(self):
        Path(self.config, "unlimited", "incentives.json").write_text("{not json")
        done = run(json.dumps([{"vendor": "openai", "account": "a", "status": "ok", "limits": []}]), self.config)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)[0]["steering"]["error"], "policy-unavailable")
        self.assertIn("unlimited: steering: policy-unavailable", done.stderr)


if __name__ == "__main__":
    unittest.main()
