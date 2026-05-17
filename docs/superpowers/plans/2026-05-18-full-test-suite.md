# Full Test Suite Implementation Plan — claude-session-tracker

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add unittest coverage for every non-TUI logical unit of `tracker.py` (display, state, session loading, text, export, CLI), split into focused per-area test files.

**Architecture:** These are characterization tests against *already-working* code — the spec was verified line-by-line against `tracker.py`, so each test is expected to PASS on first run. Each file imports `tracker.py` by path via the existing `load_tracker()` pattern (no pytest, no conftest). Tests that touch the filesystem reassign `tracker.py` module globals to a `tempfile.TemporaryDirectory` in `setUp` and restore them in `tearDown`.

**Tech Stack:** Python 3.10+ stdlib only (`unittest`, `tempfile`, `importlib.util`, `pathlib`, `unicodedata`, `argparse`, `os`, `json`, `re`).

**Spec:** `docs/superpowers/specs/2026-05-18-full-test-suite-design.md`

---

## File Structure

```
tests/
├── test_orphan_relocate.py    # EXISTING — untouched
├── test_display.py            # NEW — display_width, pad_display, truncate_display(_tail), shorten_path
├── test_text.py               # NEW — extract_text, parse_ts, fmt_ts, _is_system_wrapper_msg, truncate
├── test_state.py              # NEW — resolve_status, load/save_state, done_ids, mark_done, set_done
├── test_session.py            # NEW — iter_jsonl, encode_cwd, load_session_meta, load_all_sessions, _load/_save_cache
├── test_export.py             # NEW — _build_export_text, _build_export_md, export_session
└── test_cli.py                # NEW — cmd_done, cmd_undone
```

Each NEW file is independent. `test_display.py` and `test_text.py` test pure functions (no global isolation needed). `test_state.py`, `test_session.py`, `test_export.py`, `test_cli.py` reassign module globals and need `setUp`/`tearDown`.

**Run the whole suite:**
```bash
cd /Users/uni4love/project/workspace/211-withwiz/claude-utils/claude-skills/claude-session-tracker
python3 -m unittest discover -s tests -p "test_*.py" -v
```

---

## Shared import preamble

Every NEW test file starts with this exact preamble (copied from `test_orphan_relocate.py`; repeated per-file deliberately — no conftest, files may be read out of order):

```python
import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def load_tracker():
    """Import tracker.py by path. sys.modules registration BEFORE exec_module
    is required or @dataclass raises AttributeError (cls.__module__ is None)."""
    spec = importlib.util.spec_from_file_location("tracker_under_test", _REPO / "tracker.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tracker_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


tk = load_tracker()
```

---

### Task 1: test_display.py — display utilities

**Files:**
- Create: `tests/test_display.py`

- [ ] **Step 1: Write the test file**

```python
import importlib.util
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def load_tracker():
    """Import tracker.py by path. sys.modules registration BEFORE exec_module
    is required or @dataclass raises AttributeError (cls.__module__ is None)."""
    spec = importlib.util.spec_from_file_location("tracker_under_test", _REPO / "tracker.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tracker_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


tk = load_tracker()


class TestDisplayWidth(unittest.TestCase):
    def test_ascii_is_one_each(self):
        self.assertEqual(tk.display_width("a"), 1)
        self.assertEqual(tk.display_width("abc"), 3)

    def test_cjk_is_two_each(self):
        self.assertEqual(tk.display_width("가"), 2)
        self.assertEqual(tk.display_width("한글"), 4)

    def test_mixed_string(self):
        self.assertEqual(tk.display_width("a가"), 3)

    def test_empty_string(self):
        self.assertEqual(tk.display_width(""), 0)


class TestPadDisplay(unittest.TestCase):
    def test_left_align_pads_right(self):
        out = tk.pad_display("ab", 5, "left")
        self.assertEqual(out, "ab   ")
        self.assertEqual(tk.display_width(out), 5)

    def test_right_align_pads_left(self):
        out = tk.pad_display("ab", 5, "right")
        self.assertEqual(out, "   ab")
        self.assertEqual(tk.display_width(out), 5)

    def test_already_at_or_over_width_unchanged(self):
        self.assertEqual(tk.pad_display("abcde", 3), "abcde")
        self.assertEqual(tk.pad_display("abc", 3), "abc")


class TestTruncateDisplay(unittest.TestCase):
    def test_no_cut_when_fits(self):
        self.assertEqual(tk.truncate_display("abc", 10), "abc")

    def test_ascii_cut_appends_ellipsis(self):
        out = tk.truncate_display("abcdef", 4)
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(tk.display_width(out), 4)

    def test_cjk_boundary_safe(self):
        out = tk.truncate_display("가나다라", 5)
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(tk.display_width(out), 5)
        # no half-character: every kept char is a full CJK glyph
        self.assertTrue(all(ord(c) for c in out))

    def test_empty_string(self):
        self.assertEqual(tk.truncate_display("", 5), "")


class TestTruncateDisplayTail(unittest.TestCase):
    def test_no_cut_when_fits(self):
        self.assertEqual(tk.truncate_display_tail("proj", 10), "proj")

    def test_keeps_tail_prepends_ellipsis(self):
        s = "/very/long/path/myproj"
        out = tk.truncate_display_tail(s, 8)
        self.assertTrue(out.startswith("…"))
        self.assertLessEqual(tk.display_width(out), 8)
        self.assertTrue(s.endswith(out[1:]))  # tail preserved verbatim


class TestShortenPath(unittest.TestCase):
    def test_home_prefix_becomes_tilde(self):
        self.assertEqual(tk.shorten_path(tk.HOME + "/proj/x"), "~/proj/x")

    def test_non_home_unchanged(self):
        self.assertEqual(tk.shorten_path("/etc/hosts"), "/etc/hosts")

    def test_empty_returns_question_mark(self):
        self.assertEqual(tk.shorten_path(""), "?")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the file, expect PASS**

Run:
```bash
cd /Users/uni4love/project/workspace/211-withwiz/claude-utils/claude-skills/claude-session-tracker
python3 -m unittest tests.test_display -v
```
Expected: `OK` — all tests pass (code already implements this behavior; spec was verified against `tracker.py:367-426`).

If any test FAILs: the test encodes a wrong expectation, not a code bug. Re-read the relevant function in `tracker.py` and correct the assertion. Do not modify `tracker.py`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_display.py
git commit -m "test: add display utility tests (display_width, pad/truncate, shorten_path)"
```

---

### Task 2: test_text.py — text processing utilities

**Files:**
- Create: `tests/test_text.py`

- [ ] **Step 1: Write the test file**

```python
import importlib.util
import re
import sys
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


class TestExtractText(unittest.TestCase):
    def test_none_returns_empty(self):
        self.assertEqual(tk.extract_text(None), "")

    def test_str_returned_verbatim(self):
        self.assertEqual(tk.extract_text("hello"), "hello")

    def test_text_blocks_joined_with_newline(self):
        content = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
        self.assertEqual(tk.extract_text(content), "a\nb")

    def test_tool_use_block_is_labeled_not_ignored(self):
        content = [{"type": "tool_use", "name": "Read"}]
        self.assertEqual(tk.extract_text(content), "[tool_use:Read]")

    def test_tool_result_string_content(self):
        content = [{"type": "tool_result", "content": "result text"}]
        self.assertEqual(tk.extract_text(content), "result text")

    def test_tool_result_list_text_subblocks(self):
        content = [{"type": "tool_result",
                    "content": [{"type": "text", "text": "sub"}]}]
        self.assertEqual(tk.extract_text(content), "sub")

    def test_empty_list_returns_empty(self):
        self.assertEqual(tk.extract_text([]), "")


class TestParseTs(unittest.TestCase):
    def test_iso_with_z_suffix(self):
        dt = tk.parse_ts("2026-05-18T01:02:03Z")
        self.assertIsInstance(dt, datetime)
        self.assertIsNotNone(dt.tzinfo)

    def test_none_returns_none(self):
        self.assertIsNone(tk.parse_ts(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(tk.parse_ts(""))

    def test_bad_format_returns_none(self):
        self.assertIsNone(tk.parse_ts("not-a-timestamp"))


class TestFmtTs(unittest.TestCase):
    def test_none_returns_question_mark(self):
        self.assertEqual(tk.fmt_ts(None), "?")

    def test_datetime_formats_to_minute(self):
        out = tk.fmt_ts(datetime(2026, 5, 18, 1, 2, 3, tzinfo=timezone.utc))
        # local-tz dependent value; assert shape, never a hardcoded string
        self.assertRegex(out, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")


class TestIsSystemWrapper(unittest.TestCase):
    def test_empty_is_wrapper(self):
        self.assertTrue(tk._is_system_wrapper_msg(""))

    def test_known_prefix_is_wrapper(self):
        self.assertTrue(tk._is_system_wrapper_msg("<command-name>foo</command-name>"))

    def test_leading_whitespace_then_prefix(self):
        self.assertTrue(tk._is_system_wrapper_msg("   <command-name>foo"))

    def test_plain_message_is_not_wrapper(self):
        self.assertFalse(tk._is_system_wrapper_msg("please fix the bug"))


class TestTruncate(unittest.TestCase):
    def test_over_length_cut_with_ellipsis(self):
        self.assertEqual(tk.truncate("aaaaaa", 3), "aa…")

    def test_under_length_unchanged(self):
        self.assertEqual(tk.truncate("a b", 10), "a b")

    def test_whitespace_collapsed(self):
        self.assertEqual(tk.truncate("a   b   c", 20), "a b c")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the file, expect PASS**

Run:
```bash
cd /Users/uni4love/project/workspace/211-withwiz/claude-utils/claude-skills/claude-session-tracker
python3 -m unittest tests.test_text -v
```
Expected: `OK`. Behavior verified against `tracker.py:429-474` and `:643-659`.

If FAIL: fix the test assertion (re-read the function), not `tracker.py`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_text.py
git commit -m "test: add text utility tests (extract_text, parse_ts, fmt_ts, wrapper, truncate)"
```

---

### Task 3: test_state.py — status & done-flag overlay

**Files:**
- Create: `tests/test_state.py`

**Isolation note:** `save_state` calls `CACHE_DIR.mkdir(...)` and writes `STATE_PATH.with_suffix(".tmp")`, then `tmp.replace(STATE_PATH)`. `setUp` must point BOTH `tk.CACHE_DIR` and `tk.STATE_PATH` into a tempdir (keeping `STATE_PATH = CACHE_DIR / "state.json"`), and `tearDown` must restore the originals.

- [ ] **Step 1: Write the test file**

```python
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


class TestResolveStatus(unittest.TestCase):
    def test_done_wins(self):
        self.assertEqual(tk.resolve_status("s", set(), {"s"}), tk.STATUS_DONE)

    def test_active_when_live_only(self):
        self.assertEqual(tk.resolve_status("s", {"s"}, set()), tk.STATUS_ACTIVE)

    def test_ended_when_neither(self):
        self.assertEqual(tk.resolve_status("s", set(), set()), tk.STATUS_ENDED)

    def test_done_beats_live(self):
        self.assertEqual(tk.resolve_status("s", {"s"}, {"s"}), tk.STATUS_DONE)


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
        self.assertTrue(tk.mark_done("sid-y"))      # now done
        self.assertIn("sid-y", tk.done_ids())
        self.assertFalse(tk.mark_done("sid-y"))     # toggled off
        self.assertNotIn("sid-y", tk.done_ids())

    def test_unset_unknown_session_is_noop(self):
        tk.set_done("never-seen", False)            # must not raise
        self.assertNotIn("never-seen", tk.done_ids())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the file, expect PASS**

Run:
```bash
cd /Users/uni4love/project/workspace/211-withwiz/claude-utils/claude-skills/claude-session-tracker
python3 -m unittest tests.test_state -v
```
Expected: `OK`. Behavior verified against `tracker.py:535-587`.

If FAIL: fix the test (re-read the function), not `tracker.py`. Confirm `setUp` correctly reassigns both globals and that no real `~/.cache` file was touched.

- [ ] **Step 3: Commit**

```bash
git add tests/test_state.py
git commit -m "test: add status/done-flag tests (resolve_status, state IO, done_ids)"
```

---

### Task 4: test_session.py — session loading & cache

**Files:**
- Create: `tests/test_session.py`

**Isolation note:** Override `tk.PROJECTS_DIR`, `tk.CACHE_PATH`, `tk.CACHE_DIR` in `setUp`; restore in `tearDown`. `all_session_files()` requires `PROJECTS_DIR.exists()`.

- [ ] **Step 1: Write the test file**

```python
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
            tk.load_all_sessions()          # pass 1: indexes (1 load)
            n1 = len(calls)
            tk.load_all_sessions()          # pass 2: mtime+size match -> cached
            n2 = len(calls)
        finally:
            tk.load_session_meta = orig
        self.assertEqual(n1, 1)
        self.assertEqual(n2, n1)            # no extra load on cached pass


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
```

- [ ] **Step 2: Run the file, expect PASS**

Run:
```bash
cd /Users/uni4love/project/workspace/211-withwiz/claude-utils/claude-skills/claude-session-tracker
python3 -m unittest tests.test_session -v
```
Expected: `OK`. Behavior verified against `tracker.py:662-836` and `:2618-2623`.

If FAIL: fix the test (re-read the function), not `tracker.py`. Common pitfalls: `_mk` must write at least one user/assistant event or `load_session_meta` returns `None`; the cache-hit spy must restore `tk.load_session_meta` in `finally`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_session.py
git commit -m "test: add session-loading & cache tests (iter_jsonl, encode_cwd, load_all_sessions)"
```

---

### Task 5: test_export.py — transcript export

**Files:**
- Create: `tests/test_export.py`

**Isolation note:** `export_session` calls `scan_live_sessions()` (reads `SESSIONS_REGISTRY_DIR` — returns empty if not a dir) and `done_ids()` (reads `STATE_PATH`). Point `SESSIONS_REGISTRY_DIR` at a non-existent tempdir path and isolate `CACHE_DIR`/`STATE_PATH`. `_build_export_*` re-reads `target.path` via `iter_jsonl`, so the transcript must be a real file.

- [ ] **Step 1: Write the test file**

```python
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


def _meta(path, cwd="/work/proj"):
    return tk.SessionMeta(
        session_id=_SID, path=path, cwd=cwd,
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


class TestBuildExportMd(unittest.TestCase):
    def test_markdown_header_and_shortened_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            t = _meta(_transcript(d), cwd=tk.HOME + "/proj")
            out = tk._build_export_md(t, tk.STATUS_DONE)
            self.assertTrue(out.startswith("# Session: "))
            self.assertIn("\n---\n", out)
            self.assertIn("~/proj", out)        # shorten_path applied in md


class _ExportIsolation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._orig_reg = tk.SESSIONS_REGISTRY_DIR
        self._orig_cache_dir = tk.CACHE_DIR
        self._orig_state = tk.STATE_PATH
        tk.SESSIONS_REGISTRY_DIR = root / "noreg"   # absent -> scan returns empty
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
```

- [ ] **Step 2: Run the file, expect PASS**

Run:
```bash
cd /Users/uni4love/project/workspace/211-withwiz/claude-utils/claude-skills/claude-session-tracker
python3 -m unittest tests.test_export -v
```
Expected: `OK`. Behavior verified against `tracker.py:1036-1107`.

If FAIL: fix the test (re-read the function), not `tracker.py`. Note `_build_export_text` uses `"Cwd:      "` (raw cwd, 6 spaces of padding) while `_build_export_md` uses `shorten_path`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_export.py
git commit -m "test: add export tests (_build_export_text/md, export_session)"
```

---

### Task 6: test_cli.py — cmd_done / cmd_undone

**Files:**
- Create: `tests/test_cli.py`

**Isolation note:** `cmd_done`/`cmd_undone` → `find_session()` → `all_session_files()` (reads `PROJECTS_DIR`) + `load_session_meta()` (no cache, `fast=False`, so the fake `.jsonl` MUST contain ≥1 user/assistant event or `find_session` returns `None`). Then `set_done()` → `load_state`/`save_state` (`STATE_PATH`, `CACHE_DIR`). Override `PROJECTS_DIR`, `CACHE_DIR`, `STATE_PATH`. (`CACHE_PATH` is *not* used by these commands — they never call `load_all_sessions`.)

- [ ] **Step 1: Write the test file**

```python
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
```

- [ ] **Step 2: Run the file, expect PASS**

Run:
```bash
cd /Users/uni4love/project/workspace/211-withwiz/claude-utils/claude-skills/claude-session-tracker
python3 -m unittest tests.test_cli -v
```
Expected: `OK`. Behavior verified against `tracker.py:1186-1203` and `:3392-3408`.

If FAIL: fix the test (re-read the function), not `tracker.py`. If `cmd_done` unexpectedly returns 1 on the existing-session case, confirm the fake `.jsonl` has a `type:"user"` event (otherwise `load_session_meta` → `None` → `find_session` → `None`).

- [ ] **Step 3: Commit**

```bash
git add tests/test_cli.py
git commit -m "test: add CLI tests (cmd_done, cmd_undone)"
```

---

### Task 7: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the entire suite including the pre-existing file**

Run:
```bash
cd /Users/uni4love/project/workspace/211-withwiz/claude-utils/claude-skills/claude-session-tracker
python3 -m unittest discover -s tests -p "test_*.py" -v
```
Expected: `OK` with the combined count from all 7 files (existing `test_orphan_relocate` + 6 new). Zero failures, zero errors.

- [ ] **Step 2: Confirm no real state files were mutated**

Run:
```bash
git status --porcelain ~/.cache/claude-session-tracker 2>/dev/null; echo "exit:$?"
ls -la ~/.cache/claude-session-tracker/ 2>/dev/null
```
Expected: the suite must not have created/modified `~/.cache/claude-session-tracker/state.json` or `index.json` timestamps as a side effect (all isolated to tempdirs). If they were touched, a `setUp` override is missing — fix the offending file and re-run.

- [ ] **Step 3: Final commit (only if any plan-doc tweaks were made)**

```bash
git add docs/superpowers/plans/2026-05-18-full-test-suite.md
git commit -m "docs: finalize full test suite plan" --allow-empty
```

---

## Self-Review

**Spec coverage:** Every spec table maps to a task — test_display.py→T1, test_text.py→T2, test_state.py→T3, test_session.py→T4 (incl. `_load_cache`/`_save_cache` TestCache), test_export.py→T5, test_cli.py→T6. Constraints (TUI/live-process/external-program exclusion, global isolation) are honored: no curses, no `scan_live_sessions`/`_pid_alive` assertions on real processes, no `mdfind`/`fd` calls; every FS-touching test isolates globals in `setUp`/`tearDown`.

**Placeholder scan:** No TBD/TODO; every code step contains the full, runnable test file; every run step has the exact command and expected `OK`.

**Type consistency:** `tk.STATUS_DONE/ACTIVE/ENDED`, `tk.SessionMeta(session_id=, path=, cwd=, first_ts=, last_ts=, msg_count=)`, `tk.HOME`, `tk._CACHE_SCHEMA`, `argparse.Namespace(session_id=...)`, and global names (`PROJECTS_DIR`, `SESSIONS_REGISTRY_DIR`, `CACHE_DIR`, `CACHE_PATH`, `STATE_PATH`) match `tracker.py` exactly as read during spec verification.
