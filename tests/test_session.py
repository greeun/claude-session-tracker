import importlib.util
import json
import os
import sys
import tempfile
import time
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


def _user_evt(text="hi", **extra):
    e = {"type": "user", "message": {"content": text},
         "timestamp": "2026-05-18T00:00:00Z"}
    e.update(extra)
    return json.dumps(e)


class TestIterJsonl(unittest.TestCase):
    def test_valid_lines_only(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.jsonl"
            p.write_text('{"a":1}\n\nnotjson\n{"b":2}\n', encoding="utf-8")
            self.assertEqual(list(tk.iter_jsonl(p)), [{"a": 1}, {"b": 2}])

    def test_missing_file_is_empty(self):
        self.assertEqual(list(tk.iter_jsonl(Path("/no/such/x.jsonl"))), [])


class TestEncodeCwd(unittest.TestCase):
    def test_non_alnum_replaced_with_dash(self):
        self.assertEqual(tk.encode_cwd("/a/b c"), "-a-b-c")

    def test_alnum_and_dash_preserved(self):
        self.assertEqual(tk.encode_cwd("Abc-123"), "Abc-123")

    def test_nfc_normalized_before_encode(self):
        import unicodedata
        nfd = unicodedata.normalize("NFD", "가")
        nfc = unicodedata.normalize("NFC", "가")
        self.assertEqual(tk.encode_cwd(nfd), tk.encode_cwd(nfc))


class TestLoadSessionMeta(unittest.TestCase):
    def _write(self, d, lines):
        p = Path(d) / "11111111-2222-3333-4444-555555555555.jsonl"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p

    def test_first_user_msg_extracted(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, [_user_evt("hello there")])
            m = tk.load_session_meta(p)
            self.assertEqual(m.first_user_msg, "hello there")
            self.assertEqual(m.msg_count, 1)

    def test_system_wrapper_skipped_for_first_user_msg(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, [
                _user_evt("<command-name>foo</command-name>"),
                _user_evt("real message"),
            ])
            m = tk.load_session_meta(p)
            self.assertEqual(m.first_user_msg, "real message")
            self.assertEqual(m.msg_count, 2)

    def test_tool_use_only_message_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            tu = json.dumps({"type": "user", "timestamp": "2026-05-18T00:00:00Z",
                              "message": {"content": [
                                  {"type": "tool_use", "name": "Bash"}]}})
            p = self._write(d, [tu, _user_evt("actual ask")])
            m = tk.load_session_meta(p)
            self.assertEqual(m.first_user_msg, "actual ask")

    def test_cwd_and_git_branch_extracted(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, [_user_evt("x", cwd="/work/x", gitBranch="main")])
            m = tk.load_session_meta(p)
            self.assertEqual(m.cwd, "/work/x")
            self.assertEqual(m.git_branch, "main")

    def test_no_user_assistant_events_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            sys_only = json.dumps({"type": "system", "message": {"content": "x"}})
            p = self._write(d, [sys_only])
            self.assertIsNone(tk.load_session_meta(p))

    def test_fast_mode_sets_last_ts_from_mtime(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, [_user_evt("hi")])
            m = tk.load_session_meta(p, fast=True)
            self.assertIsInstance(m.last_ts, datetime)


class _ProjIsolation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._orig_proj = tk.PROJECTS_DIR
        self._orig_cache_path = tk.CACHE_PATH
        self._orig_cache_dir = tk.CACHE_DIR
        tk.PROJECTS_DIR = root / "projects"
        tk.PROJECTS_DIR.mkdir(parents=True)
        tk.CACHE_DIR = root / "cache"
        tk.CACHE_PATH = tk.CACHE_DIR / "index.json"

    def tearDown(self):
        tk.PROJECTS_DIR = self._orig_proj
        tk.CACHE_PATH = self._orig_cache_path
        tk.CACHE_DIR = self._orig_cache_dir
        self._tmp.cleanup()

    def _mk(self, name, **extra):
        p = tk.PROJECTS_DIR / "proj" / f"{name}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_user_evt("hi", **extra) + "\n", encoding="utf-8")
        return p


class TestLoadAllSessions(_ProjIsolation):
    def test_days_filter_excludes_backdated(self):
        old = self._mk("aaaaaaaa-0000-0000-0000-000000000000")
        self._mk("bbbbbbbb-1111-1111-1111-111111111111")
        old_t = time.time() - 10 * 86400
        os.utime(old, (old_t, old_t))
        ids = {m.session_id for m in tk.load_all_sessions(days=1)}
        self.assertIn("bbbbbbbb-1111-1111-1111-111111111111", ids)
        self.assertNotIn("aaaaaaaa-0000-0000-0000-000000000000", ids)

    def test_cwd_filter(self):
        self._mk("cccccccc-0000-0000-0000-000000000000", cwd="/aaa/x")
        self._mk("dddddddd-1111-1111-1111-111111111111", cwd="/bbb/y")
        ids = {m.session_id for m in tk.load_all_sessions(cwd_filter="/aaa")}
        self.assertEqual(ids, {"cccccccc-0000-0000-0000-000000000000"})

    def test_second_call_hits_cache(self):
        self._mk("eeeeeeee-0000-0000-0000-000000000000")
        calls = []
        orig = tk.load_session_meta

        def spy(p, fast=True):
            calls.append(str(p))
            return orig(p, fast=fast)

        tk.load_session_meta = spy
        try:
            tk.load_all_sessions()
            n1 = len(calls)
            tk.load_all_sessions()
            n2 = len(calls)
        finally:
            tk.load_session_meta = orig
        self.assertEqual(n1, 1)
        self.assertEqual(n2, n1)


class TestCache(_ProjIsolation):
    def test_save_load_roundtrip(self):
        tk._save_cache({"entries": {"k": {"x": 1}}})
        d = tk._load_cache()
        self.assertEqual(d["schema"], tk._CACHE_SCHEMA)
        self.assertEqual(d["entries"]["k"], {"x": 1})

    def test_schema_mismatch_returns_empty_entries(self):
        tk.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tk.CACHE_PATH.write_text(
            json.dumps({"schema": 999, "entries": {"k": 1}}), encoding="utf-8")
        self.assertEqual(tk._load_cache(),
                         {"schema": tk._CACHE_SCHEMA, "entries": {}})

    def test_corrupt_cache_falls_back(self):
        tk.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tk.CACHE_PATH.write_text("{bad", encoding="utf-8")
        self.assertEqual(tk._load_cache(),
                         {"schema": tk._CACHE_SCHEMA, "entries": {}})


if __name__ == "__main__":
    unittest.main()
