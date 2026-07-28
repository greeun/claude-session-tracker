"""`cst done --filter` — bulk mark-done matching the TUI `/`-filter semantics.

The TUI flow "`/` filter → Ctrl-A mark all → `d` bulk done" as one CLI call.
Matching is a case-insensitive substring over `sessionId + cwd + first user
message` (identical to the TUI `filtered()` pool filter). Already-done
sessions are excluded; ● working sessions are skipped unless --force
(`done_guard_blocks`); confirmation is required unless -y (non-tty without
-y refuses, mirroring `cst rm`). Explicit multi-ID mode marks each ID with
no prompt.
"""
import importlib.util
import io
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

_TP = pathlib.Path(__file__).resolve().parent.parent / "tracker.py"
_spec = importlib.util.spec_from_file_location("tracker_bd", _TP)
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_bd"] = tk
_spec.loader.exec_module(tk)

SID_IDLE = "aaaa1111-0000-0000-0000-000000000001"   # cwd matches, idle
SID_ENDED = "bbbb2222-0000-0000-0000-000000000002"  # first msg matches, ended
SID_WORK = "cccc3333-0000-0000-0000-000000000003"   # cwd matches, ● working
SID_DONE = "dddd4444-0000-0000-0000-000000000004"   # cwd matches, already ✓
SID_MISS = "eeee5555-0000-0000-0000-000000000005"   # matches nothing


class _FakeCtx:
    def __init__(self, status_map, done):
        self._m = status_map
        self.done = set(done)

    def resolve(self, sid):
        if sid in self.done:
            return tk.STATUS_DONE
        return self._m.get(sid, tk.STATUS_ENDED)


class _TtyStdin(io.StringIO):
    def isatty(self):
        return True


class _BulkBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self._tmp.name)
        self._orig = (tk.STATE_PATH, tk.CACHE_DIR, tk.load_all_sessions,
                      tk.StatusContext.capture, tk.require_session, sys.stdin)
        tk.CACHE_DIR = root
        tk.STATE_PATH = root / "state.json"

        def meta(sid, cwd, msg):
            return tk.SessionMeta(session_id=sid,
                                  path=pathlib.Path(f"/{sid[:4]}.jsonl"),
                                  cwd=cwd, first_user_msg=msg)
        self.s_idle = meta(SID_IDLE, "/repo/heyhey-origin", "버그 수정")
        self.s_ended = meta(SID_ENDED, "/repo/other", "deploy heyhey now")
        self.s_work = meta(SID_WORK, "/repo/heyhey-api", "리팩터링")
        self.s_done = meta(SID_DONE, "/repo/heyhey-old", "finished")
        self.s_miss = meta(SID_MISS, "/repo/unrelated", "none")
        self.sessions = [self.s_idle, self.s_ended, self.s_work,
                         self.s_done, self.s_miss]

        self.load_kwargs = {}

        def fake_load(**kw):
            self.load_kwargs = kw
            cwd = kw.get("cwd_filter")
            return [s for s in self.sessions
                    if not cwd or s.cwd.startswith(cwd)]
        tk.load_all_sessions = fake_load

        tk.set_done(SID_DONE, True)
        status_map = {SID_IDLE: tk.STATUS_IDLE, SID_ENDED: tk.STATUS_ENDED,
                      SID_WORK: tk.STATUS_WORKING, SID_MISS: tk.STATUS_IDLE}
        tk.StatusContext.capture = lambda: _FakeCtx(status_map, {SID_DONE})

    def tearDown(self):
        (tk.STATE_PATH, tk.CACHE_DIR, tk.load_all_sessions,
         tk.StatusContext.capture, tk.require_session, sys.stdin) = self._orig
        self._tmp.cleanup()

    @staticmethod
    def _ns(**kw):
        base = dict(session_id=[], filter=None, cwd=None, days=None,
                    status=None, yes=False, force=False)
        base.update(kw)
        return tk.argparse.Namespace(**base)

    def _run(self, ns):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = tk.cmd_done(ns)
        return rc, out.getvalue() + err.getvalue()


class TestBulkFilter(_BulkBase):
    def test_filter_marks_matches_and_skips_working(self):
        rc, out = self._run(self._ns(filter="heyhey", yes=True))
        self.assertEqual(rc, 0)
        done = tk.done_ids()
        self.assertIn(SID_IDLE, done)    # cwd substring match
        self.assertIn(SID_ENDED, done)   # first-user-msg substring match
        self.assertNotIn(SID_WORK, done)  # ● skipped without --force
        self.assertNotIn(SID_MISS, done)
        self.assertIn("working", out.lower())

    def test_force_includes_working(self):
        rc, _ = self._run(self._ns(filter="heyhey", yes=True, force=True))
        self.assertEqual(rc, 0)
        self.assertIn(SID_WORK, tk.done_ids())

    def test_already_done_excluded_from_candidates(self):
        rc, out = self._run(self._ns(filter="heyhey", yes=True))
        self.assertEqual(rc, 0)
        self.assertNotIn(SID_DONE[:8], out)

    def test_filter_is_case_insensitive(self):
        rc, _ = self._run(self._ns(filter="HEYHEY", yes=True))
        self.assertEqual(rc, 0)
        self.assertIn(SID_IDLE, tk.done_ids())

    def test_korean_first_msg_matches(self):
        rc, _ = self._run(self._ns(filter="버그", yes=True))
        self.assertEqual(rc, 0)
        self.assertEqual(tk.done_ids() - {SID_DONE}, {SID_IDLE})

    def test_no_match_returns_1(self):
        rc, out = self._run(self._ns(filter="zzzz-nope", yes=True))
        self.assertEqual(rc, 1)
        self.assertIn("no", out.lower())

    def test_all_matches_working_returns_1(self):
        rc, out = self._run(self._ns(filter="리팩터링", yes=True))
        self.assertEqual(rc, 1)
        self.assertNotIn(SID_WORK, tk.done_ids())

    def test_status_narrows_candidates(self):
        rc, _ = self._run(self._ns(filter="heyhey", status="ended", yes=True))
        self.assertEqual(rc, 0)
        self.assertEqual(tk.done_ids() - {SID_DONE}, {SID_ENDED})

    def test_cwd_days_delegated_to_loader(self):
        self._run(self._ns(filter="heyhey", cwd="/repo/heyhey-origin",
                           days=7, yes=True))
        self.assertEqual(self.load_kwargs.get("cwd_filter"),
                         "/repo/heyhey-origin")
        self.assertEqual(self.load_kwargs.get("days"), 7)


class TestBulkConfirm(_BulkBase):
    def test_non_tty_without_yes_refuses(self):
        sys.stdin = io.StringIO("")
        rc, out = self._run(self._ns(filter="heyhey"))
        self.assertEqual(rc, 1)
        self.assertIn("-y", out)
        self.assertEqual(tk.done_ids(), {SID_DONE})

    def test_tty_decline_aborts(self):
        sys.stdin = _TtyStdin("n\n")
        rc, _ = self._run(self._ns(filter="heyhey"))
        self.assertEqual(rc, 1)
        self.assertEqual(tk.done_ids(), {SID_DONE})

    def test_tty_accept_marks(self):
        sys.stdin = _TtyStdin("y\n")
        rc, _ = self._run(self._ns(filter="heyhey"))
        self.assertEqual(rc, 0)
        self.assertIn(SID_IDLE, tk.done_ids())

    def test_prompt_lists_candidates_first(self):
        sys.stdin = _TtyStdin("n\n")
        _, out = self._run(self._ns(filter="heyhey"))
        self.assertIn(SID_IDLE[:8], out)
        self.assertIn(SID_ENDED[:8], out)


class TestBulkArgErrors(_BulkBase):
    def test_filter_with_ids_is_an_error(self):
        rc, out = self._run(self._ns(session_id=["abc"], filter="x", yes=True))
        self.assertEqual(rc, 1)
        self.assertEqual(tk.done_ids(), {SID_DONE})

    def test_no_ids_no_filter_is_an_error(self):
        rc, _ = self._run(self._ns())
        self.assertEqual(rc, 1)

    def test_narrow_flags_require_filter(self):
        for extra in (dict(cwd="/repo"), dict(days=3), dict(status="ended")):
            rc, out = self._run(self._ns(session_id=["abc"], **extra))
            self.assertEqual(rc, 1, extra)
            self.assertIn("--filter", out)


class TestMultiId(_BulkBase):
    def setUp(self):
        super().setUp()
        by_prefix = {s.session_id[:8]: s for s in self.sessions}
        tk.require_session = lambda p: by_prefix.get(p)

    def test_multiple_ids_marked(self):
        rc, _ = self._run(self._ns(session_id=[SID_IDLE[:8], SID_ENDED[:8]]))
        self.assertEqual(rc, 0)
        self.assertIn(SID_IDLE, tk.done_ids())
        self.assertIn(SID_ENDED, tk.done_ids())

    def test_working_id_skipped_others_marked_rc1(self):
        rc, out = self._run(self._ns(session_id=[SID_IDLE[:8], SID_WORK[:8]]))
        self.assertEqual(rc, 1)
        self.assertIn(SID_IDLE, tk.done_ids())
        self.assertNotIn(SID_WORK, tk.done_ids())
        self.assertIn("working", out.lower())

    def test_legacy_plain_string_session_id_still_works(self):
        # pre-1.12 callers built Namespace(session_id="<id>") — keep working
        rc, _ = self._run(self._ns(session_id=SID_IDLE[:8]))
        self.assertEqual(rc, 0)
        self.assertIn(SID_IDLE, tk.done_ids())


class TestBulkParser(unittest.TestCase):
    def _parse(self, argv):
        return tk._build_parser().parse_args(argv)

    def test_single_id_is_list(self):
        ns = self._parse(["done", "abc"])
        self.assertEqual(ns.session_id, ["abc"])
        self.assertIsNone(ns.filter)
        self.assertFalse(ns.yes)

    def test_multiple_ids(self):
        ns = self._parse(["done", "a", "b", "c"])
        self.assertEqual(ns.session_id, ["a", "b", "c"])

    def test_filter_flags(self):
        ns = self._parse(["done", "--filter", "heyhey", "-y", "--force",
                          "--cwd", "/repo", "--days", "7",
                          "--status", "ended"])
        self.assertEqual(ns.filter, "heyhey")
        self.assertTrue(ns.yes)
        self.assertTrue(ns.force)
        self.assertEqual(ns.cwd, "/repo")
        self.assertEqual(ns.days, 7)
        self.assertEqual(ns.status, "ended")

    def test_bare_done_parses(self):
        ns = self._parse(["done"])  # usage error is raised in cmd_done
        self.assertEqual(ns.session_id, [])


if __name__ == "__main__":
    unittest.main()
