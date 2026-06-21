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
  {"window_id": 97, "pane_id": 101, "tty_name": "/dev/ttys015", "window_title": "✳ task A"},
  {"window_id": 95, "pane_id": 99,  "tty_name": "/dev/ttys010", "window_title": "⠂ task B"}
]
"""


class WeztermFindPaneTests(unittest.TestCase):
    def test_match_returns_pane_dict(self):
        p = tracker._wezterm_find_pane(WEZ_SAMPLE, "/dev/ttys010")
        self.assertIsInstance(p, dict)
        self.assertEqual(p["pane_id"], 99)

    def test_no_match_returns_none(self):
        self.assertIsNone(tracker._wezterm_find_pane(WEZ_SAMPLE, "/dev/ttys004"))

    def test_bad_json_returns_none(self):
        self.assertIsNone(tracker._wezterm_find_pane("not json at all", "/dev/ttys010"))

    def test_non_list_returns_none(self):
        self.assertIsNone(tracker._wezterm_find_pane("{}", "/dev/ttys010"))


class StripStatusGlyphTests(unittest.TestCase):
    def test_strips_asterisk_glyph(self):
        self.assertEqual(tracker._strip_status_glyph("✳ Bring CST to foreground"),
                         "Bring CST to foreground")

    def test_strips_braille_spinner(self):
        self.assertEqual(tracker._strip_status_glyph("⠂ Re-transcribe audio"),
                         "Re-transcribe audio")

    def test_plain_title_unchanged(self):
        self.assertEqual(tracker._strip_status_glyph("Manage CLI sessions"),
                         "Manage CLI sessions")

    def test_leading_whitespace_trimmed(self):
        self.assertEqual(tracker._strip_status_glyph("   spaced title"),
                         "spaced title")

    def test_all_glyphs_falls_back_to_original(self):
        self.assertEqual(tracker._strip_status_glyph("✳⠂"), "✳⠂")


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


class WeztermAxraiseScriptTests(unittest.TestCase):
    def test_embeds_needle_and_axraise(self):
        s = tracker._build_wezterm_axraise_script("Manage CLI sessions")
        self.assertIn("Manage CLI sessions", s)
        self.assertIn("wezterm-gui", s)
        self.assertIn("AXRaise", s)
        self.assertIn('return "FOCUSED"', s)
        self.assertIn('return "NOMATCH"', s)


class WeztermGuiSocketsTests(unittest.TestCase):
    def test_filters_dead_pids_and_non_pid_names(self):
        fake = ["/x/gui-sock-111", "/x/gui-sock-222", "/x/gui-sock-notapid"]
        with mock.patch("glob.glob", return_value=fake), \
             mock.patch.object(tracker, "_pid_alive",
                               side_effect=lambda p: p == 111):
            socks = tracker._wezterm_gui_sockets()
        self.assertEqual(socks, ["/x/gui-sock-111"])

    def test_no_socket_dir_returns_empty(self):
        with mock.patch("glob.glob", return_value=[]):
            self.assertEqual(tracker._wezterm_gui_sockets(), [])


CMUX_DEBUG_SAMPLE = """\
[0] surface:10 (33A0D740-0666-43C2-85E0-501ABA4CF0AC) "task A" mapped=1 tree=1 window=window:1 (7495C250-43F7-4714-BD08-443113F7153E) workspace=workspace:1 (A9A05530-568D-4727-BBCF-06FA05A7CDFE) pane=pane:10 (579596E1-9EEE-4959-9AD1-38DC7D3785BB) bonsplitTab=D0B46344 ctx=split
    runtime=1 focused=0 terminal=0x0000000b9da44380 ghostty=0x0000000ba11b8000 portal=live#1
    tty=ttys005 cwd=/x branch=develop ports=[] visible=1
[1] surface:12 mapped=0 tree=0 window=nil workspace=nil pane=nil bonsplitTab=nil ctx=tab
    runtime=0 terminal=0x1 ghostty=nil
    tty=nil cwd=nil
[2] surface:5 (E189283E-9DFC-4E23-8FC2-DD04E50CBD7B) "task B" mapped=1 tree=1 window=window:2 (7F229B95-16C5-4E9B-BF4C-8151B01AC4AC) workspace=workspace:2 (F6376A2E-1D26-4C61-A300-BF8F5A97D718) pane=pane:5 (09C4ECD7-5F86-48BA-8610-A27CC28C6D2B) bonsplitTab=3B ctx=split
    runtime=1
    tty=ttys002 cwd=/y branch=main
"""


class CmuxLocateSurfaceTests(unittest.TestCase):
    def test_match_first_surface(self):
        loc = tracker._cmux_locate_surface(CMUX_DEBUG_SAMPLE, "ttys005")
        self.assertEqual(loc["window"], "7495C250-43F7-4714-BD08-443113F7153E")
        self.assertEqual(loc["workspace"], "A9A05530-568D-4727-BBCF-06FA05A7CDFE")
        self.assertEqual(loc["pane"], "579596E1-9EEE-4959-9AD1-38DC7D3785BB")

    def test_match_later_surface(self):
        loc = tracker._cmux_locate_surface(CMUX_DEBUG_SAMPLE, "ttys002")
        self.assertEqual(loc["window"], "7F229B95-16C5-4E9B-BF4C-8151B01AC4AC")
        self.assertEqual(loc["pane"], "09C4ECD7-5F86-48BA-8610-A27CC28C6D2B")

    def test_no_match_returns_none(self):
        self.assertIsNone(tracker._cmux_locate_surface(CMUX_DEBUG_SAMPLE, "ttys999"))

    def test_unmapped_surface_ignored(self):
        # surface:12 has tty=nil and window=nil — never a valid target.
        self.assertIsNone(tracker._cmux_locate_surface(CMUX_DEBUG_SAMPLE, "nil"))

    def test_match_without_window_returns_none(self):
        sample = (
            "[0] surface:1 (X) m=1 window=nil workspace=nil pane=nil ctx=tab\n"
            "    tty=ttys777 cwd=/z\n"
        )
        self.assertIsNone(tracker._cmux_locate_surface(sample, "ttys777"))


def _fake_cmux_run(sample, debug_rc=0, focus_rc=0):
    def run(cmd, *a, **k):
        if "debug-terminals" in cmd:
            return mock.Mock(returncode=debug_rc, stdout=sample)
        return mock.Mock(returncode=focus_rc, stdout="OK\n")
    return run


class FocusCmuxTests(unittest.TestCase):
    def test_cmux_not_found_returns_false(self):
        with mock.patch("shutil.which", return_value=None):
            ok, info = tracker._focus_cmux("/dev/ttys005")
        self.assertFalse(ok)
        self.assertEqual(info, "cmux not found")

    def test_success_raises_workspace(self):
        with mock.patch("shutil.which", return_value="/bin/cmux"), \
             mock.patch("subprocess.run",
                        side_effect=_fake_cmux_run(CMUX_DEBUG_SAMPLE)):
            ok, info = tracker._focus_cmux("/dev/ttys005")
        self.assertTrue(ok)
        self.assertEqual(info, "cmux workspace")

    def test_no_surface_for_tty_returns_false(self):
        with mock.patch("shutil.which", return_value="/bin/cmux"), \
             mock.patch("subprocess.run",
                        side_effect=_fake_cmux_run(CMUX_DEBUG_SAMPLE)):
            ok, info = tracker._focus_cmux("/dev/ttys999")
        self.assertFalse(ok)
        self.assertIn("ttys999", info)

    def test_debug_terminals_error_returns_false(self):
        with mock.patch("shutil.which", return_value="/bin/cmux"), \
             mock.patch("subprocess.run",
                        side_effect=_fake_cmux_run("", debug_rc=1)):
            ok, info = tracker._focus_cmux("/dev/ttys005")
        self.assertFalse(ok)
        self.assertEqual(info, "cmux debug-terminals error")

    def test_focus_window_failure_returns_false(self):
        with mock.patch("shutil.which", return_value="/bin/cmux"), \
             mock.patch("subprocess.run",
                        side_effect=_fake_cmux_run(CMUX_DEBUG_SAMPLE, focus_rc=1)):
            ok, info = tracker._focus_cmux("/dev/ttys005")
        self.assertFalse(ok)
        self.assertEqual(info, "cmux focus-window failed")


class CmuxOrderTests(unittest.TestCase):
    def test_cmux_probed_first_inside_cmux_workspace(self):
        with mock.patch.object(tracker, "_controlling_tty",
                               return_value="/dev/ttys005"), \
             mock.patch("shutil.which", return_value="/bin/cmux"), \
             mock.patch.object(tracker, "_macos_proc_running", return_value=True), \
             mock.patch.object(tracker, "_focus_cmux",
                               return_value=(True, "cmux workspace")) as fc, \
             mock.patch.dict(os.environ,
                             {"CMUX_WORKSPACE_ID": "X", "TERM_PROGRAM": "ghostty"}):
            ok, info = tracker.focus_existing_window("sid", {"pid": 4321})
        self.assertTrue(ok)
        self.assertEqual(info, "cmux workspace")
        fc.assert_called_once_with("/dev/ttys005")


if __name__ == "__main__":
    unittest.main()
