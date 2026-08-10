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


if __name__ == "__main__":
    unittest.main()
