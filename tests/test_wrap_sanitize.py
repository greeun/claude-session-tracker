"""TAB / control-char sanitization in the preview wrap.

Regression: a transcript line containing a literal TAB (e.g. ``"1\\t- ..."``)
was wrapped to chunks whose `display_width` looked <= inner_w, but curses
`addnstr` expands the TAB to the next tab stop, so the rendered line ran past
the box border and curses wrapped it to column 0 — overwriting the left
border. `_sanitize_cells` expands tabs and neutralizes control chars so
measured width == rendered width.
"""
import importlib.util
import os
import pty
import sys
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


class TestSanitizeCells(unittest.TestCase):
    def test_tab_expanded_to_spaces(self):
        out = tk._sanitize_cells("1\t- x")
        self.assertNotIn("\t", out)
        self.assertTrue(out.startswith("1       "))  # expandtabs(8): col1 -> col8

    def test_c0_controls_become_space(self):
        self.assertEqual(tk._sanitize_cells("a\x07b\x00c"), "a b c")

    def test_esc_neutralized(self):
        # ESC must not survive — it would move the curses cursor
        self.assertNotIn("\x1b", tk._sanitize_cells("a\x1b[31mb"))

    def test_c1_controls_become_space(self):
        self.assertEqual(tk._sanitize_cells("a\x85b\x9fc"), "a b c")

    def test_plain_text_unchanged(self):
        s = "강점 기록함. (AI Slop/표현)"
        self.assertEqual(tk._sanitize_cells(s), s)


class TestWrapWithinWidth(unittest.TestCase):
    def test_tab_line_chunks_fit(self):
        # The exact shape of the line that broke the border in the bug report.
        raw = "1\t- **[강력 전역 이력서 룰](feedback_strong_global_resume_rules.md) " \
              "- 이력서/자소서/포트폴리오/base 작업 시 사용자 명시 모든 지침(AI Slop/표현/구조 등)은 " \
              "강력 전역 룰. 한 번 명시된 룰은 향후 모든 기업 지원에 자동 적용. 위반 즉시 FAIL.**"
        inner_w = 116
        for chunk in tk._wrap_display(raw, inner_w):
            self.assertLessEqual(tk.display_width(chunk), inner_w)
            self.assertNotIn("\t", chunk)
            for ch in chunk:
                o = ord(ch)
                self.assertFalse(o < 0x20 or 0x7f <= o <= 0x9f, repr(ch))


class TestBoxBorderIntact(unittest.TestCase):
    """Render the wrapped chunks into a real curses box under a PTY and confirm
    the left/right border cells survive — i.e. nothing wrapped to column 0."""

    def test_border_not_overwritten(self):
        child = r'''
import curses, os, sys
import importlib.util
spec = importlib.util.spec_from_file_location("t", os.environ["TRACKER_PATH"])
m = importlib.util.module_from_spec(spec)
sys.modules["t"] = m
spec.loader.exec_module(m)
raw = "1\t- " + "가" * 200  # tab + long CJK run, guaranteed to wrap
def run(stdscr):
    box_w, box_h = 60, 20
    inner_w = box_w - 4
    win = curses.newwin(box_h, box_w, 0, 0)
    win.box()
    chunks = m._wrap_display(raw, inner_w)
    for i, text in enumerate(chunks[:box_h - 2]):
        try:
            win.addnstr(1 + i, 2, text, box_w - 4)
        except curses.error:
            pass
    bad = 0
    vline = curses.ACS_VLINE & 0xFF
    for row in range(1, min(len(chunks) + 1, box_h - 1)):
        for col in (0, box_w - 1):
            cell = win.inch(row, col) & 0xFF
            if cell != vline:
                bad += 1
    return bad
bad = curses.wrapper(run)
sys.stdout.write("BAD=" + str(bad) + "\n")
'''
        pid, fd = pty.fork()
        if pid == 0:
            os.environ["TERM"] = "xterm"
            os.environ["TRACKER_PATH"] = str(_REPO / "tracker.py")
            os.execv(sys.executable, [sys.executable, "-c", child])
        out = b""
        try:
            while True:
                try:
                    chunk = os.read(fd, 1024)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
        finally:
            os.waitpid(pid, 0)
        text = out.decode(errors="replace")
        self.assertIn("BAD=", text, text)
        bad = int(text.split("BAD=")[1].split()[0])
        self.assertEqual(bad, 0, "border cells overwritten: %s" % text)


if __name__ == "__main__":
    unittest.main()
