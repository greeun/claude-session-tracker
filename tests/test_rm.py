"""`cst rm <id>` — remove (unlink) a session transcript from the CLI.

Additive subcommand that reuses the same `_delete_sessions` path as the TUI
`Del` key. These tests isolate the same way the other command tests do
(test_cmd_smoke.py): monkeypatch the module-level path globals onto a temp dir,
write one real `.jsonl` transcript, then call `cmd_rm` directly with a
Namespace. No subprocess/HOME juggling — that mirrors the existing suite.
"""
import importlib.util
import io
import json as _json
import os
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

_TP = pathlib.Path(__file__).resolve().parent.parent / "tracker.py"
_spec = importlib.util.spec_from_file_location("tracker_rm", _TP)
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_rm"] = tk
_spec.loader.exec_module(tk)

NS = lambda **kw: tk.argparse.Namespace(**kw)


def _quiet(fn, *a, **k):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = fn(*a, **k)
    return rc, out.getvalue() + err.getvalue()


class _Base(unittest.TestCase):
    """Temp PROJECTS_DIR/CACHE/etc + one real session transcript."""
    SID = "aaaaaaaa-1111-2222-3333-444444444444"
    CWD = "/repo/app"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._orig = {k: getattr(tk, k) for k in (
            "PROJECTS_DIR", "CACHE_DIR", "CACHE_PATH", "STATE_PATH",
            "JOBS_DIR", "DAEMON_DIR", "SESSIONS_REGISTRY_DIR")}
        tk.PROJECTS_DIR = self.root / "projects"
        tk.CACHE_DIR = self.root / "cache"
        tk.CACHE_PATH = tk.CACHE_DIR / "index.json"
        tk.STATE_PATH = tk.CACHE_DIR / "state.json"
        tk.JOBS_DIR = self.root / "jobs"
        tk.DAEMON_DIR = self.root / "daemon"
        tk.SESSIONS_REGISTRY_DIR = self.root / "sessions"
        self.path = self._write_session(
            self.SID, self.CWD, "2020-01-01T00:00:00Z", "remove me please")

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(tk, k, v)
        self._tmp.cleanup()

    def _write_session(self, sid, cwd, ts, text):
        d = tk.PROJECTS_DIR / tk.encode_cwd(cwd)
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{sid}.jsonl"
        p.write_text(
            _json.dumps({"type": "user", "timestamp": ts, "cwd": cwd,
                         "message": {"content": text}}) + "\n",
            encoding="utf-8")
        from datetime import datetime
        epoch = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        os.utime(p, (epoch, epoch))
        return p


class TestRm(_Base):
    def test_rm_dry_run_keeps_file(self):
        rc, out = _quiet(tk.cmd_rm, NS(
            session_id=self.SID[:8], dry_run=True, yes=False, force=False))
        self.assertEqual(rc, 0)
        self.assertTrue(self.path.exists(), "dry-run must NOT unlink the file")
        self.assertIn(self.SID[:8], out)          # names the target

    def test_rm_yes_unlinks(self):
        rc, out = _quiet(tk.cmd_rm, NS(
            session_id=self.SID[:8], dry_run=False, yes=True, force=False))
        self.assertEqual(rc, 0)
        self.assertFalse(self.path.exists(), "-y must unlink the transcript")
        self.assertIn(self.SID[:8], out)

    def test_rm_missing_id_errors(self):
        rc, out = _quiet(tk.cmd_rm, NS(
            session_id="deadbeef", dry_run=True, yes=False, force=False))
        self.assertEqual(rc, 1)
        self.assertTrue(self.path.exists())       # untouched

    def test_rm_noninteractive_without_yes_refuses(self):
        # stdin is not a tty under the test runner → must refuse, not hang.
        rc, out = _quiet(tk.cmd_rm, NS(
            session_id=self.SID[:8], dry_run=False, yes=False, force=False))
        self.assertEqual(rc, 1)
        self.assertTrue(self.path.exists(), "refusal must not unlink")
        self.assertIn("-y", out)


if __name__ == "__main__":
    unittest.main()
