import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tracker


class NormalizeTtyTests(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(tracker._normalize_tty("ttys010"), "/dev/ttys010")

    def test_trailing_whitespace(self):
        self.assertEqual(tracker._normalize_tty("ttys010 \n"), "/dev/ttys010")

    def test_already_dev_prefixed(self):
        self.assertEqual(tracker._normalize_tty("/dev/ttys010"), "/dev/ttys010")

    def test_single_question(self):
        self.assertIsNone(tracker._normalize_tty("?"))

    def test_double_question(self):
        self.assertIsNone(tracker._normalize_tty("??"))

    def test_empty(self):
        self.assertIsNone(tracker._normalize_tty(""))


WEZ_SAMPLE = """
[
  {"window_id": 97, "pane_id": 101, "tty_name": "/dev/ttys015"},
  {"window_id": 95, "pane_id": 99,  "tty_name": "/dev/ttys010"}
]
"""


class WeztermFindPaneTests(unittest.TestCase):
    def test_match_returns_pane_id(self):
        self.assertEqual(
            tracker._wezterm_find_pane_id(WEZ_SAMPLE, "/dev/ttys010"), 99)

    def test_no_match_returns_none(self):
        self.assertIsNone(
            tracker._wezterm_find_pane_id(WEZ_SAMPLE, "/dev/ttys004"))

    def test_bad_json_returns_none(self):
        self.assertIsNone(
            tracker._wezterm_find_pane_id("not json at all", "/dev/ttys010"))

    def test_non_list_returns_none(self):
        self.assertIsNone(tracker._wezterm_find_pane_id("{}", "/dev/ttys010"))


if __name__ == "__main__":
    unittest.main()
