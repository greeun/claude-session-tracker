"""`cst rm` 벌크 삭제 — 조건으로 세션을 골라 한 번에 지우는 경로.

`cst done --filter`와 동일한 매칭 규칙(sessionId+cwd+첫 사용자 메시지에 대한
대소문자 무시 부분일치)에, 삭제 전용 시간 필터(--older-than/--before)와
라이브 가드(rm_guard_blocks)를 더한 것. 살아있는 프로세스는 unlink된 inode에
계속 append 하므로 ●/! 세션은 --force 없이는 건드리지 않는다.
"""
import argparse
import importlib.util
import io
import pathlib
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from contextlib import redirect_stderr, redirect_stdout

_TP = pathlib.Path(__file__).resolve().parent.parent / "tracker.py"
_spec = importlib.util.spec_from_file_location("tracker_brm", _TP)
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_brm"] = tk
_spec.loader.exec_module(tk)

NS = lambda **kw: argparse.Namespace(**kw)


def _quiet(fn, *a, **k):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = fn(*a, **k)
    return rc, out.getvalue() + err.getvalue()


class TestRmGuard(unittest.TestCase):
    def test_live_states_block_unless_forced(self):
        for st in (tk.STATUS_WORKING, tk.STATUS_WAITING):
            with self.subTest(status=st):
                self.assertTrue(tk.rm_guard_blocks(st))
                self.assertFalse(tk.rm_guard_blocks(st, force=True))

    def test_idle_ended_done_never_block(self):
        for st in (tk.STATUS_IDLE, tk.STATUS_ENDED, tk.STATUS_DONE):
            with self.subTest(status=st):
                self.assertFalse(tk.rm_guard_blocks(st))
                self.assertFalse(tk.rm_guard_blocks(st, force=True))


def _meta(sid, cwd="/repo/app", msg="", last_ts=None):
    return tk.SessionMeta(session_id=sid,
                          path=pathlib.Path(f"/tmp/{sid[:8]}.jsonl"),
                          cwd=cwd, first_user_msg=msg, last_ts=last_ts)


class _FakeCtx:
    """StatusContext stand-in: fixed sid -> glyph map, empty jobs/done."""

    def __init__(self, status_map=None, jobs=None):
        self._m = status_map or {}
        self.done = set()
        self.jobs = jobs or {}
        self.pins = set()

    def resolve(self, sid):
        return self._m.get(sid, tk.STATUS_ENDED)


class TestRmCutoff(unittest.TestCase):
    def test_none_without_time_flags(self):
        self.assertIsNone(tk._rm_cutoff(NS(older_than=None, before=None)))

    def test_before_parses_as_utc_midnight(self):
        got = tk._rm_cutoff(NS(older_than=None, before="2026-01-01"))
        self.assertEqual(got, datetime(2026, 1, 1, tzinfo=timezone.utc))

    def test_before_rejects_bad_date(self):
        with self.assertRaises(ValueError) as cm:
            tk._rm_cutoff(NS(older_than=None, before="01/01/2026"))
        self.assertIn("--before must be YYYY-MM-DD", str(cm.exception))

    def test_older_than_is_now_minus_days(self):
        got = tk._rm_cutoff(NS(older_than=30, before=None))
        expected = datetime.now(timezone.utc) - timedelta(days=30)
        self.assertLess(abs((got - expected).total_seconds()), 5)

    def test_before_takes_precedence_over_older_than(self):
        got = tk._rm_cutoff(NS(older_than=30, before="2026-01-01"))
        self.assertEqual(got, datetime(2026, 1, 1, tzinfo=timezone.utc))


class TestRmCandidates(unittest.TestCase):
    def setUp(self):
        now = datetime.now(timezone.utc)
        self.old = _meta("aaaa1111-0000-0000-0000-000000000001",
                         cwd="/repo/app", msg="old prototype",
                         last_ts=now - timedelta(days=100))
        self.recent = _meta("bbbb2222-0000-0000-0000-000000000002",
                            cwd="/repo/other", msg="prototype rerun",
                            last_ts=now - timedelta(days=1))
        self.byid = _meta("aaaa1111-9999-0000-0000-000000000003",
                          cwd="/elsewhere", msg="unrelated",
                          last_ts=now - timedelta(days=2))
        self.all = [self.old, self.recent, self.byid]
        self.ctx = _FakeCtx({self.recent.session_id: tk.STATUS_WORKING,
                             self.old.session_id: tk.STATUS_DONE})

    def test_needle_matches_id_cwd_and_message(self):
        by_id = tk._rm_candidates(self.all, self.ctx, "aaaa1111", None, None)
        self.assertEqual({s.session_id for s in by_id},
                         {self.old.session_id, self.byid.session_id})
        by_cwd = tk._rm_candidates(self.all, self.ctx, "/repo/other", None, None)
        self.assertEqual([s.session_id for s in by_cwd], [self.recent.session_id])
        by_msg = tk._rm_candidates(self.all, self.ctx, "PROTOTYPE", None, None)
        self.assertEqual({s.session_id for s in by_msg},
                         {self.old.session_id, self.recent.session_id})

    def test_cutoff_keeps_only_older_sessions(self):
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        got = tk._rm_candidates(self.all, self.ctx, None, None, cutoff)
        self.assertEqual([s.session_id for s in got], [self.old.session_id])

    def test_status_filter_uses_ctx_resolve(self):
        got = tk._rm_candidates(self.all, self.ctx, None, "done", None)
        self.assertEqual([s.session_id for s in got], [self.old.session_id])

    def test_done_sessions_are_kept_unlike_bulk_done(self):
        got = tk._rm_candidates(self.all, self.ctx, None, None, None)
        self.assertIn(self.old.session_id, {s.session_id for s in got})

    def test_filters_compose(self):
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        got = tk._rm_candidates(self.all, self.ctx, "prototype", "done", cutoff)
        self.assertEqual([s.session_id for s in got], [self.old.session_id])


class _TtyStdin(io.StringIO):
    def isatty(self):
        return True


class _NoTtyStdin(io.StringIO):
    def isatty(self):
        return False


class _BulkBase(unittest.TestCase):
    """실제 .jsonl 파일 + 스텁된 load_all_sessions/StatusContext.capture."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._orig = (tk.CACHE_DIR, tk.CACHE_PATH, tk.STATE_PATH,
                      tk.load_all_sessions, tk.StatusContext.capture, sys.stdin)
        tk.CACHE_DIR = self.root
        tk.CACHE_PATH = self.root / "index.json"
        tk.STATE_PATH = self.root / "state.json"

        now = datetime.now(timezone.utc)
        self.metas = {}
        for sid, cwd, msg, age in (
            ("aaaa1111-0000-0000-0000-000000000001", "/repo/app", "old prototype", 100),
            ("bbbb2222-0000-0000-0000-000000000002", "/repo/app", "live work", 0),
            ("cccc3333-0000-0000-0000-000000000003", "/repo/other", "prototype rerun", 5),
        ):
            p = self.root / f"{sid[:8]}.jsonl"
            p.write_text('{"type":"user"}\n', encoding="utf-8")
            self.metas[sid] = tk.SessionMeta(session_id=sid, path=p, cwd=cwd,
                                             first_user_msg=msg,
                                             last_ts=now - timedelta(days=age))
        self.sessions = list(self.metas.values())
        self.status = {"bbbb2222-0000-0000-0000-000000000002": tk.STATUS_WORKING}
        tk.load_all_sessions = lambda **kw: list(self.sessions)
        tk.StatusContext.capture = classmethod(
            lambda cls: _FakeCtx(self.status))
        sys.stdin = _TtyStdin("y\n")

    def tearDown(self):
        (tk.CACHE_DIR, tk.CACHE_PATH, tk.STATE_PATH,
         tk.load_all_sessions, tk.StatusContext.capture, sys.stdin) = self._orig
        self._tmp.cleanup()

    def args(self, **kw):
        base = dict(session_id=[], filter=None, cwd=None, status=None,
                    days=None, older_than=None, before=None,
                    dry_run=False, yes=True, force=False)
        base.update(kw)
        return NS(**base)

    def exists(self, sid):
        return self.metas[sid].path.exists()


class TestBulkRm(_BulkBase):
    def test_filter_removes_matching_files_only(self):
        rc, out = _quiet(tk._bulk_rm, self.args(filter="prototype"))
        self.assertEqual(rc, 0)
        self.assertFalse(self.exists("aaaa1111-0000-0000-0000-000000000001"))
        self.assertFalse(self.exists("cccc3333-0000-0000-0000-000000000003"))
        self.assertTrue(self.exists("bbbb2222-0000-0000-0000-000000000002"))
        self.assertIn("removed 2 session(s)", out)

    def test_live_sessions_skipped_and_reported(self):
        # --cwd는 load_all_sessions(cwd_filter=...)가 처리하고 여기선 스텁이라
        # 필터 없이 전체 풀로 가드 동작만 본다.
        rc, out = _quiet(tk._bulk_rm, self.args(status=None, filter=None))
        self.assertEqual(rc, 0)
        self.assertTrue(self.exists("bbbb2222-0000-0000-0000-000000000002"))
        self.assertIn("skipped", out)
        self.assertIn("bbbb2222", out)
        self.assertIn("--force", out)

    def test_force_includes_live_sessions(self):
        rc, _ = _quiet(tk._bulk_rm, self.args(force=True))
        self.assertEqual(rc, 0)
        self.assertFalse(self.exists("bbbb2222-0000-0000-0000-000000000002"))

    def test_all_matches_live_returns_1_and_deletes_nothing(self):
        self.sessions = [self.metas["bbbb2222-0000-0000-0000-000000000002"]]
        rc, out = _quiet(tk._bulk_rm, self.args())
        self.assertEqual(rc, 1)
        self.assertTrue(self.exists("bbbb2222-0000-0000-0000-000000000002"))
        self.assertIn("Nothing to remove", out)

    def test_no_match_returns_1(self):
        rc, out = _quiet(tk._bulk_rm, self.args(filter="zzz-nothing"))
        self.assertEqual(rc, 1)
        self.assertIn("no sessions matching", out)

    def test_dry_run_deletes_nothing(self):
        rc, out = _quiet(tk._bulk_rm, self.args(filter="prototype", dry_run=True))
        self.assertEqual(rc, 0)
        self.assertTrue(self.exists("aaaa1111-0000-0000-0000-000000000001"))
        self.assertIn("dry run", out)

    def test_non_tty_without_yes_refuses(self):
        sys.stdin = _NoTtyStdin("")
        rc, out = _quiet(tk._bulk_rm, self.args(filter="prototype", yes=False))
        self.assertEqual(rc, 1)
        self.assertTrue(self.exists("aaaa1111-0000-0000-0000-000000000001"))
        self.assertIn("Refusing to remove", out)

    def test_tty_prompt_abort_keeps_files(self):
        sys.stdin = _TtyStdin("n\n")
        rc, out = _quiet(tk._bulk_rm, self.args(filter="prototype", yes=False))
        self.assertEqual(rc, 1)
        self.assertTrue(self.exists("aaaa1111-0000-0000-0000-000000000001"))
        self.assertIn("Aborted", out)

    def test_bad_before_returns_2(self):
        rc, out = _quiet(tk._bulk_rm, self.args(before="01/01/2026"))
        self.assertEqual(rc, 2)
        self.assertIn("--before must be YYYY-MM-DD", out)

    def test_older_than_selects_only_old(self):
        rc, _ = _quiet(tk._bulk_rm, self.args(older_than=30))
        self.assertEqual(rc, 0)
        self.assertFalse(self.exists("aaaa1111-0000-0000-0000-000000000001"))
        self.assertTrue(self.exists("cccc3333-0000-0000-0000-000000000003"))

    def test_delete_purges_done_flag_and_cache_entry(self):
        sid = "aaaa1111-0000-0000-0000-000000000001"
        path = self.metas[sid].path
        tk.set_done(sid, True)
        tk._save_cache({"schema": tk._CACHE_SCHEMA,
                        "entries": {str(path): {"session_id": sid}}})
        rc, _ = _quiet(tk._bulk_rm, self.args(filter="old prototype"))
        self.assertEqual(rc, 0)
        self.assertNotIn(sid, tk.load_state().get("done", {}))
        self.assertNotIn(str(path), tk._load_cache().get("entries", {}))

    def test_long_list_is_capped(self):
        now = datetime.now(timezone.utc)
        extra = []
        for i in range(25):
            sid = f"dddd{i:04d}-0000-0000-0000-00000000{i:04d}"
            p = self.root / f"bulk{i}.jsonl"
            p.write_text("{}\n", encoding="utf-8")
            extra.append(tk.SessionMeta(session_id=sid, path=p, cwd="/repo/bulk",
                                        first_user_msg="capped", last_ts=now))
        self.sessions = extra
        rc, out = _quiet(tk._bulk_rm, self.args(filter="capped", dry_run=True))
        self.assertEqual(rc, 0)
        self.assertIn("… +5 more", out)


if __name__ == "__main__":
    unittest.main()
