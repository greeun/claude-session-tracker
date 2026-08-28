"""Coverage for the riskiest untested commands.

Audit (stdlib `trace`) found these had zero test references despite mutating or
deleting files (backup --delete, restore overwrite, relocate transcript rewrite)
or rendering rows (the class of bug that silently broke cmd_list). These tests
exercise the real success paths against temp dirs.
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
from datetime import datetime, timezone

_TP = pathlib.Path(__file__).resolve().parent.parent / "tracker.py"
_spec = importlib.util.spec_from_file_location("tracker_smoke", _TP)
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_smoke"] = tk
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
        self._write_session(self.SID, self.CWD, "2020-01-01T00:00:00Z",
                            "find me search target")

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
                         "message": {"content": text}}) + "\n" +
            _json.dumps({"type": "assistant", "timestamp": ts, "cwd": cwd,
                         "message": {"content": [{"type": "text",
                                                  "text": "ok " + text}]}}) + "\n",
            encoding="utf-8")
        # load_all_sessions(fast=True) derives last_ts from mtime, so age the
        # file to match `ts` (backup/restore select by last_ts).
        epoch = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        os.utime(p, (epoch, epoch))
        return p


class TestRenderSmoke(_Base):
    """Each render command must run and emit output without crashing."""
    def test_search(self):
        rc, out = _quiet(tk.cmd_search,
                         NS(query="search target", limit=20, cwd=None, ignore_case=False))
        self.assertEqual(rc, 0)
        self.assertIn(self.SID[:8], out)

    def test_live_empty_registry(self):
        rc, out = _quiet(tk.cmd_live, NS(all=True))
        self.assertEqual(rc, 0)

    def test_stats(self):
        rc, out = _quiet(tk.cmd_stats, NS(top=5))
        self.assertEqual(rc, 0)
        self.assertIn("1", out)            # one session counted

    def test_show(self):
        rc, out = _quiet(tk.cmd_show,
                         NS(session_id=self.SID[:8], max_chars=200, with_subagents=False))
        self.assertEqual(rc, 0)
        self.assertIn("search target", out)

    def test_subagents_none(self):
        # Fixed (FP-001): 이전 단언은 `assertIn(rc, (0, 1))` 이라 성공과 실패를 모두
        # 통과시켰다 — 서브에이전트가 없을 때의 계약이 무엇인지 고정하지 못하고,
        # 구현이 어느 쪽으로 바뀌어도 회귀를 감지하지 못한다.
        # 사양(cmd_subagents): 대상 세션은 찾았고 서브에이전트만 없으므로 정상 종료(0)
        # 이며 안내 문구를 낸다. 세션 자체를 못 찾은 경우에만 1이다.
        rc, out = _quiet(tk.cmd_subagents, NS(session_id=self.SID[:8]))
        self.assertEqual(rc, 0)
        self.assertIn("has no subagents", out)

    def test_subagents_unknown_session(self):
        # TC-API-102 — 위 계약의 반대편: 세션을 못 찾으면 1
        rc, _ = _quiet(tk.cmd_subagents, NS(session_id="zzzzzzzz"))
        self.assertEqual(rc, 1)

    def test_export(self):
        outp = self.root / "exp.md"
        rc, _ = _quiet(tk.cmd_export,
                       NS(session_id=self.SID[:8], format="md", out=str(outp)))
        self.assertEqual(rc, 0)
        self.assertTrue(outp.exists())
        self.assertIn("search target", outp.read_text(encoding="utf-8"))


class TestBackupRestoreRoundtrip(_Base):
    def test_backup_delete_then_restore(self):
        sess_path = next((tk.PROJECTS_DIR).rglob("*.jsonl"))
        archive = self.root / "bak.tar.gz"
        # backup everything before 2021 (our session is 2020) + DELETE originals
        rc, out = _quiet(tk.cmd_backup, NS(
            days=None, before="2021-01-01", cwd=None, out=str(archive),
            delete=True, force=False, dry_run=False, yes=True))
        self.assertEqual(rc, 0)
        self.assertTrue(archive.exists())
        self.assertFalse(sess_path.exists(), "original must be deleted with --delete")

        # restore into the now-empty projects dir
        rc2, out2 = _quiet(tk.cmd_restore, NS(
            archive=str(archive), cwd=None, on_conflict="skip",
            dry_run=False, yes=True))
        self.assertEqual(rc2, 0)
        self.assertTrue(sess_path.exists(), "session must be restored")
        self.assertEqual(sess_path.name, f"{self.SID}.jsonl")   # same session id
        self.assertIn("search target", sess_path.read_text(encoding="utf-8"))

    def test_backup_dry_run_writes_nothing(self):
        archive = self.root / "none.tar.gz"
        rc, out = _quiet(tk.cmd_backup, NS(
            days=None, before="2021-01-01", cwd=None, out=str(archive),
            delete=True, force=False, dry_run=True, yes=True))
        self.assertEqual(rc, 0)
        self.assertFalse(archive.exists())
        self.assertIn("dry run", out.lower())


class TestRelocate(_Base):
    def test_rewrite_cwd_inplace(self):
        p = next((tk.PROJECTS_DIR).rglob("*.jsonl"))
        tk._rewrite_cwd_inplace(p, "/new/place")
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                self.assertEqual(_json.loads(line)["cwd"], "/new/place")

    def test_relocate_session_moves_and_rewrites(self):
        new_cwd = str(self.root / "moved")
        (self.root / "moved").mkdir()
        old_path = next((tk.PROJECTS_DIR).rglob("*.jsonl"))
        meta = tk.load_session_meta(old_path)
        res = tk.relocate_session(meta, new_cwd)
        self.assertTrue(res.ok, res.message)
        new_path = tk.PROJECTS_DIR / tk.encode_cwd(new_cwd) / old_path.name
        self.assertTrue(new_path.exists())
        self.assertFalse(old_path.exists())
        self.assertIn(new_cwd, new_path.read_text(encoding="utf-8"))

    def test_relocate_dry_run_no_mutation(self):
        new_cwd = str(self.root / "moved2")
        (self.root / "moved2").mkdir()
        old_path = next((tk.PROJECTS_DIR).rglob("*.jsonl"))
        meta = tk.load_session_meta(old_path)
        res = tk.relocate_session(meta, new_cwd, dry_run=True)
        self.assertTrue(res.ok)
        self.assertTrue(old_path.exists())   # unchanged
        self.assertFalse((tk.PROJECTS_DIR / tk.encode_cwd(new_cwd) /
                          old_path.name).exists())


if __name__ == "__main__":
    unittest.main()
