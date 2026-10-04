"""Every command explains itself: an external caller learns the CLI from --help alone."""

from __future__ import annotations

import argparse
import shlex
import unittest

from unlimited import cli


def parsers(p: argparse.ArgumentParser, path: tuple = ()):
    yield path, p
    for action in p._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                yield from parsers(sub, path + (name,))


class Help(unittest.TestCase):
    def test_every_command_has_a_description_and_every_argument_a_help(self):
        for path, p in parsers(cli._parser()):
            with self.subTest(command=" ".join(path) or "unlimited"):
                self.assertTrue(p.description, "no description")
                for a in p._actions:
                    if not isinstance(a, argparse._SubParsersAction):
                        self.assertTrue(a.help, f"{a.dest}: no help")

    def test_the_examples_parse(self):
        # Each example line after `unlimited` in an epilog is a command the parser accepts.
        p = cli._parser()
        for path, sub in parsers(p):
            text = (sub.epilog or "").replace("\\\n", " ")
            for line in text.splitlines():
                line = line.split("#")[0].strip()
                if line.startswith("unlimited ") and "$" not in line:
                    with self.subTest(example=line):
                        p.parse_args(shlex.split(line)[1:])


    def test_verdict_offering_is_separate_from_model_scope(self):
        p = cli._parser()
        a = p.parse_args(["verdict", "--work", "600", "--offering", "gpt-6.1-sol",
                          "--model-scope", "Opus"])
        self.assertEqual((a.offering, a.model_scope), ("gpt-6.1-sol", "Opus"))
        sub = next(q for path, q in parsers(p) if path == ("verdict",))
        self.assertIn("--offering", sub.format_help())
        self.assertIn("model-family", sub.format_help())


if __name__ == "__main__":
    unittest.main()
