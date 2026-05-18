import importlib.util
import json
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

_SID = "abcdef12-2222-3333-4444-555555555555"


def _transcript(d):
    p = Path(d) / f"{_SID}.jsonl"
    p.write_text(
        json.dumps({"type": "user", "timestamp": "2026-05-18T01:02:03Z",
                    "message": {"content": "hello world"}}) + "\n" +
        json.dumps({"type": "assistant", "timestamp": "2026-05-18T01:02:05Z",
                    "message": {"content": [{"type": "text",
                                             "text": "hi there"}]}}) + "\n" +
        json.dumps({"type": "system",
                    "message": {"content": "should be skipped"}}) + "\n",
        encoding="utf-8")
    return p


def _meta(path, cwd="/work/proj", branch=""):
    return tk.SessionMeta(
        session_id=_SID, path=path, cwd=cwd, git_branch=branch,
        first_ts=datetime(2026, 5, 18, 1, 2, 3, tzinfo=timezone.utc),
        last_ts=datetime(2026, 5, 18, 1, 2, 5, tzinfo=timezone.utc),
        msg_count=2)


class TestBuildExportText(unittest.TestCase):
    def test_header_and_body(self):
        with tempfile.TemporaryDirectory() as d:
            t = _meta(_transcript(d))
            out = tk._build_export_text(t, tk.STATUS_ENDED)
            self.assertIn(f"Session:  {_SID}", out)
            self.assertIn("Cwd:      /work/proj", out)
            self.assertIn("hello world", out)
            self.assertIn("hi there", out)
            self.assertNotIn("should be skipped", out)
            self.assertIn("🧑", out)
            self.assertIn("🤖", out)

    def test_branch_line_present_when_set(self):
        with tempfile.TemporaryDirectory() as d:
            t = _meta(_transcript(d), branch="feat/x")
            out = tk._build_export_text(t, tk.STATUS_ENDED)
            self.assertIn("Branch:   feat/x", out)

    def test_branch_line_absent_when_unset(self):
        with tempfile.TemporaryDirectory() as d:
            t = _meta(_transcript(d))
            out = tk._build_export_text(t, tk.STATUS_ENDED)
            self.assertNotIn("Branch:", out)


class TestBuildExportMd(unittest.TestCase):
    def test_markdown_header_and_shortened_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            t = _meta(_transcript(d), cwd=tk.HOME + "/proj")
            out = tk._build_export_md(t, tk.STATUS_DONE)
            self.assertTrue(out.startswith("# Session: "))
            self.assertIn("\n---\n", out)
            self.assertIn("~/proj", out)

    def test_branch_line_present_when_set(self):
        with tempfile.TemporaryDirectory() as d:
            t = _meta(_transcript(d), branch="feat/x")
            out = tk._build_export_md(t, tk.STATUS_DONE)
            self.assertIn("**Branch:** feat/x", out)


class _ExportIsolation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._orig_reg = tk.SESSIONS_REGISTRY_DIR
        self._orig_cache_dir = tk.CACHE_DIR
        self._orig_state = tk.STATE_PATH
        tk.SESSIONS_REGISTRY_DIR = root / "noreg"
        tk.CACHE_DIR = root / "cache"
        tk.STATE_PATH = tk.CACHE_DIR / "state.json"
        self.root = root

    def tearDown(self):
        tk.SESSIONS_REGISTRY_DIR = self._orig_reg
        tk.CACHE_DIR = self._orig_cache_dir
        tk.STATE_PATH = self._orig_state
        self._tmp.cleanup()


class TestExportSession(_ExportIsolation):
    def test_out_dir_autonames_file(self):
        t = _meta(_transcript(self.root))
        outdir = self.root / "out"
        outdir.mkdir()
        dest = tk.export_session(t, "txt", str(outdir))
        self.assertEqual(dest, outdir / f"{_SID[:8]}-2026-05-18.txt")
        self.assertTrue(dest.exists())

    def test_out_explicit_file_path(self):
        t = _meta(_transcript(self.root))
        target = self.root / "explicit.txt"
        dest = tk.export_session(t, "txt", str(target))
        self.assertEqual(dest, target)
        self.assertTrue(dest.exists())
        self.assertIn("Session:", dest.read_text(encoding="utf-8"))

    def test_md_format_extension(self):
        t = _meta(_transcript(self.root))
        outdir = self.root / "outmd"
        outdir.mkdir()
        dest = tk.export_session(t, "md", str(outdir))
        self.assertTrue(dest.name.endswith(".md"))
        self.assertTrue(dest.exists())


if __name__ == "__main__":
    unittest.main()
