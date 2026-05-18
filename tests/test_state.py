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


class _StateIsolation(unittest.TestCase):
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


class TestStateIO(_StateIsolation):
    def test_save_then_load_roundtrip(self):
        tk.save_state({"k": 1})
        self.assertEqual(tk.load_state(), {"k": 1})

    def test_corrupt_json_falls_back_to_empty(self):
        tk.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tk.STATE_PATH.write_text("{ not json", encoding="utf-8")
        self.assertEqual(tk.load_state(), {})

    def test_cache_dir_autocreated_on_save(self):
        self.assertFalse(tk.CACHE_DIR.exists())
        tk.save_state({"a": 1})
        self.assertTrue(tk.STATE_PATH.exists())
        self.assertEqual(tk.load_state(), {"a": 1})

    def test_missing_state_file_loads_empty(self):
        self.assertFalse(tk.STATE_PATH.exists())
        self.assertEqual(tk.load_state(), {})


class TestDoneFlag(_StateIsolation):
    def test_set_done_true_then_in_done_ids(self):
        tk.set_done("sid-x", True)
        self.assertIn("sid-x", tk.done_ids())

    def test_set_done_false_removes(self):
        tk.set_done("sid-x", True)
        tk.set_done("sid-x", False)
        self.assertNotIn("sid-x", tk.done_ids())

    def test_mark_done_toggles(self):
        self.assertTrue(tk.mark_done("sid-y"))
        self.assertIn("sid-y", tk.done_ids())
        self.assertFalse(tk.mark_done("sid-y"))
        self.assertNotIn("sid-y", tk.done_ids())

    def test_unset_unknown_session_is_noop(self):
        tk.set_done("never-seen", False)
        self.assertNotIn("never-seen", tk.done_ids())


class TestStatusOverlay(_StateIsolation):
    def test_set_status_then_overlay(self):
        tk.set_status("s", "waiting", "Notification")
        ov = tk.status_overlay()
        self.assertEqual(ov["s"]["state"], "waiting")
        self.assertEqual(ov["s"]["event"], "Notification")
        self.assertIn("ts", ov["s"])

    def test_set_status_none_clears(self):
        tk.set_status("s", "working", "UserPromptSubmit")
        tk.set_status("s", None, "Stop")
        self.assertNotIn("s", tk.status_overlay())

    def test_no_status_bucket_is_empty(self):
        self.assertEqual(tk.status_overlay(), {})

    def test_done_and_status_buckets_independent(self):
        tk.set_done("s", True)
        tk.set_status("s", "waiting", "Notification")
        self.assertIn("s", tk.done_ids())
        self.assertIn("s", tk.status_overlay())


class _RegIsolation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_reg = tk.SESSIONS_REGISTRY_DIR
        tk.SESSIONS_REGISTRY_DIR = Path(self._tmp.name) / "sessions"

    def tearDown(self):
        tk.SESSIONS_REGISTRY_DIR = self._orig_reg
        self._tmp.cleanup()

    def _reg(self, name, payload):
        tk.SESSIONS_REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
        (tk.SESSIONS_REGISTRY_DIR / name).write_text(
            json.dumps(payload), encoding="utf-8")


class TestScanRegistryStatus(_RegIsolation):
    def test_normal_record_mapped(self):
        self._reg("a.json", {"sessionId": "s1", "status": "idle",
                              "updatedAt": 1700000000000})
        out = tk.scan_registry_status()
        self.assertEqual(out["s1"],
                         {"status": "idle", "updatedAt": 1700000000000})

    def test_non_int_updatedat_and_non_str_status_normalized(self):
        self._reg("b.json", {"sessionId": "s2", "status": 5,
                             "updatedAt": "nope"})
        out = tk.scan_registry_status()
        self.assertEqual(out["s2"], {"status": None, "updatedAt": None})

    def test_corrupt_and_missing_sessionid_skipped(self):
        tk.SESSIONS_REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
        (tk.SESSIONS_REGISTRY_DIR / "bad.json").write_text(
            "{not json", encoding="utf-8")
        self._reg("nosid.json", {"status": "idle"})
        self.assertEqual(tk.scan_registry_status(), {})

    def test_missing_registry_dir_returns_empty(self):
        self.assertEqual(tk.scan_registry_status(), {})


if __name__ == "__main__":
    unittest.main()
