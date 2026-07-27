import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tracker


def _fake_cli(dirpath: str, name: str) -> str:
    path = os.path.join(dirpath, name)
    with open(path, "w") as f:
        f.write("#!/bin/sh\n")
    os.chmod(path, 0o755)
    return path


class FindTerminalCliTests(unittest.TestCase):
    """macOS terminal apps (WezTerm, Ghostty, kitty, Alacritty) don't install
    their bundled CLI onto PATH, so lookup must fall back to app-bundle paths."""

    def test_path_hit_wins_over_bundle(self):
        with mock.patch("shutil.which", return_value="/usr/bin/wezterm"):
            self.assertEqual(tracker._find_terminal_cli("wezterm"),
                             "/usr/bin/wezterm")

    def test_falls_back_to_app_bundle_when_not_on_path(self):
        with tempfile.TemporaryDirectory() as td:
            fake = _fake_cli(td, "wezterm")
            with mock.patch("shutil.which", return_value=None), \
                 mock.patch.dict(tracker._MACOS_TERM_APP_CLIS,
                                 {"wezterm": (fake,)}):
                self.assertEqual(tracker._find_terminal_cli("wezterm"), fake)

    def test_missing_everywhere_returns_none(self):
        with mock.patch("shutil.which", return_value=None), \
             mock.patch.dict(tracker._MACOS_TERM_APP_CLIS,
                             {"wezterm": ("/nonexistent/wezterm",)}):
            self.assertIsNone(tracker._find_terminal_cli("wezterm"))


class OpenMacosBundleFallbackTests(unittest.TestCase):
    """TERM_PROGRAM=WezTerm with the CLI missing from PATH must still spawn
    WezTerm via the app-bundle CLI — not silently fall back to Terminal.app."""

    def test_wezterm_bundle_cli_used_when_not_on_path(self):
        with tempfile.TemporaryDirectory() as td:
            fake = _fake_cli(td, "wezterm")
            with mock.patch("shutil.which", return_value=None), \
                 mock.patch.dict(tracker._MACOS_TERM_APP_CLIS,
                                 {"wezterm": (fake,)}), \
                 mock.patch("subprocess.Popen") as popen, \
                 mock.patch.object(tracker, "_activate_macos_app"):
                ok, info = tracker._open_macos("WezTerm", "echo hi", "/tmp")
        self.assertTrue(ok)
        self.assertIn("WezTerm", info)
        argv = popen.call_args[0][0]
        self.assertEqual(argv[0], fake)


class FocusWeztermBundleFallbackTests(unittest.TestCase):
    """The focus path must find the bundle CLI too, or live sessions spawn a
    duplicate window instead of raising the existing one."""

    def test_focus_wezterm_gets_past_cli_lookup(self):
        with tempfile.TemporaryDirectory() as td:
            fake = _fake_cli(td, "wezterm")
            with mock.patch("shutil.which", return_value=None), \
                 mock.patch.dict(tracker._MACOS_TERM_APP_CLIS,
                                 {"wezterm": (fake,)}), \
                 mock.patch.object(tracker, "_wezterm_gui_sockets",
                                   return_value=[]), \
                 mock.patch.object(tracker, "_wezterm_cli_list",
                                   return_value=None):
                ok, info = tracker._focus_wezterm("ttys001")
        self.assertFalse(ok)
        self.assertEqual(info, "no wezterm pane for tty")

    def test_focus_existing_window_probes_wezterm_backend(self):
        env = {"TERM_PROGRAM": "WezTerm"}
        with tempfile.TemporaryDirectory() as td:
            fake = _fake_cli(td, "wezterm")
            with mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch("shutil.which", return_value=None), \
                 mock.patch.dict(tracker._MACOS_TERM_APP_CLIS,
                                 {"wezterm": (fake,)}), \
                 mock.patch.object(tracker, "_controlling_tty",
                                   return_value="ttys001"), \
                 mock.patch.object(tracker, "_focus_wezterm",
                                   return_value=(True, "raised wezterm")):
                os.environ.pop("CMUX_WORKSPACE_ID", None)
                ok, info = tracker.focus_existing_window(
                    "s1", {"pid": 12345})
        self.assertTrue(ok)
        self.assertEqual(info, "raised wezterm")


if __name__ == "__main__":
    unittest.main()
