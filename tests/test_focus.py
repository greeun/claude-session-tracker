import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tracker


class NormalizeTtyTests(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(tracker._normalize_tty("ttys010"), "/dev/ttys010")

    def test_trailing_whitespace(self):
        self.assertEqual(tracker._normalize_tty("ttys010 \n"), "/dev/ttys010")

    def test_already_dev_prefixed(self):
        self.assertEqual(tracker._normalize_tty("/dev/ttys010"), "/dev/ttys010")

    def test_single_question(self):
        self.assertIsNone(tracker._normalize_tty("?"))

    def test_double_question(self):
        self.assertIsNone(tracker._normalize_tty("??"))

    def test_empty(self):
        self.assertIsNone(tracker._normalize_tty(""))


WEZ_SAMPLE = """
[
  {"window_id": 97, "pane_id": 101, "tty_name": "/dev/ttys015"},
  {"window_id": 95, "pane_id": 99,  "tty_name": "/dev/ttys010"}
]
"""


class WeztermFindPaneTests(unittest.TestCase):
    def test_match_returns_pane_id(self):
        self.assertEqual(
            tracker._wezterm_find_pane_id(WEZ_SAMPLE, "/dev/ttys010"), 99)

    def test_no_match_returns_none(self):
        self.assertIsNone(
            tracker._wezterm_find_pane_id(WEZ_SAMPLE, "/dev/ttys004"))

    def test_bad_json_returns_none(self):
        self.assertIsNone(
            tracker._wezterm_find_pane_id("not json at all", "/dev/ttys010"))

    def test_non_list_returns_none(self):
        self.assertIsNone(tracker._wezterm_find_pane_id("{}", "/dev/ttys010"))


class FocusScriptBuilderTests(unittest.TestCase):
    def test_terminal_script_embeds_tty_and_app(self):
        s = tracker._build_terminal_app_focus_script("/dev/ttys010")
        self.assertIn("/dev/ttys010", s)
        self.assertIn('tell application "Terminal"', s)
        self.assertIn('return "FOCUSED"', s)
        self.assertIn('return "NOMATCH"', s)

    def test_iterm_script_embeds_tty_and_app(self):
        s = tracker._build_iterm2_focus_script("/dev/ttys010")
        self.assertIn("/dev/ttys010", s)
        self.assertIn('tell application "iTerm"', s)
        self.assertIn('return "FOCUSED"', s)
        self.assertIn('return "NOMATCH"', s)


class BackendFallbackTests(unittest.TestCase):
    def test_wezterm_no_match_returns_false(self):
        # A tty that no pane can own → (False, reason); never focuses anything.
        ok, info = tracker._focus_wezterm("/dev/ttys-nonexistent-zzz")
        self.assertFalse(ok)
        self.assertIsInstance(info, str)

    def test_macos_proc_running_returns_bool(self):
        self.assertIsInstance(
            tracker._macos_proc_running("definitely-no-such-proc-zzz"), bool)

    def test_controlling_tty_bad_pid_returns_none(self):
        # PID 0 is not a normal user process with a tty.
        self.assertIsNone(tracker._controlling_tty(0))


class FocusExistingWindowTests(unittest.TestCase):
    def test_missing_pid_returns_false(self):
        ok, info = tracker.focus_existing_window("sid", {})
        self.assertFalse(ok)
        self.assertIsInstance(info, str)

    def test_non_int_pid_returns_false(self):
        ok, _ = tracker.focus_existing_window("sid", {"pid": "nope"})
        self.assertFalse(ok)

    def test_pid_without_tty_returns_false(self):
        # PID 0 has no normal controlling tty → no backend can match.
        ok, _ = tracker.focus_existing_window("sid", {"pid": 0})
        self.assertFalse(ok)


class RunApplescriptFocusTests(unittest.TestCase):
    def test_focused_returns_true(self):
        fake = mock.Mock(returncode=0, stdout="FOCUSED\n")
        with mock.patch("subprocess.run", return_value=fake):
            ok, info = tracker._run_applescript_focus("script", "Terminal.app")
        self.assertTrue(ok)
        self.assertEqual(info, "Terminal.app tab")

    def test_nomatch_returns_false(self):
        fake = mock.Mock(returncode=0, stdout="NOMATCH\n")
        with mock.patch("subprocess.run", return_value=fake):
            ok, _ = tracker._run_applescript_focus("script", "iTerm2")
        self.assertFalse(ok)

    def test_osascript_error_returns_false(self):
        with mock.patch("subprocess.run", side_effect=OSError("boom")):
            ok, _ = tracker._run_applescript_focus("script", "Terminal.app")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
