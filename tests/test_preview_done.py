"""Done-toggle from the preview modal (`d` / `Ctrl-D`).

Driven headlessly via pty.fork — the curses modal needs a real tty. Three modes:
  * "done"     — `d` on an idle session marks it done; modal stays open (q
                 closes) and returns None (no delete request bubbles up).
  * "guard"    — `d` on a ● working session is refused by done_guard_blocks;
                 the done flag is NOT written.
  * "toggle"   — `d` twice clears the flag again (toggle semantics).
The substantive effect is the persisted done flag, asserted via done_ids().
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


def load_tracker():
    spec = importlib.util.spec_from_file_location("tracker_under_test", _TP)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tracker_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


_OUT = pathlib.Path(tempfile.gettempdir()) / "cst_preview_done.json"

_SID_A = "aaaaaaaa-1111"


def _child():
    import curses

    mode = os.environ.get("CST_TEST_MODE", "done")
    tr = load_tracker()
    Path = pathlib.Path

    # Stub state/cache into a fresh tempdir so mark_done writes here, not ~/.
    cdir = Path(tempfile.mkdtemp()) / "cache"
    tr.CACHE_DIR = cdir
    tr.STATE_PATH = cdir / "state.json"

    d = tempfile.mkdtemp()
    pa = Path(d) / "a.jsonl"
    with open(pa, "w") as f:
        f.write(json.dumps({"type": "user",
                            "timestamp": "2026-06-28T10:00:00.000Z",
                            "message": {"content": "AAAA"}}) + "\n")

    ts = datetime.datetime(2026, 6, 28, 10, 0, 0)
    items = [tr.SessionMeta(session_id=_SID_A, path=pa, cwd="/a",
                            first_ts=ts, last_ts=ts, msg_count=1,
                            first_user_msg="AAAA")]

    status = tr.STATUS_WORKING if mode == "guard" else "idle"

    class Ctx:
        def __init__(self):
            self.done = set()

        def resolve(self, sid):
            return status

    if mode == "toggle":
        keyseq = [(ord("d"), None), (ord("d"), None), (ord("q"), None)]
    else:
        keyseq = [(ord("d"), None), (ord("q"), None)]
    idx = [0]

    def fake_read_key(win):
        k = keyseq[idx[0]] if idx[0] < len(keyseq) else (ord("q"), None)
        idx[0] += 1
        return k

    tr._read_key = fake_read_key

    result = {}

    def run(stdscr):
        try:
            curses.start_color()
        except Exception:
            pass
        ret = tr._preview_modal(stdscr, items, 0, Ctx())
        result["returned"] = None if ret is None else ret.session_id
        result["done_a"] = _SID_A in tr.done_ids()

    curses.wrapper(run)
    _OUT.write_text(json.dumps(result))


def _run_headless(mode):
    if _OUT.exists():
        _OUT.unlink()
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.environ["CST_TEST_MODE"] = mode
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


class TestPreviewDone(unittest.TestCase):
    def test_d_marks_idle_session_done(self):
        res = _run_headless("done")
        self.assertNotIn("error", res, msg=res.get("error"))
        self.assertTrue(res["done_a"])          # flag persisted
        self.assertIsNone(res["returned"])      # no delete request

    def test_d_guarded_on_working_session(self):
        res = _run_headless("guard")
        self.assertNotIn("error", res, msg=res.get("error"))
        self.assertFalse(res["done_a"])         # ● working — refused
        self.assertIsNone(res["returned"])

    def test_d_twice_toggles_off(self):
        res = _run_headless("toggle")
        self.assertNotIn("error", res, msg=res.get("error"))
        self.assertFalse(res["done_a"])         # marked then cleared
        self.assertIsNone(res["returned"])


if __name__ == "__main__":
    unittest.main()
