"""Fast-preview path: total-output cap (--head-chars) + cache-first lookup.

cst.app fetches a preview by shelling out to `cst show` on every click. Two
avoidable costs made that slow on large transcripts: (1) the whole transcript
is printed even though the app keeps only the head, and (2) the pre-print
metadata parse re-reads the whole file. These tests pin the fixes:

- `_print_transcript(head_chars=N)` stops emitting (and stops reading the file)
  once N chars of message text are out.
- `_meta_for_path` serves a fresh index-cache entry without re-parsing.
"""
import importlib.util
import io
import json as _json
import os
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone

_TP = pathlib.Path(__file__).resolve().parent.parent / "tracker.py"
_spec = importlib.util.spec_from_file_location("tracker_perf", _TP)
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_perf"] = tk
_spec.loader.exec_module(tk)

NS = lambda **kw: tk.argparse.Namespace(**kw)


def _quiet(fn, *a, **k):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = fn(*a, **k)
    return rc, out.getvalue() + err.getvalue()


class _Base(unittest.TestCase):
    SID = "aaaaaaaa-1111-2222-3333-444444444444"
    CWD = "/repo/app"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._orig = {k: getattr(tk, k) for k in (
            "PROJECTS_DIR", "CACHE_DIR", "CACHE_PATH", "STATE_PATH",
            "JOBS_DIR", "DAEMON_DIR", "SESSIONS_REGISTRY_DIR")}
        tk.PROJECTS_DIR = self.root / "projects"
        tk.CACHE_DIR = self.root / "cache"
        tk.CACHE_PATH = tk.CACHE_DIR / "index.json"
        tk.STATE_PATH = tk.CACHE_DIR / "state.json"
        tk.JOBS_DIR = self.root / "jobs"
        tk.DAEMON_DIR = self.root / "daemon"
        tk.SESSIONS_REGISTRY_DIR = self.root / "sessions"

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(tk, k, v)
        self._tmp.cleanup()

    def _write_session(self, sid, n_msgs, per_msg_chars, ts="2020-01-01T00:00:00Z"):
        d = tk.PROJECTS_DIR / tk.encode_cwd(self.CWD)
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{sid}.jsonl"
        lines = []
        for i in range(n_msgs):
            body = f"MSG{i:03d}-" + ("x" * per_msg_chars)
            lines.append(_json.dumps({
                "type": "assistant", "timestamp": ts, "cwd": self.CWD,
                "message": {"content": [{"type": "text", "text": body}]}}))
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        epoch = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        os.utime(p, (epoch, epoch))
        return p


class TestHeadCap(_Base):
    def test_print_transcript_head_cap_bounds_output(self):
        p = self._write_session(self.SID, n_msgs=50, per_msg_chars=200)
        _, full = _quiet(tk._print_transcript, p, 10_000)   # no head cap
        _, capped = _quiet(tk._print_transcript, p, 10_000, head_chars=500)
        # capped output is far smaller and omits later messages
        self.assertLess(len(capped), len(full))
        self.assertIn("MSG000", capped)
        self.assertNotIn("MSG049", capped)

    def test_head_cap_zero_is_unlimited(self):
        p = self._write_session(self.SID, n_msgs=10, per_msg_chars=100)
        _, a = _quiet(tk._print_transcript, p, 10_000, head_chars=0)
        _, b = _quiet(tk._print_transcript, p, 10_000)
        self.assertEqual(a, b)
        self.assertIn("MSG009", a)

    def test_cmd_show_accepts_head_chars(self):
        self._write_session(self.SID, n_msgs=40, per_msg_chars=300)
        rc, out = _quiet(tk.cmd_show, NS(
            session_id=self.SID[:8], max_chars=4000,
            with_subagents=False, head_chars=400))
        self.assertEqual(rc, 0)
        self.assertIn("MSG000", out)
        self.assertNotIn("MSG039", out)


class TestCacheFirstLookup(_Base):
    def test_meta_for_path_serves_fresh_cache_without_reparse(self):
        p = self._write_session(self.SID, n_msgs=3, per_msg_chars=50)
        entries = {}
        meta1, fresh1 = tk._meta_for_path(p, entries)
        self.assertTrue(fresh1)                    # cold: parsed
        self.assertIn(str(p), entries)
        # Poison the cache entry's derived field but keep mtime/size; a
        # cache-first read must return the poisoned value, proving it did NOT
        # re-parse the file.
        entries[str(p)]["cwd"] = "/SENTINEL"
        meta2, fresh2 = tk._meta_for_path(p, entries)
        self.assertFalse(fresh2)                   # warm: served from cache
        self.assertEqual(meta2.cwd, "/SENTINEL")

    def test_meta_for_path_reparses_on_size_change(self):
        p = self._write_session(self.SID, n_msgs=3, per_msg_chars=50)
        entries = {}
        tk._meta_for_path(p, entries)
        entries[str(p)]["cwd"] = "/SENTINEL"
        # Grow the file (size + mtime change) -> stale entry, must re-parse.
        with p.open("a", encoding="utf-8") as f:
            f.write(_json.dumps({"type": "user", "timestamp": "2020-01-02T00:00:00Z",
                                 "cwd": self.CWD, "message": {"content": "more"}}) + "\n")
        meta, fresh = tk._meta_for_path(p, entries)
        self.assertTrue(fresh)
        self.assertEqual(meta.cwd, self.CWD)

    def test_find_session_matches_direct_parse(self):
        p = self._write_session(self.SID, n_msgs=4, per_msg_chars=40)
        direct = tk.load_session_meta(p)
        found = tk.find_session(self.SID[:8])
        self.assertIsNotNone(found)
        self.assertEqual(found.session_id, direct.session_id)
        self.assertEqual(found.msg_count, direct.msg_count)
        self.assertEqual(found.cwd, direct.cwd)


class TestLastMessageTs(_Base):
    """`cst show` must report the precise last-message timestamp, not file
    mtime — cheaply, by reading the transcript tail (so huge sessions stay
    fast). mtime is set far from the real message times to prove the source."""

    def _write_msgs(self, sid, stamps, mtime_iso, per_msg_chars=20):
        d = tk.PROJECTS_DIR / tk.encode_cwd(self.CWD)
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{sid}.jsonl"
        lines = []
        for i, (etype, ts) in enumerate(stamps):
            body = f"M{i}-" + ("y" * per_msg_chars)
            if etype == "assistant":
                msg = {"content": [{"type": "text", "text": body}]}
            else:
                msg = {"content": body}
            lines.append(_json.dumps({"type": etype, "timestamp": ts,
                                      "cwd": self.CWD, "message": msg}))
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        epoch = datetime.fromisoformat(mtime_iso.replace("Z", "+00:00")).timestamp()
        os.utime(p, (epoch, epoch))
        return p

    def test_last_message_ts_ignores_mtime(self):
        p = self._write_msgs(self.SID, [
            ("user", "2020-01-01T09:00:00Z"),
            ("assistant", "2020-06-02T10:30:00Z")],   # true last message
            mtime_iso="2020-12-31T00:00:00Z")         # mtime way later
        got = tk.last_message_ts(p)
        self.assertEqual(got, tk.parse_ts("2020-06-02T10:30:00Z"))

    def test_last_message_ts_skips_trailing_non_message(self):
        # A trailing tool/system event (not user/assistant) must not count.
        d = tk.PROJECTS_DIR / tk.encode_cwd(self.CWD)
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{self.SID}.jsonl"
        p.write_text(
            _json.dumps({"type": "assistant", "timestamp": "2020-03-03T08:00:00Z",
                         "cwd": self.CWD,
                         "message": {"content": [{"type": "text", "text": "hi"}]}}) + "\n" +
            _json.dumps({"type": "system", "timestamp": "2020-09-09T08:00:00Z",
                         "cwd": self.CWD, "message": {"content": "noise"}}) + "\n",
            encoding="utf-8")
        got = tk.last_message_ts(p)
        self.assertEqual(got, tk.parse_ts("2020-03-03T08:00:00Z"))

    def test_last_message_ts_large_last_message(self):
        # Last message bigger than the initial tail window must still be found.
        big = 200_000
        p = self._write_msgs(self.SID, [
            ("user", "2020-01-01T00:00:00Z"),
            ("assistant", "2021-05-05T05:05:00Z")],
            mtime_iso="2019-01-01T00:00:00Z", per_msg_chars=big)
        got = tk.last_message_ts(p)
        self.assertEqual(got, tk.parse_ts("2021-05-05T05:05:00Z"))

    def test_last_message_ts_empty_file(self):
        d = tk.PROJECTS_DIR / tk.encode_cwd(self.CWD)
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{self.SID}.jsonl"
        p.write_text("", encoding="utf-8")
        self.assertIsNone(tk.last_message_ts(p))

    def test_cmd_show_prints_precise_last(self):
        last = "2020-06-02T10:30:00Z"
        self._write_msgs(self.SID, [
            ("user", "2020-01-01T09:00:00Z"),
            ("assistant", last)],
            mtime_iso="2020-12-31T00:00:00Z")
        rc, out = _quiet(tk.cmd_show, NS(
            session_id=self.SID[:8], max_chars=4000,
            with_subagents=False, head_chars=0))
        self.assertEqual(rc, 0)
        want = tk.fmt_ts(tk.parse_ts(last))
        mtime_str = tk.fmt_ts(tk.parse_ts("2020-12-31T00:00:00Z"))
        self.assertIn(f"Last:     {want}", out)
        self.assertNotIn(f"Last:     {mtime_str}", out)


class TestSubagentHeadCapAndCache(_Base):
    def _write_subagent(self, parent_path, subid, n_msgs, per_msg_chars):
        d = tk.subagents_dir(parent_path)
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{subid}.jsonl"
        lines = []
        for i in range(n_msgs):
            body = f"SUBM{i:03d}-" + ("z" * per_msg_chars)
            lines.append(_json.dumps({
                "type": "assistant", "timestamp": "2020-02-02T00:00:00Z",
                "cwd": self.CWD, "message": {"content": [{"type": "text", "text": body}]}}))
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        (d / f"{subid}.meta.json").write_text(
            _json.dumps({"agentType": "explore", "description": "d"}), encoding="utf-8")
        return p

    def test_head_chars_caps_subagent_transcripts(self):
        # #2: --head-chars must bound each subagent dump too, not just the main.
        parent = self._write_session(self.SID, n_msgs=50, per_msg_chars=200)
        self._write_subagent(parent, "bbbbbbbb-1111", n_msgs=50, per_msg_chars=200)
        rc, out = _quiet(tk.cmd_show, NS(
            session_id=self.SID[:8], max_chars=4000,
            with_subagents=True, head_chars=300))
        self.assertEqual(rc, 0)
        # cap note once for the main + once for the subagent
        self.assertGreaterEqual(out.count("미리보기 상한"), 2)
        self.assertNotIn("SUBM049", out)   # late subagent message omitted

    def test_find_session_does_not_pollute_cache_with_subagent(self):
        # #3: a subagent lookup must not write into the persistent index cache
        # (load_all_sessions would only prune it → churn), yet must resolve.
        subid = "cccccccc-2222-3333-4444-555555555555"
        parent = self._write_session(self.SID, n_msgs=2, per_msg_chars=20)
        sub_path = self._write_subagent(parent, subid, n_msgs=2, per_msg_chars=20)
        found = tk.find_session(subid[:8])
        self.assertIsNotNone(found)
        self.assertEqual(found.session_id, subid)
        entries = tk._load_cache().get("entries", {})
        self.assertNotIn(str(sub_path), entries)


if __name__ == "__main__":
    unittest.main()
