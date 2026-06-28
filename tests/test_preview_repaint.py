"""Regression: the preview modal must force a full repaint on every session
switch (win.clearok(True)), so terminal multiplexers that only apply ncurses'
cell-diff updates (cmux/Ghostty) don't bleed the previous session's text into
the new one. Driven headlessly via pty.fork (the curses TUI needs a real tty).
"""
import datetime
import importlib.util
import json
import os
import pathlib
import pty
import sys
import tempfile
import unittest

_TP = pathlib.Path(__file__).resolve().parent.parent / "tracker.py"

_OUT = pathlib.Path(tempfile.gettempdir()) / "cst_preview_repaint.json"


def _child():
    import curses

    spec = importlib.util.spec_from_file_location("tracker", _TP)
    tr = importlib.util.module_from_spec(spec)
    sys.modules["tracker"] = tr
    spec.loader.exec_module(tr)
    Path = pathlib.Path

    d = tempfile.mkdtemp()
    pa, pb = Path(d) / "a.jsonl", Path(d) / "b.jsonl"

    def write(path, marker, n, ln):
        with open(path, "w") as f:
            for i in range(n):
                f.write(json.dumps({"type": "user", "timestamp": "2026-06-28T10:00:00.000Z",
                                    "message": {"content": f"{marker} u{i} " + "X" * ln}}) + "\n")
                f.write(json.dumps({"type": "assistant", "timestamp": "2026-06-28T10:00:00.000Z",
                                    "message": {"content": f"{marker} a{i} " + "Y" * ln}}) + "\n")

    write(pa, "AAAA", 40, 80)   # long session
    write(pb, "BBBB", 1, 5)     # short session — must not show AAAA after switch

    ts = datetime.datetime(2026, 6, 28, 10, 0, 0)
    items = [
        tr.SessionMeta(session_id="aaaaaaaa-1111", path=pa, cwd="/a",
                       first_ts=ts, last_ts=ts, msg_count=80, first_user_msg="AAAA"),
        tr.SessionMeta(session_id="bbbbbbbb-2222", path=pb, cwd="/b",
                       first_ts=ts, last_ts=ts, msg_count=2, first_user_msg="BBBB"),
    ]

    class Ctx:
        def resolve(self, sid):
            return "idle"

    clearok_flags = []

    class WinProxy:
        def __init__(self, w):
            self._w = w

        def clearok(self, flag):
            clearok_flags.append(bool(flag))
            return self._w.clearok(flag)

        def __getattr__(self, name):
            return getattr(self._w, name)

    real_centered = tr._centered_win
    tr._centered_win = lambda *a, **k: WinProxy(real_centered(*a, **k))

    captures = []
    keyseq = [(curses.KEY_RIGHT, None), (ord("q"), None)]
    idx = [0]

    def fake_read_key(win):
        snap = []
        maxy, _ = win.getmaxyx()
        for y in range(maxy):
            try:
                snap.append(win.instr(y, 0).decode("utf-8", "replace"))
            except Exception:
                snap.append("")
        captures.append("\n".join(snap))
        k = keyseq[idx[0]] if idx[0] < len(keyseq) else (ord("q"), None)
        idx[0] += 1
        return k

    tr._read_key = fake_read_key

    def run(stdscr):
        try:
            curses.start_color()
        except Exception:
            pass
        tr._preview_modal(stdscr, items, 0, Ctx())

    curses.wrapper(run)
    _OUT.write_text(json.dumps({"clearok": clearok_flags, "captures": captures}))


def _run_headless():
    """Fork a pty, run the modal in the child, return parsed result dict."""
    if _OUT.exists():
        _OUT.unlink()
    pid, fd = pty.fork()
    if pid == 0:
        try:
            _child()
        except BaseException:
            try:
                import traceback
                _OUT.write_text(json.dumps({"error": traceback.format_exc()}))
            except Exception:
                pass
        os._exit(0)
    while True:
        try:
            if not os.read(fd, 4096):
                break
        except OSError:
            break
    os.waitpid(pid, 0)
    return json.loads(_OUT.read_text())


class TestPreviewRepaint(unittest.TestCase):
    def test_full_repaint_on_switch_no_bleed(self):
        res = _run_headless()
        self.assertNotIn("error", res, msg=res.get("error"))
        caps = res["captures"]
        self.assertGreaterEqual(len(caps), 2, "expected at least 2 rendered frames")
        # frame 0 = session A (long), frame 1 = session B after ‹/›→ switch.
        self.assertIn("AAAA", caps[0])
        self.assertIn("BBBB", caps[1])
        # no previous-session bleed in the logical buffer after the switch
        self.assertNotIn("AAAA", caps[1])
        # clearok(True) fired at least once per session entry (initial + switch)
        self.assertGreaterEqual(res["clearok"].count(True), 2)


if __name__ == "__main__":
    unittest.main()
