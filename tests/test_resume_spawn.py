import argparse
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tracker


class _StubSession:
    def __init__(self, session_id="s1", cwd="/tmp"):
        self.session_id = session_id
        self.cwd = cwd
        self.last_ts = None


class CmdResumeSpawnTests(unittest.TestCase):
    """`cst resume <id> --spawn` (cst.app's entry point) must actually open or
    focus a terminal, reusing the same logic as the TUI Enter handler — not
    just print instructions."""

    def _args(self, **overrides):
        base = dict(session_id="s1", spawn=True, print_only=False, skip_perm=False)
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_spawn_passes_terminal_override(self):
        stub = _StubSession()
        with mock.patch.object(tracker, "require_session", return_value=stub), \
             mock.patch.object(tracker, "job_short_for", return_value=None), \
             mock.patch.object(tracker, "get_live_session_info", return_value=None), \
             mock.patch.object(tracker, "open_in_new_terminal",
                               return_value=(True, "WezTerm")) as spawn_mock:
            rc = tracker.cmd_resume(self._args(terminal="wezterm"))

        self.assertEqual(rc, 0)
        self.assertEqual(spawn_mock.call_args.kwargs.get("terminal"), "wezterm")

    def test_spawn_resume_opens_new_terminal(self):
        stub = _StubSession()
        with mock.patch.object(tracker, "require_session", return_value=stub), \
             mock.patch.object(tracker, "job_short_for", return_value=None), \
             mock.patch.object(tracker, "get_live_session_info", return_value=None), \
             mock.patch.object(tracker, "open_in_new_terminal",
                               return_value=(True, "Terminal")) as spawn_mock:
            rc = tracker.cmd_resume(self._args())

        self.assertEqual(rc, 0)
        spawn_mock.assert_called_once()
        _args_, kwargs = spawn_mock.call_args
        self.assertIsNone(kwargs.get("attach_short"))

    def test_spawn_attach_passes_job_short(self):
        stub = _StubSession()
        with mock.patch.object(tracker, "require_session", return_value=stub), \
             mock.patch.object(tracker, "job_short_for", return_value="ab12"), \
             mock.patch.object(tracker, "get_live_session_info", return_value=None), \
             mock.patch.object(tracker, "open_in_new_terminal",
                               return_value=(True, "Terminal")) as spawn_mock:
            rc = tracker.cmd_resume(self._args())

        self.assertEqual(rc, 0)
        spawn_mock.assert_called_once()
        _args_, kwargs = spawn_mock.call_args
        self.assertEqual(kwargs.get("attach_short"), "ab12")

    def test_spawn_focuses_live_session_without_opening_new_terminal(self):
        stub = _StubSession()
        with mock.patch.object(tracker, "require_session", return_value=stub), \
             mock.patch.object(tracker, "job_short_for", return_value=None), \
             mock.patch.object(tracker, "get_live_session_info",
                               return_value={"pid": 4321}), \
             mock.patch.object(tracker, "focus_existing_window",
                               return_value=(True, "WezTerm")) as focus_mock, \
             mock.patch.object(tracker, "open_in_new_terminal") as spawn_mock:
            rc = tracker.cmd_resume(self._args())

        self.assertEqual(rc, 0)
        focus_mock.assert_called_once()
        spawn_mock.assert_not_called()

    def test_without_spawn_flag_still_prints_instructions(self):
        stub = _StubSession()
        with mock.patch.object(tracker, "require_session", return_value=stub), \
             mock.patch.object(tracker, "job_short_for", return_value=None), \
             mock.patch.object(tracker, "open_in_new_terminal") as spawn_mock:
            rc = tracker.cmd_resume(self._args(spawn=False))

        self.assertEqual(rc, 0)
        spawn_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
