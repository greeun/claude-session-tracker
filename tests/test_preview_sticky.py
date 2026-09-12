"""Pinned metadata header in the preview modal (`v`), and the `d` toggle
keeping the scroll position.

Two layers:
  * `_preview_sticky_head` — pure geometry: how many header rows stay pinned
    for a given viewport height.
  * headless curses (pty.fork) — the rendered window must still show the
    Session/Cwd rows after the body is scrolled down, and pressing `d` must
    not send the body back to the top.
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
_spec = importlib.util.spec_from_file_location("tracker", _TP)
tracker = importlib.util.module_from_spec(_spec)
sys.modules["tracker"] = tracker
_spec.loader.exec_module(tracker)

_OUT = pathlib.Path(tempfile.gettempdir()) / "cst_preview_sticky.json"
_SID = "aaaaaaaa-1111-2222-3333-444444444444"
_CWD = "/tmp/sticky-probe-cwd"


class TestPreviewStickyHead(unittest.TestCase):
    def test_full_header_fits(self):
        # 6 header rows in a 30-row viewport: all of them pin
        self.assertEqual(tracker._preview_sticky_head(6, 30), 6)

    def test_capped_by_min_body_rows(self):
        # the transcript keeps _PREVIEW_MIN_BODY_ROWS rows no matter what
        self.assertEqual(tracker._preview_sticky_head(6, 8),
                         8 - tracker._PREVIEW_MIN_BODY_ROWS)

    def test_no_room_pins_nothing(self):
        for view_h in (0, 1, 2, 3):
            self.assertEqual(tracker._preview_sticky_head(6, view_h), 0,
                             msg=f"view_h={view_h}")

    def test_degenerate_inputs(self):
        self.assertEqual(tracker._preview_sticky_head(0, 30), 0)
        self.assertEqual(tracker._preview_sticky_head(-2, 30), 0)
        self.assertEqual(tracker._preview_sticky_head(6, -5), 0)

    def test_never_exceeds_header(self):
        # a tall window does not pin more rows than the header actually has
        self.assertEqual(tracker._preview_sticky_head(5, 100), 5)


def _child():
    import curses

    spec = importlib.util.spec_from_file_location("tracker_under_test", _TP)
    tr = importlib.util.module_from_spec(spec)
    sys.modules["tracker_under_test"] = tr
    spec.loader.exec_module(tr)
    Path = pathlib.Path

    # state writes land in a tempdir, never in the user's ~/.cst
    cdir = Path(tempfile.mkdtemp()) / "cache"
    tr.CACHE_DIR = cdir
    tr.STATE_PATH = cdir / "state.json"

    d = tempfile.mkdtemp()
    pa = Path(d) / "a.jsonl"
    with open(pa, "w") as f:
        for i in range(60):
            f.write(json.dumps({"type": "user",
                                "timestamp": "2026-06-28T10:00:00.000Z",
                                "message": {"content": f"MSGLINE{i:03d}"}}) + "\n")

    ts = datetime.datetime(2026, 6, 28, 10, 0, 0)
    items = [tr.SessionMeta(session_id=_SID, path=pa, cwd=_CWD,
                            first_ts=ts, last_ts=ts, msg_count=60,
                            first_user_msg="MSGLINE000")]

    class Ctx:
        """Minimal StatusContext stand-in that honours the done overlay, so the
        pinned Status row really changes when `d` marks the session."""

        def __init__(self):
            self.done = set()

        def resolve(self, sid):
            return tr.STATUS_DONE if sid in self.done else tr.STATUS_IDLE

    # scroll well past the header, snapshot, press d, snapshot again
    keyseq = [(curses.KEY_NPAGE, None), (curses.KEY_NPAGE, None),
              (ord("d"), None), (ord("q"), None)]
    idx = [0]
    captures = []

    def fake_read_key(win):
        snap = []
        maxy, _ = win.getmaxyx()
        for y in range(maxy):
            try:
                snap.append(win.instr(y, 0).decode("utf-8", "replace"))
            except Exception:
                snap.append("")
        captures.append(snap)
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
    _OUT.write_text(json.dumps({"captures": captures}))


def _run_headless(rows=40, cols=120):
    if _OUT.exists():
        _OUT.unlink()
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.environ["LINES"] = str(rows)
            os.environ["COLUMNS"] = str(cols)
            os.environ["TERM"] = "xterm"
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


def _body_rows(frame):
    """Transcript lines visible in a frame (the MSGLINE probes)."""
    return [ln.strip() for ln in frame if "MSGLINE" in ln]


class TestPreviewStickyRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = _run_headless()

    def test_no_error(self):
        self.assertNotIn("error", self.res, msg=self.res.get("error"))

    def test_header_survives_scrolling(self):
        caps = self.res["captures"]
        self.assertGreaterEqual(len(caps), 3)
        top_frame, scrolled = caps[0], caps[2]
        # the body actually moved — otherwise the pin proves nothing
        self.assertNotEqual(_body_rows(top_frame), _body_rows(scrolled))
        for row_text in ("Session", _SID[:8], "Cwd", "Status"):
            self.assertTrue(any(row_text in ln for ln in scrolled),
                            msg=f"{row_text!r} scrolled out of the pinned header")

    def test_header_rows_stay_at_the_same_screen_rows(self):
        caps = self.res["captures"]
        def head(frame):
            return [ln for ln in frame[:8] if "Session  " in ln or "Cwd " in ln]
        self.assertEqual(head(caps[0]), head(caps[2]))

    def test_done_toggle_keeps_scroll_position(self):
        caps = self.res["captures"]
        before, after = caps[2], caps[3]   # frame before `d`, frame after `d`
        self.assertEqual(_body_rows(before), _body_rows(after),
                         msg="`d` reset the preview scroll position")

    def test_done_toggle_updates_pinned_status_row(self):
        caps = self.res["captures"]
        before, after = caps[2], caps[3]
        def status(frame):
            return next((ln.strip() for ln in frame if "Status " in ln), "")
        self.assertNotEqual(status(before), status(after))
        self.assertIn("✓", status(after))


if __name__ == "__main__":
    unittest.main()
