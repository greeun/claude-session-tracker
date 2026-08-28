"""카오스: 결함 주입 — 없는 디렉터리, 스캔 중 사라지는 파일, 쓸 수 없는 상태 파일.

손상 *입력* 계열(깨진 jsonl/캐시/state.json/jobs/pins/훅 stdin)은 기존 테스트가 이미
소유하므로 여기서 반복하지 않는다. 이 파일은 그 바깥의 결함 세 가지를 다룬다.

1. `~/.claude` 자체가 없는 첫 실행 — Claude Code 를 한 번도 쓰지 않았거나, 홈이
   비어 있는 새 머신. 여기서 트레이스백이 나면 도구를 아예 못 쓴다.
2. 스캔 도중 파일이 사라지는 TOCTOU — 라이브 세션이 끝나며 레지스트리 항목이
   지워지는 순간과 스캔이 겹치는 상황. 실사용 중 상시 발생한다.
3. 상태 디렉터리에 쓸 수 없음 — 읽기 전용 매체나 권한 사고. 기록은 실패하더라도
   조회 기능은 살아 있어야 한다 (save_state 는 OSError 를 삼키도록 설계됐다).
"""
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("tracker_chaos", _REPO / "tracker.py")
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_chaos"] = tk
_spec.loader.exec_module(tk)


class TestNoClaudeDirectoriesAtAll(unittest.TestCase):
    """`~/.claude` 가 통째로 없는 머신에서의 첫 실행."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        gone = Path(self._tmp.name) / "does-not-exist"
        self._orig = (tk.PROJECTS_DIR, tk.SESSIONS_REGISTRY_DIR, tk.JOBS_DIR,
                      tk.CACHE_DIR, tk.CACHE_PATH, tk.STATE_PATH)
        tk.PROJECTS_DIR = gone / "projects"
        tk.SESSIONS_REGISTRY_DIR = gone / "sessions"
        tk.JOBS_DIR = gone / "jobs"
        tk.CACHE_DIR = Path(self._tmp.name) / "cst"
        tk.CACHE_PATH = tk.CACHE_DIR / "index.json"
        tk.STATE_PATH = tk.CACHE_DIR / "state.json"
        self.addCleanup(self._restore)

    def _restore(self):
        (tk.PROJECTS_DIR, tk.SESSIONS_REGISTRY_DIR, tk.JOBS_DIR,
         tk.CACHE_DIR, tk.CACHE_PATH, tk.STATE_PATH) = self._orig

    def test_scanners_return_empty_instead_of_raising(self):
        # TC-CHAOS-101
        self.assertEqual(tk.all_session_files(), [])
        self.assertEqual(tk.scan_live_sessions(), (set(), set()))
        self.assertEqual(tk.scan_registry_status(), {})
        self.assertEqual(tk.scan_jobs(), {})
        self.assertEqual(tk.read_pins(), set())
        self.assertEqual(tk.load_state(), {})
        self.assertEqual(tk.done_ids(), set())

    def test_status_context_capture_succeeds(self):
        # TC-CHAOS-102 — 모든 상태 소스를 한 번에 모으는 진입점
        ctx = tk.StatusContext.capture()
        self.assertEqual(ctx.live, set())
        self.assertEqual(ctx.done, set())
        self.assertEqual(ctx.registry, {})
        self.assertEqual(ctx.jobs, {})

    def test_load_all_sessions_returns_empty(self):
        self.assertEqual(tk.load_all_sessions(), [])

    def test_resolve_status_of_unknown_session_is_ended(self):
        ctx = tk.StatusContext.capture()
        self.assertEqual(ctx.resolve("nobody"), tk.STATUS_ENDED)


class TestScanTimeDisappearance(unittest.TestCase):
    """글롭에는 잡혔는데 여는 순간 사라진 파일 (TOCTOU)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.reg = Path(self._tmp.name) / "sessions"
        self.reg.mkdir()
        self._orig = tk.SESSIONS_REGISTRY_DIR
        tk.SESSIONS_REGISTRY_DIR = self.reg
        self.addCleanup(setattr, tk, "SESSIONS_REGISTRY_DIR", self._orig)
        for name, sid in (("gone.json", "s-gone"), ("stay.json", "s-stay")):
            (self.reg / name).write_text(json.dumps({"sessionId": sid, "pid": 1}),
                                         encoding="utf-8")

    def _patch_open(self, exc):
        real_open = pathlib.Path.open

        def flaky(self_path, *a, **k):
            if self_path.name == "gone.json":
                raise exc
            return real_open(self_path, *a, **k)
        return mock.patch.object(pathlib.Path, "open", flaky)

    def test_vanished_file_is_skipped_not_fatal(self):
        # TC-CHAOS-111
        with self._patch_open(FileNotFoundError("vanished")):
            got = [r["sessionId"] for r in tk._iter_registry_records()]
        self.assertEqual(got, ["s-stay"])

    def test_permission_denied_file_is_skipped(self):
        # TC-CHAOS-112
        with self._patch_open(PermissionError("denied")):
            got = [r["sessionId"] for r in tk._iter_registry_records()]
        self.assertEqual(got, ["s-stay"])

    def test_scan_live_sessions_survives_the_same_fault(self):
        with self._patch_open(FileNotFoundError("vanished")):
            live, registered = tk.scan_live_sessions()
        self.assertEqual(registered, {"s-stay"})


class TestUnwritableStateDir(unittest.TestCase):
    """상태 디렉터리를 만들 수도 쓸 수도 없는 경우.

    권한(chmod)이 아니라 '부모가 파일'인 경로를 쓴다 — root 로 실행돼도 결과가
    같아야 결정적이기 때문이다 (root 는 퍼미션을 무시한다).
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        blocker = Path(self._tmp.name) / "blocker"
        blocker.write_text("나는 파일이다 — 하위 디렉터리를 만들 수 없다")
        self._orig = (tk.CACHE_DIR, tk.STATE_PATH)
        tk.CACHE_DIR = blocker / "cst"
        tk.STATE_PATH = tk.CACHE_DIR / "state.json"
        self.addCleanup(self._restore)

    def _restore(self):
        tk.CACHE_DIR, tk.STATE_PATH = self._orig

    def test_save_state_swallows_the_error(self):
        # TC-CHAOS-121
        tk.save_state({"done": {"sid": {"at": "now"}}})     # 예외가 나면 실패

    def test_load_state_still_returns_empty_dict(self):
        self.assertEqual(tk.load_state(), {})

    def test_state_lock_proceeds_unlocked(self):
        """락 파일도 못 만드는 상황에서 블록하지 않고 그냥 진행한다."""
        with tk._state_lock():
            tk.save_state({"done": {}})

    def test_done_ids_is_empty_not_an_exception(self):
        self.assertEqual(tk.done_ids(), set())


if __name__ == "__main__":
    unittest.main()
