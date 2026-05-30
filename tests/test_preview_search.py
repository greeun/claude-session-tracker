import importlib.util
import pathlib
import sys
import unittest

_TP = pathlib.Path(__file__).resolve().parent.parent / "tracker.py"
_spec = importlib.util.spec_from_file_location("tracker", _TP)
tracker = importlib.util.module_from_spec(_spec)
sys.modules["tracker"] = tracker
_spec.loader.exec_module(tracker)


def L(*texts):
    """Build a preview `lines` list of (text, attr) tuples from plain strings."""
    return [(t, 0) for t in texts]


class TestPreviewFindMatches(unittest.TestCase):
    def test_empty_and_whitespace_query(self):
        lines = L("hello world", "foo")
        self.assertEqual(tracker._preview_find_matches(lines, ""), [])
        self.assertEqual(tracker._preview_find_matches(lines, "   "), [])

    def test_case_insensitive_ascii(self):
        m = tracker._preview_find_matches(L("Hello HELLO hello"), "hello")
        self.assertEqual(m, [(0, 0, 5), (0, 6, 11), (0, 12, 17)])

    def test_multiple_lines(self):
        m = tracker._preview_find_matches(L("abc", "xabcx", "no"), "abc")
        self.assertEqual(m, [(0, 0, 3), (1, 1, 4)])

    def test_no_match(self):
        self.assertEqual(tracker._preview_find_matches(L("abc"), "zzz"), [])

    def test_literal_not_regex(self):
        # metacharacters match literally, never as regex
        m = tracker._preview_find_matches(L("a.c axc a.c"), "a.c")
        self.assertEqual(m, [(0, 0, 3), (0, 8, 11)])
        self.assertEqual(tracker._preview_find_matches(L("a|b"), "|"), [(0, 1, 2)])

    def test_cjk_offsets_are_char_indices(self):
        # offsets are CHARACTER indices into the line text, not display columns
        m = tracker._preview_find_matches(L("한글 hello 한글"), "한글")
        self.assertEqual(m, [(0, 0, 2), (0, 9, 11)])

    def test_non_overlapping(self):
        self.assertEqual(tracker._preview_find_matches(L("aaaa"), "aa"),
                         [(0, 0, 2), (0, 2, 4)])


class TestMatchStep(unittest.TestCase):
    def test_total_zero(self):
        self.assertEqual(tracker._match_step(-1, 0, True), -1)
        self.assertEqual(tracker._match_step(3, 0, False), -1)

    def test_from_unset(self):
        self.assertEqual(tracker._match_step(-1, 5, True), 0)
        self.assertEqual(tracker._match_step(-1, 5, False), 4)

    def test_forward_wrap(self):
        self.assertEqual(tracker._match_step(0, 3, True), 1)
        self.assertEqual(tracker._match_step(2, 3, True), 0)

    def test_backward_wrap(self):
        self.assertEqual(tracker._match_step(0, 3, False), 2)
        self.assertEqual(tracker._match_step(1, 3, False), 0)

    def test_single(self):
        self.assertEqual(tracker._match_step(0, 1, True), 0)
        self.assertEqual(tracker._match_step(0, 1, False), 0)


class TestScrollMatchIntoView(unittest.TestCase):
    def test_already_visible(self):
        self.assertEqual(tracker._scroll_match_into_view(5, 3, 10, 100), 3)

    def test_above(self):
        self.assertEqual(tracker._scroll_match_into_view(2, 5, 10, 100), 2)

    def test_below(self):
        # line 20, top 0, view 10 -> top = 20 - 10 + 1 = 11
        self.assertEqual(tracker._scroll_match_into_view(20, 0, 10, 100), 11)

    def test_clamp_max_top(self):
        self.assertEqual(tracker._scroll_match_into_view(200, 0, 10, 50), 50)

    def test_clamp_zero(self):
        self.assertEqual(tracker._scroll_match_into_view(-5, 3, 10, 100), 0)

    def test_view_h_one(self):
        self.assertEqual(tracker._scroll_match_into_view(7, 0, 1, 100), 7)


if __name__ == "__main__":
    unittest.main()
