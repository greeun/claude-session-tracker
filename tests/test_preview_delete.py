"""Delete-from-preview feature.

Two layers:
  * `_delete_sessions` — the shared helper behind the TUI list `Del` and the
    new preview-modal `Del`: unlinks transcripts, purges cache/state/marks,
    shrinks the live `sessions` list. Tested directly (pure-ish, no tty).
  * `_preview_modal` returning the viewed `SessionMeta` when `Del` is pressed.
    Driven headlessly via pty.fork (the curses modal needs a real tty).
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


tk = load_tracker()


class _Ctx:
    def __init__(self):
        self.done = set()

    def resolve(self, sid):
        return "idle"


def _meta(tk_mod, path, sid):
    ts = datetime.datetime(2026, 6, 28, 10, 0, 0)
    return tk_mod.SessionMeta(session_id=sid, path=path, cwd="/x",
                              first_ts=ts, last_ts=ts, msg_count=1,
                              first_user_msg=sid)


class TestDeleteSessions(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_cache_dir = tk.CACHE_DIR
        self._orig_state_path = tk.STATE_PATH
        tk.CACHE_DIR = pathlib.Path(self._tmp.name) / "cache"
        tk.STATE_PATH = tk.CACHE_DIR / "state.json"
        self._d = pathlib.Path(self._tmp.name)
        self._pa = self._d / "a.jsonl"
        self._pb = self._d / "b.jsonl"
        self._pc = self._d / "c.jsonl"
        for p in (self._pa, self._pb, self._pc):
            p.write_text('{"type":"user","message":{"content":"hi"}}\n',
                         encoding="utf-8")

    def tearDown(self):
        tk.CACHE_DIR = self._orig_cache_dir
        tk.STATE_PATH = self._orig_state_path
        self._tmp.cleanup()

    def test_delete_unlinks_and_purges(self):
        ma = _meta(tk, self._pa, "sid-a")
        mb = _meta(tk, self._pb, "sid-b")
        mc = _meta(tk, self._pc, "sid-c")
        sessions = [ma, mb, mc]
        marked = {"sid-a", "sid-b"}
        tk.set_done("sid-b", True)
        ctx = _Ctx()

        deleted, errors = tk._delete_sessions([mb], sessions, marked, ctx)

        self.assertEqual((deleted, errors), (1, 0))
        self.assertFalse(self._pb.exists())          # transcript unlinked
        self.assertTrue(self._pa.exists())           # others untouched
        self.assertTrue(self._pc.exists())
        self.assertEqual([s.session_id for s in sessions], ["sid-a", "sid-c"])
        self.assertEqual(marked, {"sid-a"})          # deleted id discarded
        self.assertNotIn("sid-b", tk.done_ids())     # done flag purged
        self.assertNotIn("sid-b", ctx.done)          # ctx.done refreshed

    def test_missing_file_counts_as_error(self):
        ghost = _meta(tk, self._d / "gone.jsonl", "sid-ghost")
        sessions = [ghost]
        deleted, errors = tk._delete_sessions([ghost], sessions, set(), _Ctx())
        self.assertEqual((deleted, errors), (0, 1))
        self.assertEqual(sessions, [])               # still dropped from list


# --- headless pty harness: preview-modal Del returns the viewed session ---

_OUT = pathlib.Path(tempfile.gettempdir()) / "cst_preview_delete.json"


def _child():
    import curses

    # Mode picks the confirm verdict + key sequence (passed via env, since the
    # pty child can't take args). "confirm": Del then accept → returns B.
    # "cancel": Del then reject → preview stays open, q closes it → None.
    mode = os.environ.get("CST_TEST_MODE", "confirm")

    tr = load_tracker()
    Path = pathlib.Path
    d = tempfile.mkdtemp()
    pa, pb = Path(d) / "a.jsonl", Path(d) / "b.jsonl"
    for p, m in ((pa, "AAAA"), (pb, "BBBB")):
        with open(p, "w") as f:
            f.write(json.dumps({"type": "user",
                                "timestamp": "2026-06-28T10:00:00.000Z",
                                "message": {"content": m}}) + "\n")

    ts = datetime.datetime(2026, 6, 28, 10, 0, 0)
    items = [
        tr.SessionMeta(session_id="aaaaaaaa-1111", path=pa, cwd="/a",
                       first_ts=ts, last_ts=ts, msg_count=1, first_user_msg="AAAA"),
        tr.SessionMeta(session_id="bbbbbbbb-2222", path=pb, cwd="/b",
                       first_ts=ts, last_ts=ts, msg_count=1, first_user_msg="BBBB"),
    ]

    class Ctx:
        def resolve(self, sid):
            return "idle"

    confirm_calls = [0]

    def fake_confirm(stdscr, targets, ctx):
        confirm_calls[0] += 1
        return mode == "confirm"

    tr._confirm_delete_modal = fake_confirm

    if mode == "confirm":
        # Switch to session B (→), Del — accepted → returns B, not A.
        keyseq = [(curses.KEY_RIGHT, None), (curses.KEY_DC, None)]
    else:
        # Del — rejected → modal must still be open to consume the q close.
        keyseq = [(curses.KEY_DC, None), (ord("q"), None)]
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
        result["confirm_calls"] = confirm_calls[0]

    curses.wrapper(run)
    _OUT.write_text(json.dumps(result))


def _run_headless(mode="confirm"):
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


class TestPreviewDeleteReturn(unittest.TestCase):
    def test_del_confirmed_returns_viewed_session(self):
        res = _run_headless("confirm")
        self.assertNotIn("error", res, msg=res.get("error"))
        # Del pressed while viewing B, confirm accepted → modal returns B's id.
        self.assertEqual(res["returned"], "bbbbbbbb-2222")
        self.assertEqual(res["confirm_calls"], 1)

    def test_del_cancelled_stays_in_preview(self):
        res = _run_headless("cancel")
        self.assertNotIn("error", res, msg=res.get("error"))
        # Confirm was shown then cancelled; the modal stayed open (it consumed
        # the following q), and returns None — no delete request bubbles up.
        self.assertEqual(res["confirm_calls"], 1)
        self.assertIsNone(res["returned"])


if __name__ == "__main__":
    unittest.main()
