"""`cst rm` 벌크 삭제 — 조건으로 세션을 골라 한 번에 지우는 경로.

`cst done --filter`와 동일한 매칭 규칙(sessionId+cwd+첫 사용자 메시지에 대한
대소문자 무시 부분일치)에, 삭제 전용 시간 필터(--older-than/--before)와
라이브 가드(rm_guard_blocks)를 더한 것. 살아있는 프로세스는 unlink된 inode에
계속 append 하므로 ●/! 세션은 --force 없이는 건드리지 않는다.
"""
import argparse
import importlib.util
import io
import pathlib
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from contextlib import redirect_stderr, redirect_stdout

_TP = pathlib.Path(__file__).resolve().parent.parent / "tracker.py"
_spec = importlib.util.spec_from_file_location("tracker_brm", _TP)
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_brm"] = tk
_spec.loader.exec_module(tk)

NS = lambda **kw: argparse.Namespace(**kw)


def _quiet(fn, *a, **k):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = fn(*a, **k)
    return rc, out.getvalue() + err.getvalue()


class TestRmGuard(unittest.TestCase):
    def test_live_states_block_unless_forced(self):
        for st in (tk.STATUS_WORKING, tk.STATUS_WAITING):
            with self.subTest(status=st):
                self.assertTrue(tk.rm_guard_blocks(st))
                self.assertFalse(tk.rm_guard_blocks(st, force=True))

    def test_idle_ended_done_never_block(self):
        for st in (tk.STATUS_IDLE, tk.STATUS_ENDED, tk.STATUS_DONE):
            with self.subTest(status=st):
                self.assertFalse(tk.rm_guard_blocks(st))
                self.assertFalse(tk.rm_guard_blocks(st, force=True))


if __name__ == "__main__":
    unittest.main()
