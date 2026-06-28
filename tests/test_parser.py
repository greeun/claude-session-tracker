"""Parser completeness — every cst subcommand parses and binds its handler.

The bulk of _build_parser had no coverage (only done/stop/logs were probed
elsewhere). This pins the whole command table: each registered subcommand
must parse with its required positionals and set `func` to the matching
cmd_* callable, and the registered set must equal EXPECTED (so adding or
removing a subcommand without updating this test fails loudly).
"""
import argparse
import importlib.util
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def load_tracker():
    spec = importlib.util.spec_from_file_location("tracker_under_test", _REPO / "tracker.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tracker_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


tk = load_tracker()

# name -> (extra argv after the subcommand, expected handler function name)
EXPECTED = {
    "pick": ([], "cmd_pick"),
    "list": ([], "cmd_list"),
    "search": (["q"], "cmd_search"),
    "show": (["sid"], "cmd_show"),
    "subagents": (["sid"], "cmd_subagents"),
    "relocate": (["sid", "/tmp"], "cmd_relocate"),
    "export": (["sid"], "cmd_export"),
    "resume": (["sid"], "cmd_resume"),
    "backup": ([], "cmd_backup"),
    "restore": (["arch.tgz"], "cmd_restore"),
    "stats": ([], "cmd_stats"),
    "done": (["sid"], "cmd_done"),
    "bg": ([], "cmd_bg"),
    "jobs": ([], "cmd_jobs"),
    "stop": (["sid"], "cmd_stop"),
    "logs": (["sid"], "cmd_logs"),
    "undone": (["sid"], "cmd_undone"),
    "live": ([], "cmd_live"),
    "prompt-hook": ([], "cmd_prompt_hook"),
    "status-hook": ([], "cmd_status_hook"),
    "install-hook": ([], "cmd_install_hook"),
    "uninstall-hook": ([], "cmd_uninstall_hook"),
}


def _registered_subcommands(parser):
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            return set(a.choices)
    return set()


class TestParserCompleteness(unittest.TestCase):
    def test_registered_set_matches_expected(self):
        got = _registered_subcommands(tk._build_parser())
        self.assertEqual(got, set(EXPECTED),
                         "subcommand table drift — update EXPECTED in test_parser.py")

    def test_each_subcommand_parses_and_binds_func(self):
        for name, (extra, fname) in EXPECTED.items():
            with self.subTest(cmd=name):
                ns = tk._build_parser().parse_args([name, *extra])
                self.assertTrue(hasattr(ns, "func"), f"{name}: no func bound")
                self.assertIs(ns.func, getattr(tk, fname),
                              f"{name}: func != tk.{fname}")
                self.assertTrue(callable(ns.func))

    def test_no_subcommand_defaults_to_no_func(self):
        ns = tk._build_parser().parse_args([])
        self.assertIsNone(getattr(ns, "func", None))

    def test_version_flag_exits(self):
        with self.assertRaises(SystemExit):
            tk._build_parser().parse_args(["--version"])


if __name__ == "__main__":
    unittest.main()
