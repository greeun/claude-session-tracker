"""`cst pick` — TUI 진입점의 **비-TUI 경로**.

curses 자체는 실제 TTY가 필요해 여기서 띄우지 않는다(TUI 여정은 pty 기반
test_origin_tui.py / test_orphan_relocate_flow.py가 소유). 이 파일은 curses 진입
전후의 분기만 다룬다:

  * 세션이 없으면 curses에 들어가지도 않고 조용히 0으로 끝난다.
  * Ghostty/cmux가 광고하는 TERM=xterm-ghostty 는 시스템 ncurses terminfo DB에
    없는 경우가 흔하다. 그대로 두면 initscr()이 "setupterm: could not find
    terminal"로 죽고 사용자 눈에는 `cst`가 아무 반응도 없는 것처럼 보인다.
    setupterm() 사전 탐지 후 xterm-256color로 투명 폴백하는 경로가 그 방지책이다.
  * TUI 안에서의 Ctrl-C는 트레이스백이 아니라 정상 종료여야 한다.
"""
import argparse
import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("tracker_cmdpick", _REPO / "tracker.py")
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_cmdpick"] = tk
_spec.loader.exec_module(tk)


def _quiet(fn, *a, **k):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = fn(*a, **k)
    return rc, out.getvalue() + err.getvalue()


def _args(**kw):
    base = dict(cwd=None, days=None, skip_perm=False, hide_done=False, theme=None)
    base.update(kw)
    return argparse.Namespace(**base)


def _session(sid="s1"):
    return tk.SessionMeta(session_id=sid, path=Path(f"/x/{sid}.jsonl"), cwd="/repo")


class TestCmdPickNoSessions(unittest.TestCase):
    def test_returns_zero_without_entering_curses(self):
        # TC-API-131
        import curses
        with mock.patch.object(tk, "load_all_sessions", return_value=[]), \
             mock.patch.object(curses, "wrapper") as wrapper:
            rc, out = _quiet(tk.cmd_pick, _args())
        self.assertEqual(rc, 0)
        self.assertIn("(no sessions found)", out)
        wrapper.assert_not_called()


class TestCmdPickTerminfoFallback(unittest.TestCase):
    def setUp(self):
        self._term = __import__("os").environ.get("TERM")
        self.addCleanup(self._restore_term)

    def _restore_term(self):
        import os
        if self._term is None:
            os.environ.pop("TERM", None)
        else:
            os.environ["TERM"] = self._term

    def test_unknown_terminfo_falls_back_to_xterm_256color(self):
        # TC-API-132
        import curses
        import os
        os.environ["TERM"] = "xterm-ghostty"
        with mock.patch.object(tk, "load_all_sessions", return_value=[_session()]), \
             mock.patch.object(curses, "setupterm", side_effect=curses.error), \
             mock.patch.object(curses, "wrapper") as wrapper:
            rc, out = _quiet(tk.cmd_pick, _args())
        self.assertEqual(rc, 0)
        self.assertEqual(os.environ["TERM"], "xterm-256color")
        self.assertIn("xterm-ghostty", out)     # 원래 TERM을 사용자에게 알린다
        wrapper.assert_called_once()            # 폴백 후에도 TUI는 뜬다

    def test_known_terminfo_is_left_alone(self):
        import curses
        import os
        os.environ["TERM"] = "xterm-256color"
        with mock.patch.object(tk, "load_all_sessions", return_value=[_session()]), \
             mock.patch.object(curses, "setupterm"), \
             mock.patch.object(curses, "wrapper") as wrapper:
            rc, _ = _quiet(tk.cmd_pick, _args())
        self.assertEqual(rc, 0)
        self.assertEqual(os.environ["TERM"], "xterm-256color")
        wrapper.assert_called_once()


class TestCmdPickInterrupt(unittest.TestCase):
    def test_keyboard_interrupt_exits_cleanly(self):
        # TC-API-133
        import curses
        with mock.patch.object(tk, "load_all_sessions", return_value=[_session()]), \
             mock.patch.object(curses, "setupterm"), \
             mock.patch.object(curses, "wrapper", side_effect=KeyboardInterrupt):
            rc, _ = _quiet(tk.cmd_pick, _args())
        self.assertEqual(rc, 0)


class TestCmdPickArgumentPassthrough(unittest.TestCase):
    def test_filters_reach_the_loader_and_the_ui(self):
        """--cwd/--days는 로더에 전달되고, 같은 값이 TUI에도 넘어가 재스캔 시
        필터가 유지된다."""
        import curses
        with mock.patch.object(tk, "load_all_sessions",
                               return_value=[_session()]) as loader, \
             mock.patch.object(curses, "setupterm"), \
             mock.patch.object(curses, "wrapper") as wrapper:
            _quiet(tk.cmd_pick, _args(cwd="/repo", days=7))
        self.assertEqual(loader.call_args.kwargs.get("cwd_filter"), "/repo")
        self.assertEqual(loader.call_args.kwargs.get("days"), 7)
        self.assertEqual(wrapper.call_args.args[2:4], ("/repo", 7))

    def test_skip_perm_and_hide_done_are_coerced_to_bool(self):
        import curses
        with mock.patch.object(tk, "load_all_sessions", return_value=[_session()]), \
             mock.patch.object(curses, "setupterm"), \
             mock.patch.object(curses, "wrapper") as wrapper:
            _quiet(tk.cmd_pick, _args(skip_perm=True, hide_done=True))
        self.assertIs(wrapper.call_args.args[4], True)
        self.assertIs(wrapper.call_args.args[5], True)

    def test_missing_optional_attrs_do_not_crash(self):
        """cst.app 같은 외부 호출자가 최소 Namespace만 넘겨도 죽지 않는다
        (구현이 getattr 기본값으로 방어한다)."""
        import curses
        with mock.patch.object(tk, "load_all_sessions", return_value=[_session()]), \
             mock.patch.object(curses, "setupterm"), \
             mock.patch.object(curses, "wrapper"):
            rc, _ = _quiet(tk.cmd_pick, argparse.Namespace(cwd=None, days=None))
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
