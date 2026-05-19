import importlib.util
import json
import sys
import tempfile
import types
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


class TestLoadSaveAutoRescan(unittest.TestCase):
    def _tmp_state(self):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        f.close()
        self.addCleanup(Path(f.name).unlink, missing_ok=True)
        tk.STATE_PATH = Path(f.name)
        return Path(f.name)

    def test_default_when_absent(self):
        p = self._tmp_state()
        p.write_text("{}")
        self.assertEqual(tk.load_auto_rescan(), (True, 10))

    def test_default_when_empty_file(self):
        self._tmp_state()  # empty content -> JSONDecodeError -> load_state returns {}
        self.assertEqual(tk.load_auto_rescan(), (True, 10))

    def test_corrupt_or_out_of_range_falls_back(self):
        p = self._tmp_state()
        p.write_text(json.dumps({"auto_rescan": {"enabled": "yes", "interval": 7}}))
        self.assertEqual(tk.load_auto_rescan(), (True, 10))
        p.write_text(json.dumps({"auto_rescan": "garbage"}))
        self.assertEqual(tk.load_auto_rescan(), (True, 10))
        p.write_text(json.dumps({"auto_rescan": {"enabled": True, "interval": True}}))
        self.assertEqual(tk.load_auto_rescan(), (True, 10))

    def test_valid_round_trip(self):
        self._tmp_state()
        tk.save_auto_rescan(False, 30)
        self.assertEqual(tk.load_auto_rescan(), (False, 30))
        tk.save_auto_rescan(True, 60)
        self.assertEqual(tk.load_auto_rescan(), (True, 60))

    def test_save_preserves_other_state_keys(self):
        p = self._tmp_state()
        p.write_text(json.dumps({"done": {"abc": "2026-01-01"}}))
        tk.save_auto_rescan(True, 5)
        data = json.loads(p.read_text())
        self.assertEqual(data["done"], {"abc": "2026-01-01"})
        self.assertEqual(data["auto_rescan"], {"enabled": True, "interval": 5})


class TestNewlyWaiting(unittest.TestCase):
    def test_new_entrants_only(self):
        self.assertEqual(tk.newly_waiting({"a"}, {"a", "b"}), {"b"})

    def test_unchanged_is_empty(self):
        self.assertEqual(tk.newly_waiting({"a", "b"}, {"a", "b"}), set())

    def test_left_is_empty(self):
        self.assertEqual(tk.newly_waiting({"a", "b"}, {"a"}), set())

    def test_left_then_rejoined_is_new_again(self):
        prev = set()                       # after it left, baseline no longer has it
        self.assertEqual(tk.newly_waiting(prev, {"a"}), {"a"})

    def test_empty_both(self):
        self.assertEqual(tk.newly_waiting(set(), set()), set())


class TestWaitingIds(unittest.TestCase):
    def _s(self, sid):
        return types.SimpleNamespace(session_id=sid)

    def test_collects_only_waiting(self):
        sessions = [self._s("s1"), self._s("s2"), self._s("s3")]
        live = {"s1", "s2", "s3"}
        done = set()
        registry = {"s1": {"status": "waiting"}, "s2": {"status": "idle"},
                    "s3": {"status": "busy"}}
        overlay = {}
        self.assertEqual(
            tk.waiting_ids(sessions, live, done, registry, overlay), {"s1"})

    def test_done_and_dead_excluded(self):
        sessions = [self._s("s1"), self._s("s2")]
        live = {"s1"}                       # s2 not alive
        done = {"s1"}                       # s1 manually done -> not waiting
        registry = {"s1": {"status": "waiting"}, "s2": {"status": "waiting"}}
        self.assertEqual(
            tk.waiting_ids(sessions, live, done, registry, {}), set())


if __name__ == "__main__":
    unittest.main()
