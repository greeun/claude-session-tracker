"""~/.claude/sessions 레지스트리 파싱 계층 — 라이브 상태의 데이터 원본.

cst의 다섯 상태 중 ●/!/◦ 를 가르는 1차 신호가 이 디렉터리다. 그런데 기존 테스트는
`StatusContext.live` 를 스텁 집합으로 주입해 이 계층을 통째로 우회하므로,
`_iter_registry_records` / `scan_live_sessions` / `scan_registry_status` 는 어느
테스트 파일에도 이름조차 등장하지 않았다. 파싱이 깨지면 살아 있는 세션이 ○ ended 로,
죽은 세션이 ● working 으로 표시된다 — 도구의 존재 이유가 무너지는 회귀다.

레코드 스키마(Claude Code가 쓰는 것):
  {"sessionId": "<uuid>", "pid": 12345, "cwd": "...", "startedAt": <ms>,
   "kind": "interactive", "status": "busy"|"idle"|"waiting", "updatedAt": <ms>}
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("tracker_livereg", _REPO / "tracker.py")
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_livereg"] = tk
_spec.loader.exec_module(tk)


def _dead_pid() -> int:
    """살아 있지 않은 것이 확실한 pid. 커널 최대치를 넘는 값을 쓴다."""
    pid = 4_000_000
    while tk._pid_alive(pid):
        pid += 1
    return pid


class _RegistryBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.reg = Path(self._tmp.name) / "sessions"
        self.reg.mkdir()
        self._orig = tk.SESSIONS_REGISTRY_DIR
        tk.SESSIONS_REGISTRY_DIR = self.reg
        self.addCleanup(setattr, tk, "SESSIONS_REGISTRY_DIR", self._orig)

    def write(self, name: str, obj):
        (self.reg / name).write_text(json.dumps(obj), encoding="utf-8")

    def write_raw(self, name: str, text: str):
        (self.reg / name).write_text(text, encoding="utf-8")


class TestIterRegistryRecords(_RegistryBase):
    def test_yields_valid_records_and_skips_broken_ones(self):
        # TC-INT-101
        self.write("a.json", {"sessionId": "s-a", "pid": 1})
        self.write("b.json", {"sessionId": "s-b", "pid": 2})
        self.write_raw("broken.json", "{not json")
        self.write_raw("note.txt", "ignored — glob is *.json")
        got = list(tk._iter_registry_records())
        self.assertEqual(len(got), 2)
        self.assertEqual({r["sessionId"] for r in got}, {"s-a", "s-b"})

    def test_missing_directory_yields_nothing(self):
        # TC-INT-102
        for p in self.reg.iterdir():
            p.unlink()
        self.reg.rmdir()
        self.assertEqual(list(tk._iter_registry_records()), [])

    def test_sort_true_iterates_in_filename_order(self):
        # TC-INT-103
        self.write("b.json", {"sessionId": "s-b"})
        self.write("a.json", {"sessionId": "s-a"})
        got = [r["sessionId"] for r in tk._iter_registry_records(sort=True)]
        self.assertEqual(got, ["s-a", "s-b"])

    def test_non_dict_toplevel_is_still_yielded(self):
        """JSON 최상위가 리스트여도 순회는 죽지 않는다. 걸러내는 책임은
        하류(scan_registry_status의 isinstance 검사)에 있다."""
        self.write("list.json", [1, 2, 3])
        got = list(tk._iter_registry_records())
        self.assertEqual(got, [[1, 2, 3]])


class TestScanLiveSessions(_RegistryBase):
    def test_splits_live_from_registered(self):
        # TC-INT-111 — 현재 프로세스는 반드시 살아 있다
        self.write("live.json", {"sessionId": "s-live", "pid": os.getpid()})
        self.write("dead.json", {"sessionId": "s-dead", "pid": _dead_pid()})
        live, registered = tk.scan_live_sessions()
        self.assertEqual(live, {"s-live"})
        self.assertEqual(registered, {"s-live", "s-dead"})

    def test_record_without_session_id_is_ignored_entirely(self):
        # TC-INT-112 — sessionId 없는 레코드는 살아 있어도 양쪽에서 빠진다
        self.write("nosid.json", {"pid": os.getpid()})
        self.assertEqual(tk.scan_live_sessions(), (set(), set()))

    def test_non_int_pid_registers_but_is_not_live(self):
        # TC-INT-113
        self.write("strpid.json", {"sessionId": "s-str", "pid": str(os.getpid())})
        self.write("nopid.json", {"sessionId": "s-none"})
        live, registered = tk.scan_live_sessions()
        self.assertEqual(live, set())
        self.assertEqual(registered, {"s-str", "s-none"})

    def test_empty_registry(self):
        self.assertEqual(tk.scan_live_sessions(), (set(), set()))

    def test_duplicate_session_id_across_files_collapses(self):
        """같은 sessionId가 두 파일에 있으면 집합이므로 1건으로 접힌다."""
        self.write("one.json", {"sessionId": "s-dup", "pid": os.getpid()})
        self.write("two.json", {"sessionId": "s-dup", "pid": _dead_pid()})
        live, registered = tk.scan_live_sessions()
        self.assertEqual(registered, {"s-dup"})
        self.assertEqual(live, {"s-dup"})


class TestScanRegistryStatus(_RegistryBase):
    def test_normalizes_wrong_typed_status(self):
        # TC-INT-121
        self.write("a.json", {"sessionId": "s", "status": 5, "updatedAt": 1700})
        self.assertEqual(tk.scan_registry_status()["s"],
                         {"status": None, "updatedAt": 1700})

    def test_normalizes_wrong_typed_updated_at(self):
        # TC-INT-122
        self.write("a.json", {"sessionId": "s", "status": "busy", "updatedAt": "x"})
        self.assertEqual(tk.scan_registry_status()["s"],
                         {"status": "busy", "updatedAt": None})

    def test_valid_values_pass_through(self):
        # TC-INT-123
        self.write("a.json", {"sessionId": "s", "status": "waiting",
                              "updatedAt": 1700000000000})
        self.assertEqual(tk.scan_registry_status()["s"],
                         {"status": "waiting", "updatedAt": 1700000000000})

    def test_missing_fields_become_none(self):
        self.write("a.json", {"sessionId": "s"})
        self.assertEqual(tk.scan_registry_status()["s"],
                         {"status": None, "updatedAt": None})

    def test_record_without_session_id_is_absent(self):
        self.write("a.json", {"status": "busy"})
        self.assertEqual(tk.scan_registry_status(), {})

    def test_non_dict_record_is_skipped(self):
        self.write("list.json", ["nope"])
        self.write("ok.json", {"sessionId": "s", "status": "idle"})
        got = tk.scan_registry_status()
        self.assertEqual(list(got), ["s"])


if __name__ == "__main__":
    unittest.main()
