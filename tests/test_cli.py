import argparse
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def load_tracker():
    spec = importlib.util.spec_from_file_location("tracker_under_test", _REPO / "tracker.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tracker_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


tk = load_tracker()

_SID = "11111111-2222-3333-4444-555555555555"


class _CliIsolation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._orig_proj = tk.PROJECTS_DIR
        self._orig_cache_dir = tk.CACHE_DIR
        self._orig_state = tk.STATE_PATH
        tk.PROJECTS_DIR = root / "projects"
        tk.PROJECTS_DIR.mkdir(parents=True)
        tk.CACHE_DIR = root / "cache"
        tk.STATE_PATH = tk.CACHE_DIR / "state.json"

    def tearDown(self):
        tk.PROJECTS_DIR = self._orig_proj
        tk.CACHE_DIR = self._orig_cache_dir
        tk.STATE_PATH = self._orig_state
        self._tmp.cleanup()

    def _mk_session(self, sid=_SID):
        p = tk.PROJECTS_DIR / "proj" / f"{sid}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"type": "user", "timestamp": "2026-05-18T00:00:00Z",
                        "message": {"content": "hi"}}) + "\n",
            encoding="utf-8")
        return p


class TestCmdDone(_CliIsolation):
    def test_existing_session_marked_done(self):
        self._mk_session()
        rc = tk.cmd_done(argparse.Namespace(session_id=_SID))
        self.assertEqual(rc, 0)
        self.assertIn(_SID, tk.done_ids())

    def test_missing_session_returns_1(self):
        rc = tk.cmd_done(argparse.Namespace(
            session_id="ffffffff-0000-0000-0000-000000000000"))
        self.assertEqual(rc, 1)


class TestCmdUndone(_CliIsolation):
    def test_clears_done_flag(self):
        self._mk_session()
        tk.set_done(_SID, True)
        rc = tk.cmd_undone(argparse.Namespace(session_id=_SID))
        self.assertEqual(rc, 0)
        self.assertNotIn(_SID, tk.done_ids())

    def test_not_done_session_is_noop_success(self):
        self._mk_session()
        rc = tk.cmd_undone(argparse.Namespace(session_id=_SID))
        self.assertEqual(rc, 0)
        self.assertNotIn(_SID, tk.done_ids())


if __name__ == "__main__":
    unittest.main()
