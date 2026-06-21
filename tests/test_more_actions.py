"""Remaining agent-view integrations: dispatch, jobs list, tempo, daemon status.

- `cst bg <prompt>`  — dispatch a new background session (claude --bg).
- `cst jobs`         — list ALL agent-view jobs incl exec/no-transcript ones
                       (the transcript browser can't show transcript-less jobs).
- tempo in job_badge — ∙ marks a job whose process has exited (recoverable),
                       mirroring agent-view's ✻/∙ icon shape (real `tempo` field).
- daemon status      — read ~/.claude/daemon/roster.json (real schema: proto,
                       supervisorPid, updatedAt, workers) for a health line.
"""
import importlib.util
import io
import json as _json
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

_TP = pathlib.Path(__file__).resolve().parent.parent / "tracker.py"
_spec = importlib.util.spec_from_file_location("tracker_ma", _TP)
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_ma"] = tk
_spec.loader.exec_module(tk)


class TestJobBadgeTempo(unittest.TestCase):
    def test_active_process_no_dot(self):
        b = tk.job_badge({"template": "bg", "tempo": "active",
                          "worktreeBranch": "wt"})
        self.assertEqual(b, "[bg ⎇wt]")

    def test_exited_process_gets_dot(self):
        self.assertEqual(tk.job_badge({"template": "bg", "tempo": "idle"}),
                         "[bg ∙]")

    def test_exec_exited(self):
        self.assertEqual(tk.job_badge({"template": "exec", "tempo": "sleeping"}),
                         "[exec ∙]")

    def test_no_tempo_unchanged(self):
        self.assertEqual(tk.job_badge({"template": "bg"}), "[bg]")


class TestDaemonStatus(unittest.TestCase):
    def test_line_not_running(self):
        self.assertIn("not running", tk.daemon_status_line(None).lower())

    def test_line_with_roster(self):
        line = tk.daemon_status_line(
            {"supervisorPid": 47620, "workers": {"a": {}, "b": {}}})
        self.assertIn("47620", line)
        self.assertIn("2", line)

    def test_line_empty_workers(self):
        line = tk.daemon_status_line({"supervisorPid": 1, "workers": {}})
        self.assertIn("0", line)

    def test_read_roster(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        orig = tk.DAEMON_DIR
        tk.DAEMON_DIR = pathlib.Path(tmp.name)
        try:
            (tk.DAEMON_DIR / "roster.json").write_text(
                _json.dumps({"supervisorPid": 9, "workers": {}}))
            self.assertEqual(tk.read_daemon_roster()["supervisorPid"], 9)
        finally:
            tk.DAEMON_DIR = orig

    def test_read_roster_missing(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        orig = tk.DAEMON_DIR
        tk.DAEMON_DIR = pathlib.Path(tmp.name) / "nope"
        try:
            self.assertIsNone(tk.read_daemon_roster())
        finally:
            tk.DAEMON_DIR = orig


class TestCmdBg(unittest.TestCase):
    def setUp(self):
        self._orig = tk._run_claude
        self._calls = []
        tk._run_claude = lambda argv: (self._calls.append(argv) or 0)

    def tearDown(self):
        tk._run_claude = self._orig

    def test_dispatch_prompt(self):
        rc = tk.cmd_bg(tk.argparse.Namespace(prompt=["fix the flaky test"],
                                             name=None))
        self.assertEqual(rc, 0)
        self.assertEqual(self._calls, [["--bg", "fix the flaky test"]])

    def test_dispatch_with_name(self):
        tk.cmd_bg(tk.argparse.Namespace(prompt=["do", "thing"], name="myjob"))
        self.assertEqual(self._calls,
                         [["--bg", "--name", "myjob", "do thing"]])

    def test_empty_prompt_refused(self):
        from contextlib import redirect_stderr
        err = io.StringIO()
        with redirect_stderr(err):
            rc = tk.cmd_bg(tk.argparse.Namespace(prompt=[], name=None))
        self.assertEqual(rc, 1)
        self.assertEqual(self._calls, [])


class TestCmdJobs(unittest.TestCase):
    def setUp(self):
        self._orig_scan = tk.scan_jobs
        self._orig_roster = tk.read_daemon_roster

    def tearDown(self):
        tk.scan_jobs = self._orig_scan
        tk.read_daemon_roster = self._orig_roster

    def _run(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = tk.cmd_jobs(tk.argparse.Namespace())
        return rc, out.getvalue()

    def test_lists_jobs_including_exec(self):
        tk.scan_jobs = lambda: {
            "sid-1": {"state": "blocked", "tempo": "active", "template": "bg",
                      "short": "aaa11111", "cwd": "/repo", "detail": "needs input",
                      "worktreeBranch": "wt-x", "worktreePath": "/w"},
            "sid-2": {"state": "done", "tempo": "idle", "template": "exec",
                      "short": "bbb22222", "cwd": "/repo", "detail": "pytest -x",
                      "worktreeBranch": "", "worktreePath": ""},
        }
        tk.read_daemon_roster = lambda: {"supervisorPid": 5, "workers": {}}
        rc, out = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("aaa11111", out)
        self.assertIn("bbb22222", out)   # exec job listed too
        self.assertIn("pytest -x", out)
        self.assertIn("wt-x", out)

    def test_empty_jobs(self):
        tk.scan_jobs = lambda: {}
        tk.read_daemon_roster = lambda: None
        rc, out = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("no background", out.lower())


class TestSubparsersMore(unittest.TestCase):
    def test_bg_and_jobs_present(self):
        parser = tk._build_parser()
        ns = parser.parse_args(["bg", "fix", "the", "thing"])
        self.assertEqual(ns.prompt, ["fix", "the", "thing"])
        ns2 = parser.parse_args(["jobs"])
        self.assertEqual(ns2.cmd, "jobs")


if __name__ == "__main__":
    unittest.main()
