"""Surface agent-view pins (read-only).

Format captured from a real agent-view Ctrl+T pin: ~/.claude/jobs/pins.json is
a JSON array of daemonShort strings, e.g. ["cbe8e3bb", "4c51890c"]. Stale shorts
(whose job was removed) persist. cst reads it and marks pinned rows with 📌; it
does NOT write the file (a supervisor-locked file — writing risks corrupting
agent-view's own pin state).
"""
import importlib.util
import json as _json
import pathlib
import sys
import tempfile
import unittest

_TP = pathlib.Path(__file__).resolve().parent.parent / "tracker.py"
_spec = importlib.util.spec_from_file_location("tracker_pins", _TP)
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_pins"] = tk
_spec.loader.exec_module(tk)


class TestReadPins(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = tk.JOBS_DIR
        tk.JOBS_DIR = pathlib.Path(self._tmp.name)

    def tearDown(self):
        tk.JOBS_DIR = self._orig
        self._tmp.cleanup()

    def test_reads_array_of_shorts(self):
        (tk.JOBS_DIR / "pins.json").write_text(
            _json.dumps(["cbe8e3bb", "4c51890c"]))
        self.assertEqual(tk.read_pins(), {"cbe8e3bb", "4c51890c"})

    def test_missing_file_empty(self):
        self.assertEqual(tk.read_pins(), set())

    def test_empty_array(self):
        (tk.JOBS_DIR / "pins.json").write_text("[]")
        self.assertEqual(tk.read_pins(), set())

    def test_corrupt_is_empty(self):
        (tk.JOBS_DIR / "pins.json").write_text("{not json")
        self.assertEqual(tk.read_pins(), set())

    def test_ignores_non_string_elements(self):
        (tk.JOBS_DIR / "pins.json").write_text(_json.dumps(["ok", 5, None]))
        self.assertEqual(tk.read_pins(), {"ok"})


class TestCmdListRendersWithPin(unittest.TestCase):
    """Regression: cmd_list must render rows (and the pin `*`) without crashing.
    A NameError in the row loop printed only the header and swallowed every row.
    """
    def setUp(self):
        import io
        self._io = io
        self._tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self._tmp.name)
        self._orig = (tk.PROJECTS_DIR, tk.CACHE_DIR, tk.CACHE_PATH,
                      tk.STATE_PATH, tk.JOBS_DIR, tk.DAEMON_DIR,
                      tk.SESSIONS_REGISTRY_DIR)
        tk.PROJECTS_DIR = root / "projects"
        tk.CACHE_DIR = root / "cache"
        tk.CACHE_PATH = tk.CACHE_DIR / "index.json"
        tk.STATE_PATH = tk.CACHE_DIR / "state.json"
        tk.JOBS_DIR = root / "jobs"
        tk.DAEMON_DIR = root / "daemon"
        tk.SESSIONS_REGISTRY_DIR = root / "sessions"
        self._sid = "bde75633-911a-4097-b070-efa81b61c689"
        sp = tk.PROJECTS_DIR / "proj" / f"{self._sid}.jsonl"
        sp.parent.mkdir(parents=True)
        sp.write_text(_json.dumps({"type": "user",
            "timestamp": "2026-06-21T00:00:00Z", "cwd": "/repo",
            "message": {"content": "hello"}}) + "\n", encoding="utf-8")
        # job-backed + pinned by its short
        jd = tk.JOBS_DIR / "bde75633"
        jd.mkdir(parents=True)
        (jd / "state.json").write_text(_json.dumps({
            "sessionId": self._sid, "daemonShort": "bde75633",
            "state": "idle", "template": "bg"}))
        (tk.JOBS_DIR / "pins.json").write_text(_json.dumps(["bde75633"]))

    def tearDown(self):
        (tk.PROJECTS_DIR, tk.CACHE_DIR, tk.CACHE_PATH, tk.STATE_PATH,
         tk.JOBS_DIR, tk.DAEMON_DIR, tk.SESSIONS_REGISTRY_DIR) = self._orig
        self._tmp.cleanup()

    def test_renders_row_and_pin_marker(self):
        from contextlib import redirect_stdout
        buf = self._io.StringIO()
        with redirect_stdout(buf):
            rc = tk.cmd_list(tk.argparse.Namespace(
                cwd=None, days=None, status=None, limit=None))
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("bde75633", out)        # the row rendered (not just header)
        self.assertIn(tk.PIN_GLYPH, out)       # pinned marker shown


class TestPinMarker(unittest.TestCase):
    def test_pinned_short(self):
        self.assertEqual(tk.pin_marker("4c51890c", {"4c51890c"}), "*")

    def test_not_pinned(self):
        self.assertEqual(tk.pin_marker("4c51890c", {"other"}), "")

    def test_empty_short(self):
        self.assertEqual(tk.pin_marker("", {"4c51890c"}), "")

    def test_none_short(self):
        self.assertEqual(tk.pin_marker(None, {"x"}), "")


if __name__ == "__main__":
    unittest.main()
