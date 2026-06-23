"""done(✓) must refuse an actively-working (●) session.

Marking a working session done is contradictory (the task is still running)
and worse, the done glyph wins over every state (done > working), so it would
*mask* a live, quota-burning session. Guard the four done=True entry points
(cmd_done, the `done!` prompt-hook, TUI D / Ctrl-D). Unmarking (undone) and
done on waiting/idle/ended stay allowed; --force overrides on the CLI.
"""
import importlib.util
import io
import json as _json
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

_TP = pathlib.Path(__file__).resolve().parent.parent / "tracker.py"
_spec = importlib.util.spec_from_file_location("tracker_dg", _TP)
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_dg"] = tk
_spec.loader.exec_module(tk)


class _FakeCtx:
    def __init__(self, status):
        self._s = status

    def resolve(self, _sid):
        return self._s


class TestDoneGuardPure(unittest.TestCase):
    def test_working_blocks(self):
        self.assertTrue(tk.done_guard_blocks(tk.STATUS_WORKING))

    def test_working_force_allows(self):
        self.assertFalse(tk.done_guard_blocks(tk.STATUS_WORKING, force=True))

    def test_other_states_never_block(self):
        for s in (tk.STATUS_WAITING, tk.STATUS_IDLE,
                  tk.STATUS_ENDED, tk.STATUS_DONE):
            self.assertFalse(tk.done_guard_blocks(s), s)


class _CmdBase(unittest.TestCase):
    """Shared temp state + monkeypatch scaffolding for command-level tests."""
    _SID = "abcdef12-3333-4444-5555-666666666666"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self._tmp.name)
        self._orig = (tk.STATE_PATH, tk.CACHE_DIR,
                      tk.require_session, tk.find_session,
                      tk.StatusContext.capture, sys.stdin)
        tk.CACHE_DIR = root
        tk.STATE_PATH = root / "state.json"
        self._target = tk.SessionMeta(session_id=self._SID,
                                      path=pathlib.Path("/x.jsonl"), cwd="/repo")
        tk.require_session = lambda _p: self._target
        tk.find_session = lambda _p: self._target

    def tearDown(self):
        (tk.STATE_PATH, tk.CACHE_DIR, tk.require_session,
         tk.find_session, tk.StatusContext.capture, sys.stdin) = self._orig
        self._tmp.cleanup()

    def _set_status(self, status):
        tk.StatusContext.capture = lambda: _FakeCtx(status)


class TestCmdDoneGuard(_CmdBase):
    def _run(self, status, force=False):
        self._set_status(status)
        ns = tk.argparse.Namespace(session_id=self._SID, force=force)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = tk.cmd_done(ns)
        return rc, out.getvalue() + err.getvalue()

    def test_working_refused_and_not_marked(self):
        rc, out = self._run(tk.STATUS_WORKING)
        self.assertEqual(rc, 1)
        self.assertIn("working", out.lower())
        self.assertNotIn(self._SID, tk.done_ids())

    def test_working_force_marks(self):
        rc, out = self._run(tk.STATUS_WORKING, force=True)
        self.assertEqual(rc, 0)
        self.assertIn(self._SID, tk.done_ids())

    def test_idle_marks(self):
        rc, out = self._run(tk.STATUS_IDLE)
        self.assertEqual(rc, 0)
        self.assertIn(self._SID, tk.done_ids())


class TestPromptHookGuard(_CmdBase):
    def _run(self, prompt, status):
        self._set_status(status)
        sys.stdin = io.StringIO(_json.dumps(
            {"prompt": prompt, "session_id": self._SID}))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = tk.cmd_prompt_hook(tk.argparse.Namespace())
        return rc, buf.getvalue()

    def test_done_bang_self_on_working_marks(self):
        # self `done!` (no explicit target) is the user declaring "this very
        # session is finished". The session is *necessarily* ● working at that
        # instant — it is processing this very prompt — so the working-guard
        # would block self-done 100% of the time. The guard is meant for *other*
        # live sessions, not the one you are sitting in; self is exempt.
        rc, out = self._run("done!", tk.STATUS_WORKING)
        self.assertEqual(_json.loads(out)["decision"], "block")  # hook always blocks the prompt
        self.assertIn(self._SID, tk.done_ids())

    def test_done_bang_explicit_target_on_working_blocks(self):
        # An explicit target (`done! <id>`) is a *different* live session — the
        # guard still refuses, so a quota-burning session is not masked by ✓.
        rc, out = self._run(f"done! {self._SID}", tk.STATUS_WORKING)
        body = _json.loads(out)
        self.assertEqual(body["decision"], "block")
        self.assertIn("working", body["reason"].lower())
        self.assertNotIn(self._SID, tk.done_ids())

    def test_done_bang_on_idle_marks(self):
        rc, out = self._run("done!", tk.STATUS_IDLE)
        self.assertEqual(_json.loads(out)["decision"], "block")  # hook always blocks the prompt
        self.assertIn(self._SID, tk.done_ids())

    def test_undone_bang_on_working_still_clears(self):
        tk.set_done(self._SID, True)
        rc, out = self._run("undone!", tk.STATUS_WORKING)
        self.assertEqual(_json.loads(out)["decision"], "block")
        self.assertNotIn(self._SID, tk.done_ids())


class TestDoneForceFlag(unittest.TestCase):
    def test_done_parser_has_force_default_false(self):
        parser = tk._build_parser()
        ns = parser.parse_args(["done", "abc"])
        self.assertFalse(ns.force)
        ns2 = parser.parse_args(["done", "abc", "--force"])
        self.assertTrue(ns2.force)


if __name__ == "__main__":
    unittest.main()
