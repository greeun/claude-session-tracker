import argparse
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tracker


class _StubSession:
    def __init__(self, session_id="s1", cwd="/tmp/proj"):
        self.session_id = session_id
        self.cwd = cwd
        self.last_ts = None


class CmdOpenTests(unittest.TestCase):
    """`cst open <id>` — the TUI `o` key as a subcommand (cst.app's
    folder-open action): spawn a plain shell at the session's cwd via
    open_folder_in_new_terminal(), no claude command involved."""

    def _args(self, session_id="s1", terminal=None):
        return argparse.Namespace(session_id=session_id, terminal=terminal)

    def test_open_spawns_folder_terminal(self):
        stub = _StubSession()
        with mock.patch.object(tracker, "require_session", return_value=stub), \
             mock.patch.object(tracker, "open_folder_in_new_terminal",
                               return_value=(True, "Terminal")) as spawn_mock:
            rc = tracker.cmd_open(self._args())

        self.assertEqual(rc, 0)
        spawn_mock.assert_called_once_with(stub.cwd, terminal=None)

    def test_open_passes_terminal_override(self):
        stub = _StubSession()
        with mock.patch.object(tracker, "require_session", return_value=stub), \
             mock.patch.object(tracker, "open_folder_in_new_terminal",
                               return_value=(True, "WezTerm")) as spawn_mock:
            rc = tracker.cmd_open(self._args(terminal="wezterm"))

        self.assertEqual(rc, 0)
        spawn_mock.assert_called_once_with(stub.cwd, terminal="wezterm")

    def test_open_unknown_session_fails(self):
        with mock.patch.object(tracker, "require_session", return_value=None), \
             mock.patch.object(tracker, "open_folder_in_new_terminal") as spawn_mock:
            rc = tracker.cmd_open(self._args("nope"))

        self.assertEqual(rc, 1)
        spawn_mock.assert_not_called()

    def test_open_spawn_failure_propagates(self):
        stub = _StubSession()
        with mock.patch.object(tracker, "require_session", return_value=stub), \
             mock.patch.object(tracker, "open_folder_in_new_terminal",
                               return_value=(False, "cwd missing")):
            rc = tracker.cmd_open(self._args())

        self.assertEqual(rc, 1)

    def test_parser_wires_open_subcommand(self):
        parser = tracker._build_parser()
        args = parser.parse_args(["open", "abc123"])
        self.assertIs(args.func, tracker.cmd_open)
        self.assertEqual(args.session_id, "abc123")
        self.assertIsNone(args.terminal)

    def test_parser_accepts_terminal_flag(self):
        parser = tracker._build_parser()
        args = parser.parse_args(["open", "abc123", "--terminal", "ghostty"])
        self.assertEqual(args.terminal, "ghostty")
        args = parser.parse_args(["resume", "abc123", "--spawn",
                                  "--terminal", "iterm"])
        self.assertEqual(args.terminal, "iterm")


if __name__ == "__main__":
    unittest.main()
