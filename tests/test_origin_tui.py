"""TUI origin filter (`f` cycles all→user→agent, `F` walks backwards).

Driven headlessly via pty.fork — the curses picker needs a real tty. The child
wraps `stdscr` in a proxy whose `getch()` feeds a scripted key sequence and
snapshots the rendered screen before each key, so the parent can assert on what
the user would actually have seen.
"""
import importlib.util
import json
import os
import pathlib
import pty
import sys
import tempfile
import unittest
from datetime import datetime, timezone

_TP = pathlib.Path(__file__).resolve().parent.parent / "tracker.py"
_OUT = pathlib.Path(tempfile.gettempdir()) / "cst_origin_tui_result.json"


def _load():
    spec = importlib.util.spec_from_file_location("tracker_origin_tui", _TP)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tracker_origin_tui"] = mod
    spec.loader.exec_module(mod)
    return mod


def _child():
    import curses

    tmp = tempfile.mkdtemp()
    os.environ["CST_HOME"] = tmp
    tr = _load()
    tr.CACHE_DIR = pathlib.Path(tmp)
    tr.STATE_PATH = tr.CACHE_DIR / "state.json"

    def _sm(sid, ep, msg):
        return tr.SessionMeta(
            session_id=sid, path=pathlib.Path(f"/x/{sid}.jsonl"), cwd="/w",
            last_ts=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
            msg_count=3, first_user_msg=msg, entrypoint=ep)

    sessions = [_sm("aaaaaaaa", "cli", "UUUONE"),
                _sm("bbbbbbbb", "sdk-py", "AAAONE"),
                _sm("cccccccc", "cli", "UUUTWO")]

    empty_ctx = tr.StatusContext(live=set(), done=set(), registry={},
                                 overlay={}, jobs={}, pins=set())
    tr.StatusContext.capture = classmethod(lambda cls: empty_ctx)

    captures = []
    keyseq = [ord("f"), ord("f"), ord("F"), 27]
    idx = [0]

    class Proxy:
        def __init__(self, w):
            self._w = w

        def getch(self):
            maxy, _ = self._w.getmaxyx()
            snap = []
            for y in range(maxy):
                try:
                    snap.append(self._w.instr(y, 0).decode("utf-8", "replace"))
                except Exception:
                    snap.append("")
            captures.append("\n".join(snap))
            k = keyseq[idx[0]] if idx[0] < len(keyseq) else 27
            idx[0] += 1
            return k

        def __getattr__(self, name):
            return getattr(self._w, name)

    def run(stdscr):
        try:
            curses.start_color()
        except Exception:
            pass
        tr._pick_ui(Proxy(stdscr), sessions, None, None)

    curses.wrapper(run)
    _OUT.write_text(json.dumps({"captures": captures,
                                "saved": tr.load_origin()}))


def _run_headless():
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


class TestOriginTui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = _run_headless()

    def frames(self):
        self.assertNotIn("error", self.res, msg=self.res.get("error"))
        caps = self.res["captures"]
        self.assertGreaterEqual(len(caps), 4, "expected 4 rendered frames")
        return caps

    def test_starts_showing_every_origin(self):
        f0 = self.frames()[0]
        self.assertIn("UUUONE", f0)
        self.assertIn("AAAONE", f0)
        self.assertIn("UUUTWO", f0)

    def test_f_once_hides_agent_sessions(self):
        f1 = self.frames()[1]
        self.assertIn("UUUONE", f1)
        self.assertIn("UUUTWO", f1)
        self.assertNotIn("AAAONE", f1)

    def test_f_twice_shows_only_agent_sessions(self):
        f2 = self.frames()[2]
        self.assertIn("AAAONE", f2)
        self.assertNotIn("UUUONE", f2)
        self.assertNotIn("UUUTWO", f2)

    def test_shift_f_walks_back_to_user(self):
        f3 = self.frames()[3]
        self.assertIn("UUUONE", f3)
        self.assertNotIn("AAAONE", f3)

    def test_header_announces_the_active_filter(self):
        # An invisible filter reads as "these are all my sessions".
        self.assertIn("user", self.frames()[1].splitlines()[0])
        self.assertIn("agent", self.frames()[2].splitlines()[0])

    def test_choice_persists_to_state(self):
        self.assertEqual(self.res["saved"], "user")


if __name__ == "__main__":
    unittest.main()
