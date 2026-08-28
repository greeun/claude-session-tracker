"""Same sessionId living in more than one project dir must render once.

Renaming/copying a project leaves Claude Code's old
`~/.claude/projects/<encoded-cwd>/<sid>.jsonl` behind, so the very same
session exists as two files. `all_session_files()` returns both, and one
SessionMeta per *file* used to mean two identical rows in `cst list` and
the TUI.
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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
_OTHER = "99999999-8888-7777-6666-555555555555"
_T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _sm(sid, dirname, cwd="", msgs=1, last=_T0):
    return tk.SessionMeta(session_id=sid, path=Path("/p") / dirname / f"{sid}.jsonl",
                          cwd=cwd, msg_count=msgs, last_ts=last)


class TestDedupeSessions(unittest.TestCase):
    def test_distinct_ids_pass_through_in_order(self):
        rows = [_sm(_SID, "a"), _sm(_OTHER, "b")]
        self.assertEqual(tk.dedupe_sessions(rows), rows)

    def test_canonical_dir_wins(self):
        cwd = "/Users/u/proj/tavlet"
        stale = _sm(_SID, "-Users-u-proj-heyhey", cwd=cwd, msgs=99)
        real = _sm(_SID, tk.encode_cwd(cwd), cwd=cwd, msgs=1)
        # Canonical location beats a fatter stale copy.
        self.assertEqual(tk.dedupe_sessions([stale, real]), [real])
        self.assertEqual(tk.dedupe_sessions([real, stale]), [real])

    def test_more_messages_wins_when_neither_is_canonical(self):
        big = _sm(_SID, "x", cwd="/nope", msgs=42)
        small = _sm(_SID, "y", cwd="/nope", msgs=3)
        self.assertEqual(tk.dedupe_sessions([small, big]), [big])

    def test_newer_last_ts_breaks_equal_message_counts(self):
        old = _sm(_SID, "x", cwd="/nope", msgs=5, last=_T0)
        new = _sm(_SID, "y", cwd="/nope", msgs=5, last=_T0 + timedelta(days=1))
        self.assertEqual(tk.dedupe_sessions([old, new]), [new])

    def test_missing_last_ts_never_beats_a_dated_copy(self):
        undated = _sm(_SID, "x", cwd="/nope", msgs=5, last=None)
        dated = _sm(_SID, "y", cwd="/nope", msgs=5, last=_T0)
        self.assertEqual(tk.dedupe_sessions([undated, dated]), [dated])

    def test_full_tie_keeps_the_first_copy(self):
        first = _sm(_SID, "a", cwd="/nope")
        second = _sm(_SID, "b", cwd="/nope")
        # all_session_files() is path-sorted, so "first" is deterministic.
        self.assertEqual(tk.dedupe_sessions([first, second]), [first])

    def test_surviving_row_keeps_input_order(self):
        other = _sm(_OTHER, "a")
        dup_a = _sm(_SID, "a", cwd="/nope", msgs=1)
        dup_b = _sm(_SID, "b", cwd="/nope", msgs=9)
        out = tk.dedupe_sessions([dup_a, other, dup_b])
        self.assertEqual(out, [other, dup_b])


class TestLoadAllSessionsDedupes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._orig = (tk.PROJECTS_DIR, tk.CACHE_DIR, tk.CACHE_PATH, tk.STATE_PATH)
        tk.PROJECTS_DIR = root / "projects"
        tk.PROJECTS_DIR.mkdir(parents=True)
        tk.CACHE_DIR = root / "cache"
        tk.CACHE_PATH = tk.CACHE_DIR / "index.json"
        tk.STATE_PATH = tk.CACHE_DIR / "state.json"

    def tearDown(self):
        tk.PROJECTS_DIR, tk.CACHE_DIR, tk.CACHE_PATH, tk.STATE_PATH = self._orig
        self._tmp.cleanup()

    def _write(self, dirname, sid, cwd):
        p = tk.PROJECTS_DIR / dirname / f"{sid}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"type": "user", "cwd": cwd,
                        "timestamp": "2026-08-01T00:00:00Z",
                        "message": {"content": "hi"}}) + "\n",
            encoding="utf-8")
        return p

    def test_same_session_in_two_project_dirs_yields_one_row(self):
        cwd = "/Users/u/proj/tavlet"
        self._write("-Users-u-proj-heyhey", _SID, cwd)
        keep = self._write(tk.encode_cwd(cwd), _SID, cwd)
        rows = tk.load_all_sessions(fast=True)
        self.assertEqual([m.session_id for m in rows], [_SID])
        self.assertEqual(rows[0].path, keep)


if __name__ == "__main__":
    unittest.main()
