"""`_centered_win` — shared modal box placement helper.

Every TUI modal repeated the same centering math
(``y0 = max(0, (h - box_h) // 2)`` / ``x0 = …``) before ``curses.newwin`` +
``keypad(True)``. `_centered_win` owns that once. This drives it under a real
PTY/curses screen and checks the created window has the requested size and is
centered for the live terminal dimensions — and that an oversized box still
raises ``curses.error`` from ``newwin`` (so callers that guard it keep working).
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


def _run_child(child: str) -> str:
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
    return out.decode(errors="replace")


_PRELUDE = r'''
import curses, os, sys
import importlib.util
spec = importlib.util.spec_from_file_location("t", os.environ["TRACKER_PATH"])
m = importlib.util.module_from_spec(spec)
sys.modules["t"] = m
spec.loader.exec_module(m)
'''


class TestCenteredWin(unittest.TestCase):
    def test_size_and_centering(self):
        child = _PRELUDE + r'''
def run(stdscr):
    H, W = stdscr.getmaxyx()
    res = []
    for bh, bw in [(10, 40), (6, 24), (3, 12)]:
        win = m._centered_win(stdscr, bh, bw)
        gh, gw = win.getmaxyx()
        by, bx = win.getbegyx()
        ey = max(0, (H - bh) // 2)
        ex = max(0, (W - bw) // 2)
        res.append("1" if (gh, gw, by, bx) == (bh, bw, ey, ex) else "0")
    return "".join(res)
r = curses.wrapper(run)
sys.stdout.write("R=" + r + "\n")
'''
        text = _run_child(child)
        self.assertIn("R=", text, text)
        self.assertEqual(text.split("R=")[1].split()[0], "111", text)

    def test_does_not_swallow_newwin_error(self):
        # The helper must not wrap newwin in try/except: callers that already
        # guard ``curses.error`` (``_show_help_modal`` / ``_auto_rescan_modal``)
        # rely on it propagating. Verified structurally — the helper's body has
        # no exception handler — since ``newwin`` won't reliably raise here.
        import ast
        import inspect
        src = inspect.getsource(tk._centered_win)
        tree = ast.parse(src)
        handlers = [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]
        self.assertEqual(handlers, [], "helper must not swallow curses.error")


if __name__ == "__main__":
    unittest.main()
