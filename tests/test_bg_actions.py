"""bg-aware actions: attach (open live session), stop, logs.

cst can see background (agent-view) sessions but its actions were not bg-aware:
- open spawned `claude --resume <sid>` (a transcript FORK), never attaching to
  the live supervisor-hosted session;
- there was no way to stop a bg session (delete only unlinks the transcript,
  leaving the live process running);
- no quick way to peek a bg session's output without attaching.

These wire the real agent-view CLI: `claude attach/stop/logs <short>`, using the
daemonShort that scan_jobs() already captures, keyed by sessionId.
"""
import importlib.util
import io
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

_TP = pathlib.Path(__file__).resolve().parent.parent / "tracker.py"
_spec = importlib.util.spec_from_file_location("tracker_bg", _TP)
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_bg"] = tk
_spec.loader.exec_module(tk)


class TestSessionOpenInvocation(unittest.TestCase):
    def test_job_backed_attaches_by_short(self):
        cmd = tk.session_open_invocation("/bin/claude", "sid-uuid", "18fccc42",
                                         skip_perm=False)
        self.assertEqual(cmd, "/bin/claude attach 18fccc42")

    def test_non_job_resumes_transcript(self):
        cmd = tk.session_open_invocation("/bin/claude", "sid-uuid", None,
                                         skip_perm=False)
        self.assertEqual(cmd, "/bin/claude --resume sid-uuid")

    def test_resume_adds_skip_perm_flag(self):
        cmd = tk.session_open_invocation("claude", "sid", None, skip_perm=True)
        self.assertIn("--dangerously-skip-permissions", cmd)

    def test_attach_ignores_skip_perm(self):
        # attach connects to an existing session — the resume-only flag is N/A.
        cmd = tk.session_open_invocation("claude", "sid", "abc", skip_perm=True)
        self.assertNotIn("--dangerously-skip-permissions", cmd)

    def test_short_is_shell_quoted(self):
        # a hostile short must be single-quoted so the `;` can't break out
        cmd = tk.session_open_invocation("claude", "sid", "a b;rm", False)
        self.assertEqual(cmd, "claude attach 'a b;rm'")


class TestJobShortFor(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = tk.JOBS_DIR
        tk.JOBS_DIR = pathlib.Path(self._tmp.name)

    def tearDown(self):
        tk.JOBS_DIR = self._orig
        self._tmp.cleanup()

    def _mk(self, short, sid):
        d = tk.JOBS_DIR / short
        d.mkdir(parents=True)
        import json
        (d / "state.json").write_text(
            json.dumps({"sessionId": sid, "daemonShort": short, "state": "idle"}))

    def test_returns_short_for_job_backed(self):
        self._mk("18fccc42", "sid-1")
        self.assertEqual(tk.job_short_for("sid-1"), "18fccc42")

    def test_none_for_unknown(self):
        self.assertIsNone(tk.job_short_for("nope"))


class TestBgDeleteWarning(unittest.TestCase):
    def test_warns_when_any_target_is_job_backed(self):
        jobs = {"a": {"short": "x"}, "b": {"short": "y"}}
        w = tk.bg_delete_warning(["a", "c"], jobs)
        self.assertIn("1", w)
        self.assertIn("stop", w.lower())

    def test_empty_when_no_job_targets(self):
        self.assertEqual(tk.bg_delete_warning(["c", "d"], {"a": {}}), "")


class _CmdBase(unittest.TestCase):
    _SID = "abcdef12-3333-4444-5555-666666666666"

    def setUp(self):
        self._orig = (tk.require_session, tk.job_short_for, tk._run_claude)
        self._target = tk.SessionMeta(session_id=self._SID,
                                      path=pathlib.Path("/x.jsonl"), cwd="/repo")
        tk.require_session = lambda _p: self._target
        self._calls = []
        tk._run_claude = lambda argv: (self._calls.append(argv) or 7)

    def tearDown(self):
        (tk.require_session, tk.job_short_for, tk._run_claude) = self._orig


class TestCmdStop(_CmdBase):
    def test_stops_job_backed_session(self):
        tk.job_short_for = lambda _s: "18fccc42"
        rc = tk.cmd_stop(tk.argparse.Namespace(session_id=self._SID))
        self.assertEqual(rc, 7)                       # passthrough rc
        self.assertEqual(self._calls, [["stop", "18fccc42"]])

    def test_refuses_non_bg_session(self):
        tk.job_short_for = lambda _s: None
        err = io.StringIO()
        with redirect_stderr(err):
            rc = tk.cmd_stop(tk.argparse.Namespace(session_id=self._SID))
        self.assertEqual(rc, 1)
        self.assertEqual(self._calls, [])
        self.assertIn("background", err.getvalue().lower())


class TestCmdLogs(_CmdBase):
    def test_logs_job_backed_session(self):
        tk.job_short_for = lambda _s: "18fccc42"
        rc = tk.cmd_logs(tk.argparse.Namespace(session_id=self._SID))
        self.assertEqual(rc, 7)
        self.assertEqual(self._calls, [["logs", "18fccc42"]])

    def test_refuses_non_bg_session(self):
        tk.job_short_for = lambda _s: None
        err = io.StringIO()
        with redirect_stderr(err):
            rc = tk.cmd_logs(tk.argparse.Namespace(session_id=self._SID))
        self.assertEqual(rc, 1)
        self.assertEqual(self._calls, [])


class TestCmdResumeAttach(_CmdBase):
    def _run(self):
        out = io.StringIO()
        with redirect_stdout(out):
            tk.cmd_resume(tk.argparse.Namespace(
                session_id=self._SID, print_only=True, skip_perm=False))
        return out.getvalue()

    def test_print_only_emits_attach_for_bg(self):
        tk.job_short_for = lambda _s: "18fccc42"
        self.assertIn("attach 18fccc42", self._run())

    def test_print_only_emits_resume_for_non_bg(self):
        tk.job_short_for = lambda _s: None
        self.assertIn("--resume", self._run())


class TestSubparsers(unittest.TestCase):
    def test_stop_and_logs_present(self):
        parser = tk._build_parser()
        for cmd in ("stop", "logs"):
            ns = parser.parse_args([cmd, "abc"])
            self.assertEqual(ns.session_id, "abc")


if __name__ == "__main__":
    unittest.main()
