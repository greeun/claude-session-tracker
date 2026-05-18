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
        self.assertTrue(s.endswith(out[1:]))


class TestShortenPath(unittest.TestCase):
    def test_home_prefix_becomes_tilde(self):
        self.assertEqual(tk.shorten_path(tk.HOME + "/proj/x"), "~/proj/x")

    def test_non_home_unchanged(self):
        self.assertEqual(tk.shorten_path("/etc/hosts"), "/etc/hosts")

    def test_empty_returns_question_mark(self):
        self.assertEqual(tk.shorten_path(""), "?")


if __name__ == "__main__":
    unittest.main()
