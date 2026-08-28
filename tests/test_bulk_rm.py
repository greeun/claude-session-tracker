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
    """StatusContext stand-in: fixed sid -> glyph map, empty jobs/done.

    live/overlay/registry default empty so existing callers (which only care
    about the resolved glyph) are unaffected; C1 tests pass them explicitly to
    simulate a session that reads ✓ done via the glyph map while its process
    is still in the live registry.
    """

    def __init__(self, status_map=None, jobs=None, live=None,
                overlay=None, registry=None):
        self._m = status_map or {}
        self.done = set()
        self.jobs = jobs or {}
        self.pins = set()
        self.live = live or set()
        self.overlay = overlay or {}
        self.registry = registry or {}

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
        self.load_kwargs = {}

        def _stub_load(**kw):
            self.load_kwargs = kw
            return list(self.sessions)

        tk.load_all_sessions = _stub_load
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

    def test_done_and_live_working_is_skipped_by_guard(self):
        """C1: resolve_status lets ✓ done short-circuit liveness, so a session
        that's ✓ AND alive-and-working must still be caught by the delete
        guard — `--status done` is this feature's headline use case, and a
        self `done!` session is ● working by design while it processes that
        very prompt. The row must still *display* ✓ (ctx.resolve), only the
        guard's internal judgement should see past the done flag."""
        live_sid = "bbbb2222-0000-0000-0000-000000000002"
        dead_sid = "aaaa1111-0000-0000-0000-000000000001"
        self.status = {live_sid: tk.STATUS_DONE, dead_sid: tk.STATUS_DONE}
        tk.StatusContext.capture = classmethod(
            lambda cls: _FakeCtx(self.status, live={live_sid},
                                 registry={live_sid: {"status": "busy"}}))
        rc, out = _quiet(tk._bulk_rm, self.args())
        self.assertEqual(rc, 0)
        # done + live + working (registry busy) -> guard still blocks it
        self.assertTrue(self.exists(live_sid))
        self.assertIn("skipped", out)
        self.assertIn(live_sid[:8], out)
        # done + NOT live -> deleted normally (the feature's main use case)
        self.assertFalse(self.exists(dead_sid))
        # the target row still shows the ✓ done glyph, not a live one
        self.assertIn(f"  {tk.STATUS_DONE} {dead_sid[:8]}", out)

    def test_force_includes_done_and_live_sessions(self):
        live_sid = "bbbb2222-0000-0000-0000-000000000002"
        self.status = {live_sid: tk.STATUS_DONE}
        tk.StatusContext.capture = classmethod(
            lambda cls: _FakeCtx(self.status, live={live_sid},
                                 registry={live_sid: {"status": "busy"}}))
        rc, _ = _quiet(tk._bulk_rm, self.args(force=True, yes=True))
        self.assertEqual(rc, 0)
        self.assertFalse(self.exists(live_sid))

    def test_force_without_yes_still_confirms_non_tty(self):
        """I1/I3: in bulk mode --force only widens the target set (live
        inclusion) — it must NOT also skip the confirmation gate the way the
        single-id `_rm_one` --force does. Matches `_bulk_done`."""
        sys.stdin = _NoTtyStdin("")
        rc, out = _quiet(tk._bulk_rm,
                         self.args(filter="prototype", yes=False, force=True))
        self.assertEqual(rc, 1)
        self.assertTrue(self.exists("aaaa1111-0000-0000-0000-000000000001"))
        self.assertTrue(self.exists("cccc3333-0000-0000-0000-000000000003"))
        self.assertIn("Refusing to remove", out)

    def test_force_without_yes_prompts_on_tty(self):
        sys.stdin = _TtyStdin("n\n")
        rc, out = _quiet(tk._bulk_rm,
                         self.args(filter="prototype", yes=False, force=True))
        self.assertEqual(rc, 1)
        self.assertTrue(self.exists("aaaa1111-0000-0000-0000-000000000001"))
        self.assertIn("Aborted", out)

    def test_cwd_and_days_pass_through_to_load_all_sessions(self):
        rc, _ = _quiet(tk._bulk_rm, self.args(cwd="/repo/app", days=7))
        self.assertEqual(rc, 0)
        self.assertEqual(self.load_kwargs.get("cwd_filter"), "/repo/app")
        self.assertEqual(self.load_kwargs.get("days"), 7)

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


class TestCmdRmDispatch(_BulkBase):
    def test_id_plus_selector_is_an_error(self):
        rc, out = _quiet(tk.cmd_rm, self.args(session_id=["aaaa1111"],
                                              filter="prototype"))
        self.assertEqual(rc, 1)
        self.assertIn("not both", out)
        self.assertTrue(self.exists("aaaa1111-0000-0000-0000-000000000001"))

    def test_no_id_and_no_selector_is_an_error(self):
        rc, out = _quiet(tk.cmd_rm, self.args())
        self.assertEqual(rc, 1)
        self.assertIn("required", out)
        self.assertTrue(self.exists("aaaa1111-0000-0000-0000-000000000001"))

    def test_selector_routes_to_bulk(self):
        rc, out = _quiet(tk.cmd_rm, self.args(filter="prototype"))
        self.assertEqual(rc, 0)
        self.assertIn("removed 2 session(s)", out)

    def _stub_require_session(self):
        """id prefix -> setUp이 만든 SessionMeta. addCleanup으로 원복."""
        orig = tk.require_session
        self.addCleanup(lambda: setattr(tk, "require_session", orig))
        tk.require_session = lambda p: next(
            (m for m in self.metas.values() if m.session_id.startswith(p)), None)

    def test_multiple_ids_remove_each(self):
        self._stub_require_session()
        rc, _ = _quiet(tk.cmd_rm, self.args(session_id=["aaaa1111", "cccc3333"]))
        self.assertEqual(rc, 0)
        self.assertFalse(self.exists("aaaa1111-0000-0000-0000-000000000001"))
        self.assertFalse(self.exists("cccc3333-0000-0000-0000-000000000003"))

    def test_string_session_id_still_works(self):
        """메뉴바 앱과 tests/test_rm.py는 Namespace(session_id="<str>")를 넘긴다."""
        self._stub_require_session()
        rc, _ = _quiet(tk.cmd_rm, self.args(session_id="aaaa1111"))
        self.assertEqual(rc, 0)
        self.assertFalse(self.exists("aaaa1111-0000-0000-0000-000000000001"))


class TestRmGuardStatus(unittest.TestCase):
    """_rm_guard_status — 가드가 *판정할* 상태 (행에 *표시될* 글리프와 다르다).

    resolve_status 는 ✓ done 을 만나면 생존 여부를 보기도 전에 단락한다. 그래서
    "✓ done 인데 아직 살아서 working" 인 세션은 ✓ 로 해석되고 rm_guard_blocks("✓")
    는 언제나 False — 라이브 가드가 조용히 무력화된다. _rm_guard_status 는 그
    경우에만 done 플래그를 지나쳐 재분류한다.

    재분류 조건에 `session_id in ctx.live` 가 붙어 있는 이유가 두 번째 불변식이다:
    죽은 백그라운드 job 의 마지막 저장 상태가 우연히 "working" 이면, 그 조건이
    없을 때 _JOB_STATE_GLYPH 를 타고 ● 로 재분류되어 **이미 죽은 세션을 삭제할 수
    없게** 만든다 (거짓 음성이 아니라 거짓 차단).

    기존 test_done_and_live_working_is_skipped_by_guard 는 첫 번째 불변식만
    _bulk_rm 경유로 확인한다. 여기서는 두 번째 불변식을 직접 고정한다.
    """

    SID = "cccc3333-0000-0000-0000-000000000003"

    def test_done_and_dead_bg_job_working_is_not_reclassified(self):
        # TC-UNIT-121 — 죽었지만 job 의 마지막 상태가 working 인 ✓ 세션
        ctx = _FakeCtx({self.SID: tk.STATUS_DONE},
                       jobs={self.SID: {"state": "working"}},
                       live=set())
        st = tk._rm_guard_status(self.SID, ctx)
        self.assertEqual(st, tk.STATUS_DONE)
        self.assertFalse(tk.rm_guard_blocks(st),
                         "죽은 bg 세션이 거짓 차단됐다")

    def test_done_and_live_working_is_reclassified_and_blocks(self):
        # TC-UNIT-122
        ctx = _FakeCtx({self.SID: tk.STATUS_DONE},
                       live={self.SID},
                       registry={self.SID: {"status": "busy"}})
        st = tk._rm_guard_status(self.SID, ctx)
        self.assertEqual(st, tk.STATUS_WORKING)
        self.assertTrue(tk.rm_guard_blocks(st))

    def test_done_and_dead_without_job_stays_done(self):
        # TC-UNIT-123
        ctx = _FakeCtx({self.SID: tk.STATUS_DONE})
        st = tk._rm_guard_status(self.SID, ctx)
        self.assertEqual(st, tk.STATUS_DONE)
        self.assertFalse(tk.rm_guard_blocks(st))

    def test_non_done_status_is_passed_through_untouched(self):
        """done 이 아니면 재분류 경로 자체가 열리지 않는다 — ctx.resolve 값 그대로."""
        for st_in in (tk.STATUS_WORKING, tk.STATUS_WAITING,
                      tk.STATUS_IDLE, tk.STATUS_ENDED):
            with self.subTest(status=st_in):
                ctx = _FakeCtx({self.SID: st_in},
                               live={self.SID},
                               registry={self.SID: {"status": "idle"}})
                self.assertEqual(tk._rm_guard_status(self.SID, ctx), st_in)

    def test_done_and_live_idle_reclassifies_to_idle_and_does_not_block(self):
        """살아 있어도 idle 이면 차단하지 않는다 — 가드는 ●/! 만 막는다."""
        ctx = _FakeCtx({self.SID: tk.STATUS_DONE},
                       live={self.SID},
                       registry={self.SID: {"status": "idle"}})
        st = tk._rm_guard_status(self.SID, ctx)
        self.assertEqual(st, tk.STATUS_IDLE)
        self.assertFalse(tk.rm_guard_blocks(st))


if __name__ == "__main__":
    unittest.main()
