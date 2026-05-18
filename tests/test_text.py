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
