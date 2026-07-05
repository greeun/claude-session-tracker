import os
import shlex
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tracker


class OpenFolderTests(unittest.TestCase):
    """TUI `o` — open the focused session's folder in a new terminal window
    (plain interactive shell at the recorded cwd, no claude command)."""

    def test_missing_cwd_fails_without_spawning(self):
        with mock.patch.object(tracker, "_open_macos") as mac, \
             mock.patch.object(tracker, "_open_linux") as lin, \
             mock.patch.object(tracker, "_open_in_cmux") as cmux:
            ok, info = tracker.open_folder_in_new_terminal(
                "/nonexistent/cst-open-folder-test")
        self.assertFalse(ok)
        self.assertIn("relocate", info)
        mac.assert_not_called()
        lin.assert_not_called()
        cmux.assert_not_called()

    def test_empty_cwd_fails(self):
        ok, info = tracker.open_folder_in_new_terminal("")
        self.assertFalse(ok)
        self.assertIn("no recorded cwd", info)

    def test_macos_spawns_plain_interactive_shell_at_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(sys, "platform", "darwin"), \
                 mock.patch.object(tracker, "_open_macos",
                                   return_value=(True, "opened in Terminal.app")) as mac:
                ok, info = tracker.open_folder_in_new_terminal(d)
        self.assertTrue(ok)
        mac.assert_called_once()
        _tp, shell_cmd, cwd = mac.call_args[0]
        self.assertEqual(cwd, d)
        self.assertIn(f"cd {shlex.quote(d)}", shell_cmd)
        # `exec` hands the wrapper shell over to the user's interactive shell
        # so the new window stays open instead of closing immediately.
        self.assertIn('exec "${SHELL:-bash}"', shell_cmd)
        # plain shell only — no resume/attach
        self.assertNotIn("claude", shell_cmd)

    def test_linux_spawns_plain_interactive_shell_at_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(sys, "platform", "linux"), \
                 mock.patch.object(tracker, "_open_linux",
                                   return_value=(True, "opened in xterm")) as lin:
                ok, _info = tracker.open_folder_in_new_terminal(d)
        self.assertTrue(ok)
        lin.assert_called_once()
        cwd, shell_cmd = lin.call_args[0]
        self.assertEqual(cwd, d)
        self.assertIn('exec "${SHELL:-bash}"', shell_cmd)
        self.assertNotIn("claude", shell_cmd)

    def test_cmux_mode_routes_to_cmux_with_dir_workspace_name(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(tracker, "_open_in_cmux",
                                   return_value=(True, "opened in cmux workspace")) as cmux:
                ok, _info = tracker.open_folder_in_new_terminal(
                    d, cmux_mode="workspace")
        self.assertTrue(ok)
        cmux.assert_called_once()
        ws_name = cmux.call_args.kwargs.get("ws_name", "")
        self.assertEqual(ws_name, f"dir:{os.path.basename(d)}")
        core_cmd = cmux.call_args[0][3]
        self.assertNotIn("claude", core_cmd)

    def test_resume_cmux_workspace_name_unchanged(self):
        # open_in_new_terminal's cmux path must keep its claude:<sid8> name —
        # ws_name is an optional override, defaulting to the old behavior.
        with mock.patch("shutil.which", return_value="/usr/bin/cmux"), \
             mock.patch("subprocess.Popen") as popen:
            ok, _info = tracker._open_in_cmux(
                "workspace", "/tmp", "/tmp", "true", "abcdef1234567890")
        self.assertTrue(ok)
        argv = popen.call_args[0][0]
        self.assertIn("claude:abcdef12", argv)

    def test_help_documents_the_key(self):
        joined = "\n".join(tracker.HELP_LINES)
        self.assertIn("o / O", joined)


if __name__ == "__main__":
    unittest.main()
