"""Display enrichment for background (agent-view) sessions.

A job-backed row carries metadata the transcript alone can't: which agent-view
`template` it is (exec vs model bg) and which git worktree branch it is editing
on. `job_badge()` renders a compact tag (`[bg ⎇branch]` / `[exec]`) appended to
the row so triaging bg sessions doesn't require attaching. Branch comes from the
real state.json `worktreeBranch` field (verified on Claude Code 2.1.x).
"""
import importlib.util
import json as _json
import pathlib
import sys
import tempfile
import unittest

_TP = pathlib.Path(__file__).resolve().parent.parent / "tracker.py"
_spec = importlib.util.spec_from_file_location("tracker_jd", _TP)
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_jd"] = tk
_spec.loader.exec_module(tk)


class TestScanJobsWorktree(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = tk.JOBS_DIR
        tk.JOBS_DIR = pathlib.Path(self._tmp.name)

    def tearDown(self):
        tk.JOBS_DIR = self._orig
        self._tmp.cleanup()

    def test_captures_worktree_branch_and_path(self):
        d = tk.JOBS_DIR / "26a496e8"
        d.mkdir()
        (d / "state.json").write_text(_json.dumps({
            "sessionId": "sid-1", "daemonShort": "26a496e8", "state": "working",
            "template": "bg",
            "worktreeBranch": "worktree-cst-probe",
            "worktreePath": "/repo/.claude/worktrees/cst-probe",
        }))
        rec = tk.scan_jobs()["sid-1"]
        self.assertEqual(rec["worktreeBranch"], "worktree-cst-probe")
        self.assertEqual(rec["worktreePath"],
                         "/repo/.claude/worktrees/cst-probe")

    def test_missing_worktree_fields_default_empty(self):
        d = tk.JOBS_DIR / "x"
        d.mkdir()
        (d / "state.json").write_text(_json.dumps({
            "sessionId": "s", "daemonShort": "x", "state": "done",
            "template": "exec"}))
        rec = tk.scan_jobs()["s"]
        self.assertEqual(rec["worktreeBranch"], "")
        self.assertEqual(rec["worktreePath"], "")


class TestJobBadge(unittest.TestCase):
    def test_none_is_empty(self):
        self.assertEqual(tk.job_badge(None), "")
        self.assertEqual(tk.job_badge({}), "")

    def test_exec_template(self):
        self.assertEqual(tk.job_badge({"template": "exec"}), "[exec]")

    def test_bg_without_branch(self):
        self.assertEqual(tk.job_badge({"template": "bg"}), "[bg]")

    def test_bg_with_worktree_branch(self):
        b = tk.job_badge({"template": "bg", "worktreeBranch": "worktree-fix"})
        self.assertEqual(b, "[bg ⎇worktree-fix]")

    def test_exec_ignores_branch(self):
        # exec jobs never have a worktree; branch (if somehow set) is ignored
        self.assertEqual(
            tk.job_badge({"template": "exec", "worktreeBranch": "x"}), "[exec]")


if __name__ == "__main__":
    unittest.main()
