"""`cst live` — 레지스트리에 등록된 프로세스 목록.

cst.app이 반환 코드로 분기하므로, "레지스트리 없음 / 등록 0건 / 전부 죽음"의 세 빈
상태가 전부 rc 0 이어야 하고(오류가 아니라 정상적인 빈 결과), 각각 서로 다른 안내
문구를 내야 사용자가 원인을 구분할 수 있다.
"""
import argparse
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("tracker_cmdlive", _REPO / "tracker.py")
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_cmdlive"] = tk
_spec.loader.exec_module(tk)


def _quiet(fn, *a, **k):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = fn(*a, **k)
    return rc, out.getvalue() + err.getvalue()


def _dead_pid() -> int:
    pid = 4_000_000
    while tk._pid_alive(pid):
        pid += 1
    return pid


class TestCmdLive(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.reg = Path(self._tmp.name) / "sessions"
        self.reg.mkdir()
        self._orig = tk.SESSIONS_REGISTRY_DIR
        tk.SESSIONS_REGISTRY_DIR = self.reg
        self.addCleanup(setattr, tk, "SESSIONS_REGISTRY_DIR", self._orig)

    def write(self, name, obj):
        (self.reg / name).write_text(json.dumps(obj), encoding="utf-8")

    def test_missing_registry_directory(self):
        # TC-API-111
        self.reg.rmdir()
        rc, out = _quiet(tk.cmd_live, argparse.Namespace(all=False))
        self.assertEqual(rc, 0)
        self.assertIn("no ~/.claude/sessions registry directory", out)

    def test_empty_registry(self):
        # TC-API-112
        rc, out = _quiet(tk.cmd_live, argparse.Namespace(all=False))
        self.assertEqual(rc, 0)
        self.assertIn("(no registered sessions)", out)

    def test_all_dead_without_all_flag(self):
        # TC-API-113
        self.write("d.json", {"sessionId": "s-dead-0000", "pid": _dead_pid(),
                              "cwd": "/repo/app", "kind": "interactive"})
        rc, out = _quiet(tk.cmd_live, argparse.Namespace(all=False))
        self.assertEqual(rc, 0)
        self.assertIn("(no live sessions)", out)
        self.assertNotIn("s-dead-", out)

    def test_all_flag_shows_dead_rows(self):
        # TC-API-114
        self.write("d.json", {"sessionId": "s-dead-0000", "pid": _dead_pid(),
                              "cwd": "/repo/app", "kind": "interactive"})
        rc, out = _quiet(tk.cmd_live, argparse.Namespace(all=True))
        self.assertEqual(rc, 0)
        self.assertIn("s-dead-0", out)          # sid[:8]
        self.assertIn("dead", out)
        self.assertIn("interactive", out)

    def test_live_row_is_listed_without_all_flag(self):
        self.write("l.json", {"sessionId": "s-live-0000", "pid": os.getpid(),
                              "cwd": "/repo/app", "kind": "interactive"})
        rc, out = _quiet(tk.cmd_live, argparse.Namespace(all=False))
        self.assertEqual(rc, 0)
        self.assertIn("s-live-0", out)
        self.assertIn(str(os.getpid()), out)

    def test_registry_status_overrides_liveness_label(self):
        """레지스트리가 status를 들고 있으면 live/dead 대신 그 값을 보여준다."""
        self.write("l.json", {"sessionId": "s-busy-0000", "pid": os.getpid(),
                              "cwd": "/repo/app", "kind": "interactive",
                              "status": "busy"})
        _, out = _quiet(tk.cmd_live, argparse.Namespace(all=False))
        self.assertIn("busy", out)

    def test_started_at_is_rendered_when_numeric(self):
        self.write("l.json", {"sessionId": "s-ts-0000", "pid": os.getpid(),
                              "cwd": "/repo/app", "kind": "interactive",
                              "startedAt": 1_700_000_000_000})
        _, out = _quiet(tk.cmd_live, argparse.Namespace(all=False))
        self.assertRegex(out, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")

    def test_broken_record_does_not_abort_the_listing(self):
        (self.reg / "bad.json").write_text("{oops", encoding="utf-8")
        self.write("l.json", {"sessionId": "s-ok-0000", "pid": os.getpid(),
                              "cwd": "/repo/app", "kind": "interactive"})
        rc, out = _quiet(tk.cmd_live, argparse.Namespace(all=False))
        self.assertEqual(rc, 0)
        self.assertIn("s-ok-000", out)


if __name__ == "__main__":
    unittest.main()
