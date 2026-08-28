"""성능: 인덱스 캐시가 n-스케일에서 재파싱으로 퇴화하지 않는가.

**벽시계 시간을 재지 않는다.** 시간 단언은 CI 부하에 따라 흔들려 flaky 를 만들고,
느려진 원인도 알려주지 않는다. 대신 "불필요한 작업을 했는가"를 직접 센다 —
`load_session_meta` 호출 횟수가 곧 transcript 전체 파싱 횟수다.

test_session.py::test_second_call_hits_cache 는 2-세션 규모에서 캐시 히트 자체를
소유한다. 여기서 다루는 건 그와 다른 성질이다: 세션 수가 커져도 **재파싱이 0으로
유지되고**, 파일 하나가 바뀌면 **그 하나만** 다시 읽는가. 이게 깨지면 수백 개 세션을
가진 사용자의 TUI 기동이 매번 전량 파싱이 된다.
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("tracker_scale", _REPO / "tracker.py")
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_scale"] = tk
_spec.loader.exec_module(tk)

N_SESSIONS = 200


class _CountingParse:
    """load_session_meta 를 감싸 호출 횟수를 센다."""

    def __init__(self):
        self.calls = []
        self._orig = tk.load_session_meta

    def __enter__(self):
        def counted(path, fast=True):
            self.calls.append(Path(path).name)
            return self._orig(path, fast=fast)
        tk.load_session_meta = counted
        return self

    def __exit__(self, *exc):
        tk.load_session_meta = self._orig
        return False

    @property
    def n(self):
        return len(self.calls)


class TestIndexScale(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        self.projects = self.root / "projects"
        self.proj_dir = self.projects / "-repo-app"
        self.proj_dir.mkdir(parents=True)

        self._orig = (tk.PROJECTS_DIR, tk.CACHE_DIR, tk.CACHE_PATH)
        tk.PROJECTS_DIR = self.projects
        tk.CACHE_DIR = self.root / "cst"
        tk.CACHE_DIR.mkdir()
        tk.CACHE_PATH = tk.CACHE_DIR / "index.json"
        self.addCleanup(self._restore)

        base = datetime(2026, 6, 1, tzinfo=timezone.utc)
        self.files = []
        for i in range(N_SESSIONS):
            ts = (base + timedelta(minutes=i)).isoformat().replace("+00:00", "Z")
            p = self.proj_dir / f"{i:08d}-0000-0000-0000-000000000000.jsonl"
            p.write_text(json.dumps({
                "type": "user", "cwd": "/repo/app", "timestamp": ts,
                "message": {"content": f"session {i}"},
            }) + "\n", encoding="utf-8")
            self.files.append(p)

    def _restore(self):
        tk.PROJECTS_DIR, tk.CACHE_DIR, tk.CACHE_PATH = self._orig

    def test_cold_load_parses_every_file_once(self):
        with _CountingParse() as c:
            sessions = tk.load_all_sessions()
        self.assertEqual(len(sessions), N_SESSIONS)
        self.assertEqual(c.n, N_SESSIONS, "콜드 로드가 파일당 1회를 넘겼다")

    def test_warm_load_reparses_nothing(self):
        # TC-PERF-101
        first = tk.load_all_sessions()
        with _CountingParse() as c:
            second = tk.load_all_sessions()
        self.assertEqual(c.n, 0, f"캐시 히트인데 {c.n}개 파일을 다시 파싱했다")
        self.assertEqual(len(second), len(first))
        self.assertEqual([s.session_id for s in second],
                         [s.session_id for s in first])

    def test_only_the_touched_file_is_reparsed(self):
        # TC-PERF-102
        tk.load_all_sessions()
        victim = self.files[7]
        with victim.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "assistant", "timestamp": "2026-06-02T00:00:00.000Z",
                "message": {"content": "reply"},
            }) + "\n")

        with _CountingParse() as c:
            tk.load_all_sessions()
        self.assertEqual(c.n, 1, f"변경 1건인데 {c.n}개를 재파싱했다: {c.calls[:5]}")
        self.assertEqual(c.calls, [victim.name])

    def test_deleted_file_drops_its_cache_entry(self):
        """사라진 세션의 인덱스 엔트리는 정리되어 캐시가 무한히 자라지 않는다."""
        tk.load_all_sessions()
        self.files[3].unlink()
        tk.load_all_sessions()
        entries = json.loads(tk.CACHE_PATH.read_text())["entries"]
        self.assertNotIn(str(self.files[3]), entries)
        self.assertEqual(len(entries), N_SESSIONS - 1)

    def test_empty_projects_dir(self):
        # TC-PERF-103
        for p in self.files:
            p.unlink()
        with _CountingParse() as c:
            self.assertEqual(tk.load_all_sessions(), [])
        self.assertEqual(c.n, 0)

    def test_cache_survives_reload_from_disk(self):
        """같은 프로세스의 메모리 캐시가 아니라 디스크의 index.json 이 효력을
        갖는지 확인한다 — 매 실행이 새 프로세스인 CLI 도구의 실제 조건."""
        tk.load_all_sessions()
        self.assertTrue(tk.CACHE_PATH.exists())
        raw = json.loads(tk.CACHE_PATH.read_text())
        self.assertEqual(len(raw["entries"]), N_SESSIONS)
        with _CountingParse() as c:
            tk.load_all_sessions()
        self.assertEqual(c.n, 0)


if __name__ == "__main__":
    unittest.main()
