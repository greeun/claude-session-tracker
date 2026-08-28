"""부하: state.json 의 동시 read-modify-write.

cst 의 유일한 실질 동시성 표면이다. `cst install-hook` 을 깔면 세션마다 상태 훅이
붙고, 여러 Claude Code 세션이 동시에 턴을 넘길 때 여러 `cst status-hook` 프로세스가
같은 state.json 을 읽고-고치고-쓴다. 락이 없으면 나중에 쓴 프로세스가 앞선 갱신을
통째로 덮어써서(lost update) 사용자가 완료 표시한 세션이 조용히 되살아난다.

`_state_lock` 의 docstring 이 바로 이 상황을 위한 것이라고 말하지만, 저장소 전체에
동시성 테스트가 없었다.

**스레드가 아니라 프로세스를 쓴다** — flock 은 파일 기술자 단위 권고 락이라 같은
프로세스 안의 스레드끼리는 의미가 없다. 실제 배치(독립 훅 프로세스)를 재현해야
락이 검증된다.
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("tracker_lock", _REPO / "tracker.py")
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_lock"] = tk
_spec.loader.exec_module(tk)

N_WRITERS = 8


class TestStateLockConcurrency(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._orig = (tk.CACHE_DIR, tk.STATE_PATH)
        tk.CACHE_DIR = self.root / "cst"
        tk.CACHE_DIR.mkdir()
        tk.STATE_PATH = tk.CACHE_DIR / "state.json"
        self.addCleanup(self._restore)

    def _restore(self):
        tk.CACHE_DIR, tk.STATE_PATH = self._orig

    def _fork_writers(self, sids):
        """각 자식이 고유 sid 하나를 done 으로 표시한다. 부모는 전부 수거한다."""
        pids = []
        for sid in sids:
            pid = os.fork()
            if pid == 0:
                try:
                    with tk._state_lock():
                        state = tk.load_state()
                        state.setdefault("done", {})[sid] = {
                            "at": "2026-08-29T00:00:00Z"}
                        tk.save_state(state)
                except BaseException:
                    os._exit(1)
                os._exit(0)
            pids.append(pid)
        failures = 0
        for pid in pids:
            _, status = os.waitpid(pid, 0)
            if os.waitstatus_to_exitcode(status) != 0:
                failures += 1
        self.assertEqual(failures, 0, f"{failures}개 자식이 실패로 끝났다")

    def test_concurrent_writers_lose_no_updates(self):
        # TC-LOAD-101
        sids = [f"sid-{i:04d}" for i in range(N_WRITERS)]
        self._fork_writers(sids)

        raw = tk.STATE_PATH.read_text(encoding="utf-8")
        state = json.loads(raw)          # 부분 기록으로 깨지지 않았는지도 함께 확인
        done = state.get("done", {})
        missing = [s for s in sids if s not in done]
        self.assertEqual(missing, [], f"동시 기록에서 유실됨: {missing}")
        self.assertEqual(len(done), N_WRITERS)

    def test_concurrent_writers_preserve_a_pre_existing_key(self):
        # TC-LOAD-102
        tk.save_state({"done": {"sid-pre": {"at": "2026-01-01T00:00:00Z"}}})
        sids = [f"sid-{i:04d}" for i in range(N_WRITERS)]
        self._fork_writers(sids)

        done = json.loads(tk.STATE_PATH.read_text())["done"]
        self.assertIn("sid-pre", done, "기존 키가 동시 기록에 밀려 사라졌다")
        self.assertEqual(len(done), N_WRITERS + 1)

    def test_concurrent_writers_do_not_clobber_sibling_prefs(self):
        """done 갱신이 테마/정렬 같은 다른 최상위 키를 날리지 않는다."""
        tk.save_state({"theme": "light", "sort": {"key": "msgs", "reverse": True}})
        self._fork_writers([f"sid-{i:04d}" for i in range(N_WRITERS)])

        state = json.loads(tk.STATE_PATH.read_text())
        self.assertEqual(state.get("theme"), "light")
        self.assertEqual(state.get("sort"), {"key": "msgs", "reverse": True})
        self.assertEqual(len(state.get("done", {})), N_WRITERS)


class TestStateLockFallback(unittest.TestCase):
    """fcntl 이 없는 환경에서도 계약은 'best-effort, 절대 블록/예외 없음' 이다."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._orig = (tk.CACHE_DIR, tk.STATE_PATH, tk.fcntl)
        tk.CACHE_DIR = self.root / "cst"
        tk.CACHE_DIR.mkdir()
        tk.STATE_PATH = tk.CACHE_DIR / "state.json"
        self.addCleanup(self._restore)

    def _restore(self):
        tk.CACHE_DIR, tk.STATE_PATH, tk.fcntl = self._orig

    def test_no_fcntl_does_not_raise_and_creates_no_lock_file(self):
        # TC-LOAD-111
        tk.fcntl = None
        with tk._state_lock():
            tk.save_state({"done": {"sid-x": {"at": "now"}}})
        self.assertEqual(json.loads(tk.STATE_PATH.read_text())["done"]["sid-x"],
                         {"at": "now"})
        self.assertFalse(tk.STATE_PATH.with_suffix(".lock").exists())

    def test_lock_file_is_created_when_fcntl_is_available(self):
        if self._orig[2] is None:
            self.skipTest("이 플랫폼에 fcntl 이 없다")
        with tk._state_lock():
            pass
        self.assertTrue(tk.STATE_PATH.with_suffix(".lock").exists())

    def test_exception_inside_the_block_still_releases(self):
        """본문이 터져도 finally 가 락을 풀어야 다음 프로세스가 굶지 않는다."""
        with self.assertRaises(RuntimeError):
            with tk._state_lock():
                raise RuntimeError("boom")
        # 다시 잡을 수 있으면 풀린 것이다 (풀리지 않았다면 여기서 데드락)
        with tk._state_lock():
            pass


if __name__ == "__main__":
    unittest.main()
