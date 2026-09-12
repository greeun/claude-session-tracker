"""The preview modal's Status row carries its state's color, and inverts for
one keypress right after `d` changes the flag.

`curses.color_pair()` raises before `initscr()`, so even the "pure" assertions
on `_preview_status_row` run inside a pty.fork'd curses screen; the second
class drives the real modal and reads the painted attributes back off the
window with `inch()`.
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
_OUT = pathlib.Path(tempfile.gettempdir()) / "cst_preview_status_color.json"
_SID_A = "aaaaaaaa-1111"
_SID_B = "bbbbbbbb-2222"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _TP)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _run_headless(body):
    """Run `body()` inside a forked pty and return the JSON it wrote to _OUT."""
    if _OUT.exists():
        _OUT.unlink()
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.environ["TERM"] = "xterm"
            body()
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


# --------------------------------------------------------------------------
# _preview_status_row: color per state, reverse only when flashing
# --------------------------------------------------------------------------
def _row_probe():
    import curses
    tr = _load("tracker_rowprobe")

    out = {}

    def run(stdscr):
        curses.start_color()
        tr.tui_init_colors("dark")
        states = (tr.STATUS_WORKING, tr.STATUS_WAITING, tr.STATUS_IDLE,
                  tr.STATUS_ENDED, tr.STATUS_DONE)
        out["plain"] = {}
        for st in states:
            text, attr = tr._preview_status_row(st, 40)
            out["plain"][st] = {
                "text": text,
                "pair": curses.pair_number(attr),
                "reverse": bool(attr & curses.A_REVERSE),
                "matches_list_column": attr == tr._status_attr(st),
            }
        text, attr = tr._preview_status_row(tr.STATUS_DONE, 40, flash=True)
        out["flash"] = {
            "text": text,
            "pair": curses.pair_number(attr),
            "reverse": bool(attr & curses.A_REVERSE),
        }

    curses.wrapper(run)
    _OUT.write_text(json.dumps(out))


class TestPreviewStatusRow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = _run_headless(_row_probe)

    def test_no_error(self):
        self.assertNotIn("error", self.res, msg=self.res.get("error"))

    def test_row_text_unchanged_by_color(self):
        for st, info in self.res["plain"].items():
            self.assertTrue(info["text"].startswith("Status   "),
                            msg=f"{st}: {info['text']!r}")

    def test_each_state_reuses_the_list_palette(self):
        for st, info in self.res["plain"].items():
            self.assertTrue(info["matches_list_column"],
                            msg=f"{st} drifted from _status_attr")

    def test_states_are_visually_distinct(self):
        pairs = {st: info["pair"] for st, info in self.res["plain"].items()}
        self.assertEqual(len(set(pairs.values())), len(pairs),
                         msg=f"two states share a color pair: {pairs}")

    def test_plain_rows_are_not_reversed(self):
        for st, info in self.res["plain"].items():
            self.assertFalse(info["reverse"], msg=f"{st} is reversed without a flash")

    def test_flash_inverts_but_keeps_the_state_color(self):
        self.assertTrue(self.res["flash"]["reverse"])
        self.assertEqual(self.res["flash"]["pair"],
                         self.res["plain"][
                             [s for s in self.res["plain"] if "✓" in s][0]]["pair"])


# --------------------------------------------------------------------------
# the modal itself: `d` flashes the row, the next keypress settles it
# --------------------------------------------------------------------------
def _modal_probe():
    import curses
    tr = _load("tracker_modalprobe")
    Path = pathlib.Path

    cdir = Path(tempfile.mkdtemp()) / "cache"
    tr.CACHE_DIR = cdir
    tr.STATE_PATH = cdir / "state.json"

    d = tempfile.mkdtemp()
    paths = {}
    for sid, marker in ((_SID_A, "AAAA"), (_SID_B, "BBBB")):
        pth = Path(d) / f"{sid}.jsonl"
        with open(pth, "w") as f:
            f.write(json.dumps({"type": "user",
                                "timestamp": "2026-06-28T10:00:00.000Z",
                                "message": {"content": marker}}) + "\n")
        paths[sid] = pth

    ts = datetime.datetime(2026, 6, 28, 10, 0, 0)
    items = [tr.SessionMeta(session_id=sid, path=paths[sid], cwd="/x",
                            first_ts=ts, last_ts=ts, msg_count=1,
                            first_user_msg="m")
             for sid in (_SID_A, _SID_B)]

    class Ctx:
        """Session A is ○ ended, session B is ! waiting; the done overlay wins."""

        def __init__(self):
            self.done = set()

        def resolve(self, sid):
            if sid in self.done:
                return tr.STATUS_DONE
            return tr.STATUS_ENDED if sid == _SID_A else tr.STATUS_WAITING

    # snapshot the Status row, then: d (toggle) · j (settle) · → (session B) · q
    keyseq = [(ord("d"), None), (curses.KEY_DOWN, None),
              (curses.KEY_RIGHT, None), (ord("q"), None)]
    idx = [0]
    frames = []
    win_ref = {}

    real_centered = tr._centered_win

    def centered(*a, **k):
        w = real_centered(*a, **k)
        win_ref["w"] = w
        return w

    tr._centered_win = centered

    def fake_read_key(win):
        row = 2  # box border + Session line -> the Status row
        text = win.instr(row, 2, 30).decode("utf-8", "replace").strip()
        ch = win.inch(row, 2)
        attr = ch & ~0xFF  # drop the character, keep attributes
        frames.append({"text": text,
                       "pair": curses.pair_number(attr),
                       "reverse": bool(attr & curses.A_REVERSE)})
        k = keyseq[idx[0]] if idx[0] < len(keyseq) else (ord("q"), None)
        idx[0] += 1
        return k

    tr._read_key = fake_read_key

    def run(stdscr):
        curses.start_color()
        tr.tui_init_colors("dark")
        tr._preview_modal(stdscr, items, 0, Ctx())

    curses.wrapper(run)
    _OUT.write_text(json.dumps({"frames": frames}))


class TestPreviewStatusFlash(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = _run_headless(_modal_probe)

    def test_no_error(self):
        self.assertNotIn("error", self.res, msg=self.res.get("error"))

    def test_frames_captured(self):
        self.assertGreaterEqual(len(self.res["frames"]), 4)

    def test_initial_row_is_colored_not_reversed(self):
        first = self.res["frames"][0]
        self.assertIn("ended", first["text"])
        self.assertFalse(first["reverse"])

    def test_done_toggle_flashes_the_row(self):
        after_d = self.res["frames"][1]
        self.assertIn("done", after_d["text"])
        self.assertTrue(after_d["reverse"], msg="`d` did not flash the Status row")

    def test_next_keypress_settles_the_row(self):
        settled = self.res["frames"][2]
        self.assertIn("done", settled["text"])       # state still done
        self.assertFalse(settled["reverse"])         # but no longer inverted

    def test_color_tracks_the_state_change(self):
        before, after = self.res["frames"][0], self.res["frames"][2]
        self.assertNotEqual(before["pair"], after["pair"],
                            msg="✓ done reuses ○ ended's color")

    def test_other_session_keeps_its_own_color(self):
        # frame 3 = session B (! waiting) after ‹/›
        other = self.res["frames"][3]
        self.assertIn("waiting", other["text"])
        self.assertFalse(other["reverse"])
        self.assertNotEqual(other["pair"], self.res["frames"][2]["pair"])


if __name__ == "__main__":
    unittest.main()
