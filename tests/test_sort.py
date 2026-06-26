import argparse
import importlib.util
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def load_tracker():
    spec = importlib.util.spec_from_file_location("tracker_under_test", _REPO / "tracker.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tracker_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


tk = load_tracker()


def _ts(day):
    return datetime(2026, 6, day, 12, 0, tzinfo=timezone.utc)


def _sm(sid, *, last=None, msgs=0, cwd=""):
    return tk.SessionMeta(session_id=sid, path=Path(f"/x/{sid}.jsonl"),
                          cwd=cwd, last_ts=last, msg_count=msgs)


class _FakeCtx:
    """Minimal stand-in: sort_sessions only calls ctx.resolve(sid)."""
    def __init__(self, mapping):
        self._m = mapping

    def resolve(self, sid):
        return self._m.get(sid, tk.STATUS_ENDED)


class TestSortSessions(unittest.TestCase):
    def setUp(self):
        # last_ts: a=day5, b=day3, c=day4 ; msgs: a=10,b=30,c=20
        self.a = _sm("a", last=_ts(5), msgs=10, cwd="~/zeta")
        self.b = _sm("b", last=_ts(3), msgs=30, cwd="~/alpha")
        self.c = _sm("c", last=_ts(4), msgs=20, cwd="~/mid")
        self.sessions = [self.b, self.c, self.a]  # unsorted input
        self.ctx = _FakeCtx({})

    def ids(self, key, reverse=None):
        return [s.session_id for s in tk.sort_sessions(self.sessions, self.ctx, key, reverse)]

    def test_time_default_is_newest_first(self):
        self.assertEqual(self.ids("time"), ["a", "c", "b"])

    def test_time_reverse_is_oldest_first(self):
        self.assertEqual(self.ids("time", reverse=False), ["b", "c", "a"])

    def test_msgs_default_descending(self):
        self.assertEqual(self.ids("msgs"), ["b", "c", "a"])

    def test_msgs_reverse_ascending(self):
        self.assertEqual(self.ids("msgs", reverse=True) and self.ids("msgs", reverse=False),
                         ["a", "c", "b"])

    def test_project_alphabetical(self):
        # shorten_path keeps ~/alpha < ~/mid < ~/zeta
        self.assertEqual(self.ids("project"), ["b", "c", "a"])

    def test_unknown_key_falls_back_to_time(self):
        self.assertEqual(self.ids("bogus"), ["a", "c", "b"])

    def test_returns_new_list_does_not_mutate(self):
        before = list(self.sessions)
        tk.sort_sessions(self.sessions, self.ctx, "msgs")
        self.assertEqual(self.sessions, before)

    def test_tie_break_by_last_ts_descending(self):
        # all msgs equal -> ties broken by recency (newest first)
        x = _sm("x", last=_ts(1), msgs=5)
        y = _sm("y", last=_ts(9), msgs=5)
        z = _sm("z", last=_ts(4), msgs=5)
        out = [s.session_id for s in tk.sort_sessions([x, y, z], self.ctx, "msgs")]
        self.assertEqual(out, ["y", "z", "x"])

    def test_status_rank_order(self):
        ctx = _FakeCtx({"w": tk.STATUS_WORKING, "i": tk.STATUS_IDLE,
                        "d": tk.STATUS_DONE, "e": tk.STATUS_ENDED,
                        "a": tk.STATUS_WAITING})
        rows = [_sm(s) for s in ("d", "e", "i", "a", "w")]
        out = [s.session_id for s in tk.sort_sessions(rows, ctx, "status")]
        # working -> waiting -> idle -> ended -> done
        self.assertEqual(out, ["w", "a", "i", "e", "d"])


class TestStatusSortRank(unittest.TestCase):
    def test_rank_is_status_all_order(self):
        ranks = [tk._status_sort_rank(g) for g in tk.STATUS_ALL]
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(ranks, list(range(len(tk.STATUS_ALL))))

    def test_unknown_glyph_sorts_last(self):
        self.assertGreater(tk._status_sort_rank("?"), tk._status_sort_rank(tk.STATUS_DONE))


class TestSortDefaults(unittest.TestCase):
    def test_keys_and_natural_directions(self):
        self.assertEqual(tk.SORT_KEYS, ("time", "status", "msgs", "project"))
        self.assertEqual(tk._SORT_DEFAULT_DESC,
                         {"time": True, "status": False, "msgs": True, "project": False})


class TestSortPersistence(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_cache_dir = tk.CACHE_DIR
        self._orig_state_path = tk.STATE_PATH
        tk.CACHE_DIR = Path(self._tmp.name) / "cache"
        tk.STATE_PATH = tk.CACHE_DIR / "state.json"

    def tearDown(self):
        tk.CACHE_DIR = self._orig_cache_dir
        tk.STATE_PATH = self._orig_state_path
        self._tmp.cleanup()

    def test_default_is_time_desc(self):
        self.assertEqual(tk.load_sort(), ("time", True))

    def test_roundtrip(self):
        tk.save_sort("msgs", False)
        self.assertEqual(tk.load_sort(), ("msgs", False))
        tk.save_sort("project", True)
        self.assertEqual(tk.load_sort(), ("project", True))

    def test_invalid_key_coerced_to_time_on_save(self):
        tk.save_sort("bogus", True)
        self.assertEqual(tk.load_sort(), ("time", True))

    def test_does_not_clobber_other_state(self):
        tk.save_theme("light")
        tk.save_sort("status", False)
        self.assertEqual(tk.load_sort(), ("status", False))
        self.assertEqual(tk.load_theme(), "light")


class TestCmdListSortArg(unittest.TestCase):
    def test_bare_namespace_without_sort_attr_does_not_crash(self):
        # cmd_list must tolerate a Namespace lacking sort/reverse (getattr guard).
        ns = argparse.Namespace(cwd=None, days=None, status=None, limit=1)
        self.assertNotIn("sort", vars(ns))
        rc = tk.cmd_list(ns)  # exercises the getattr path; returns 0
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
