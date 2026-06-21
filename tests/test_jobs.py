"""Tests for the ~/.claude/jobs background-session scanner.

Background (agent-view) sessions are hosted by the supervisor, not the pid
registry under ~/.claude/sessions. Once the supervisor stops an idle bg
process, the session vanishes from the registry and cst used to show it as
○ ended — even though it is recoverable and may be working/waiting. The jobs
scanner reads ~/.claude/jobs/<id>/state.json and feeds the agent-view `state`
back into status resolution so bg sessions show their true state.

Schema captured from a real Claude Code 2.1.183 job state.json:
  {"state":"done","tempo":"idle","detail":"...","template":"exec",
   "intent":"...","sessionId":"<uuid>","resumeSessionId":"<uuid>",
   "daemonShort":"18fccc42","cwd":"...","createdAt":"...","updatedAt":"..."}
state union: working | blocked | idle | done | failed | stopped | queued
"""
import importlib.util
import json as _json
import pathlib
import sys
import tempfile
import unittest

_TP = pathlib.Path(__file__).resolve().parent.parent / "tracker.py"
_spec = importlib.util.spec_from_file_location("tracker_jobs", _TP)
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_jobs"] = tk
_spec.loader.exec_module(tk)

_ISO = "2026-06-20T20:15:06.775Z"


class TestJobStateGlyph(unittest.TestCase):
    def test_active_states_map_to_live_glyphs(self):
        self.assertEqual(tk._JOB_STATE_GLYPH["working"], tk.STATUS_WORKING)
        self.assertEqual(tk._JOB_STATE_GLYPH["blocked"], tk.STATUS_WAITING)
        self.assertEqual(tk._JOB_STATE_GLYPH["idle"], tk.STATUS_IDLE)
        self.assertEqual(tk._JOB_STATE_GLYPH["queued"], tk.STATUS_IDLE)

    def test_finished_states_map_to_ended(self):
        for s in ("done", "failed", "stopped"):
            self.assertEqual(tk._JOB_STATE_GLYPH[s], tk.STATUS_ENDED, s)


class TestScanJobs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = tk.JOBS_DIR
        tk.JOBS_DIR = pathlib.Path(self._tmp.name)

    def tearDown(self):
        tk.JOBS_DIR = self._orig
        self._tmp.cleanup()

    def _mk_job(self, short, **fields):
        d = tk.JOBS_DIR / short
        d.mkdir(parents=True, exist_ok=True)
        (d / "state.json").write_text(_json.dumps(fields), encoding="utf-8")
        return d

    def test_missing_dir_returns_empty(self):
        tk.JOBS_DIR = pathlib.Path(self._tmp.name) / "nope"
        self.assertEqual(tk.scan_jobs(), {})

    def test_reads_state_keyed_by_session_id(self):
        self._mk_job("18fccc42", state="working", tempo="active",
                     detail="building thing", template="claude",
                     sessionId="18fccc42-308d-427f-89db-db34041cc8c0",
                     daemonShort="18fccc42", cwd="/repo", updatedAt=_ISO)
        jobs = tk.scan_jobs()
        rec = jobs["18fccc42-308d-427f-89db-db34041cc8c0"]
        self.assertEqual(rec["state"], "working")
        self.assertEqual(rec["tempo"], "active")
        self.assertEqual(rec["detail"], "building thing")
        self.assertEqual(rec["short"], "18fccc42")
        self.assertEqual(rec["cwd"], "/repo")
        self.assertEqual(rec["template"], "claude")

    def test_falls_back_to_resume_session_id(self):
        self._mk_job("abc", state="idle",
                     resumeSessionId="resume-uuid", daemonShort="abc")
        self.assertIn("resume-uuid", tk.scan_jobs())

    def test_skips_non_dir_entries(self):
        # pins.json and .order live alongside job dirs and are not sessions
        (tk.JOBS_DIR / "pins.json").write_text("[]", encoding="utf-8")
        (tk.JOBS_DIR / ".order").write_text("", encoding="utf-8")
        self.assertEqual(tk.scan_jobs(), {})

    def test_skips_dir_without_state_json(self):
        (tk.JOBS_DIR / "empty").mkdir()
        self.assertEqual(tk.scan_jobs(), {})

    def test_skips_corrupt_state_json(self):
        d = tk.JOBS_DIR / "bad"
        d.mkdir()
        (d / "state.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(tk.scan_jobs(), {})

    def test_skips_state_without_any_session_id(self):
        self._mk_job("noid", state="working", daemonShort="noid")
        self.assertEqual(tk.scan_jobs(), {})


class TestClassifyWithJob(unittest.TestCase):
    def c(self, **kw):
        base = dict(done=False, alive=False, overlay=None, reg=None, job=None)
        base.update(kw)
        return tk.classify_status(**base)

    def test_dead_bg_working_shows_working_not_ended(self):
        self.assertEqual(self.c(job={"state": "working"}), tk.STATUS_WORKING)

    def test_dead_bg_blocked_shows_waiting(self):
        self.assertEqual(self.c(job={"state": "blocked"}), tk.STATUS_WAITING)

    def test_dead_bg_idle_shows_idle(self):
        self.assertEqual(self.c(job={"state": "idle"}), tk.STATUS_IDLE)

    def test_dead_bg_done_shows_ended(self):
        self.assertEqual(self.c(job={"state": "done"}), tk.STATUS_ENDED)

    def test_dead_bg_unknown_state_shows_ended(self):
        self.assertEqual(self.c(job={"state": "weird"}), tk.STATUS_ENDED)

    def test_no_job_still_ended(self):
        self.assertEqual(self.c(), tk.STATUS_ENDED)

    def test_user_done_beats_job(self):
        self.assertEqual(self.c(done=True, job={"state": "working"}),
                         tk.STATUS_DONE)

    def test_live_registry_wins_over_job(self):
        # An attached/running bg session is alive in the pid registry; the
        # fresher registry signal must win over the persisted job state.
        self.assertEqual(
            self.c(alive=True, reg={"status": "busy"}, job={"state": "blocked"}),
            tk.STATUS_WORKING)

    def test_job_param_defaults_none_back_compat(self):
        # Existing callers omit job entirely.
        self.assertEqual(
            tk.classify_status(done=False, alive=False, overlay=None, reg=None),
            tk.STATUS_ENDED)


class TestResolveAndWaitingWithJobs(unittest.TestCase):
    def test_resolve_status_threads_jobs_map(self):
        out = tk.resolve_status("s", set(), set(), {}, {},
                                {"s": {"state": "blocked"}})
        self.assertEqual(out, tk.STATUS_WAITING)

    def test_resolve_status_without_jobs_back_compat(self):
        self.assertEqual(tk.resolve_status("s", set(), set()), tk.STATUS_ENDED)

    def test_waiting_ids_includes_dead_bg_blocked(self):
        S = type("S", (), {})

        def sess(sid):
            o = S()
            o.session_id = sid
            return o

        sessions = [sess("a"), sess("b")]
        jobs = {"a": {"state": "blocked"}, "b": {"state": "working"}}
        got = tk.waiting_ids(sessions, set(), set(), {}, {}, jobs)
        self.assertEqual(got, {"a"})


class TestStatusContextCapturesJobs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_jobs = tk.JOBS_DIR
        self._orig_reg = tk.SESSIONS_REGISTRY_DIR
        self._orig_state = tk.STATE_PATH
        tk.JOBS_DIR = pathlib.Path(self._tmp.name) / "jobs"
        tk.SESSIONS_REGISTRY_DIR = pathlib.Path(self._tmp.name) / "sessions"
        tk.STATE_PATH = pathlib.Path(self._tmp.name) / "state.json"

    def tearDown(self):
        tk.JOBS_DIR = self._orig_jobs
        tk.SESSIONS_REGISTRY_DIR = self._orig_reg
        tk.STATE_PATH = self._orig_state
        self._tmp.cleanup()

    def test_capture_includes_jobs_and_resolve_uses_them(self):
        d = tk.JOBS_DIR / "abc"
        d.mkdir(parents=True)
        (d / "state.json").write_text(
            _json.dumps({"state": "blocked", "sessionId": "sid-1",
                         "daemonShort": "abc"}), encoding="utf-8")
        ctx = tk.StatusContext.capture()
        self.assertIn("sid-1", ctx.jobs)
        self.assertEqual(ctx.resolve("sid-1"), tk.STATUS_WAITING)


if __name__ == "__main__":
    unittest.main()
