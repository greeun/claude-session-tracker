"""TUI 여정: 정렬(`s`/`S`), 테마(`t`/`T`), 폴더 열기(`o`) 키 디스패치.

기존 pty 테스트는 origin 필터(`f`/`F`)만 다룬다(test_origin_tui.py). 나머지 액션 키의
디스패치 — 특히 "화면이 바뀌는 것"과 "state.json 에 즉시 영구화되는 것"이 함께
일어나는지 — 는 검증된 적이 없다. 영구화가 빠지면 사용자가 매번 정렬/테마를 다시
맞춰야 하고, 그건 다음 실행 전까지 아무도 눈치채지 못하는 회귀다.

계층 구분: 정렬 순서 자체는 test_sort.py(sort_sessions), 테마 해석은 test_theme.py,
폴더 열기 함수는 test_open_folder.py 가 소유한다. 여기서 보는 것은 **키 → 상태 변화
→ 영구화** 경로뿐이다.
"""
import importlib.util
import json
import os
import pathlib
import pty
import sys
import tempfile
import unittest
from datetime import datetime, timezone

_TP = pathlib.Path(__file__).resolve().parent.parent / "tracker.py"
_OUT = pathlib.Path(tempfile.gettempdir()) / "cst_pick_ui_keys_result.json"


def _load():
    spec = importlib.util.spec_from_file_location("tracker_pick_keys", _TP)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tracker_pick_keys"] = mod
    spec.loader.exec_module(mod)
    return mod


def _child():
    import curses

    tmp = tempfile.mkdtemp()
    os.environ["CST_HOME"] = tmp
    tr = _load()
    tr.CACHE_DIR = pathlib.Path(tmp)
    tr.STATE_PATH = tr.CACHE_DIR / "state.json"

    def _sm(sid, msg, msgs, day):
        return tr.SessionMeta(
            session_id=sid, path=pathlib.Path(f"/x/{sid}.jsonl"),
            cwd=f"/w/{sid[:4]}",
            last_ts=datetime(2026, 6, day, 12, 0, tzinfo=timezone.utc),
            msg_count=msgs, first_user_msg=msg, entrypoint="cli")

    sessions = [_sm("aaaaaaaa", "ROWONE", 9, 3),
                _sm("bbbbbbbb", "ROWTWO", 1, 1)]

    empty_ctx = tr.StatusContext(live=set(), done=set(), registry={},
                                 overlay={}, jobs={}, pins=set())
    tr.StatusContext.capture = classmethod(lambda cls: empty_ctx)

    opened = []
    tr.open_folder_in_new_terminal = (
        lambda cwd, **kw: (opened.append({"cwd": cwd, "kw": {k: str(v) for k, v in kw.items()}})
                           or (True, "stub-terminal")))

    def _sort_dict():
        key, rev = tr.load_sort()
        return {"key": key, "reverse": bool(rev)}

    captures = []
    # s, s, S  -> 정렬 컬럼 2칸 이동 후 방향 반전
    # t        -> 테마 토글
    # o        -> 포커스 행의 폴더 열기
    # Esc      -> 종료
    keyseq = [ord("s"), ord("s"), ord("S"), ord("t"), ord("o"), 27]
    idx = [0]
    snapshots = {}

    class Proxy:
        def __init__(self, w):
            self._w = w

        def getch(self):
            maxy, _ = self._w.getmaxyx()
            snap = []
            for y in range(maxy):
                try:
                    snap.append(self._w.instr(y, 0).decode("utf-8", "replace"))
                except Exception:
                    snap.append("")
            captures.append("\n".join(snap))
            # 키를 먹이기 직전의 영구화 상태도 함께 남긴다.
            # load_sort() 는 (key, reverse) 튜플이므로 이름 있는 형태로 옮긴다.
            snapshots[len(captures) - 1] = {"sort": _sort_dict(),
                                            "theme": tr.load_theme()}
            k = keyseq[idx[0]] if idx[0] < len(keyseq) else 27
            idx[0] += 1
            return k

        def __getattr__(self, name):
            return getattr(self._w, name)

    def run(stdscr):
        try:
            curses.start_color()
        except Exception:
            pass
        tr._pick_ui(Proxy(stdscr), sessions, None, None)

    curses.wrapper(run)
    _OUT.write_text(json.dumps({
        "captures": captures,
        "snapshots": {str(k): v for k, v in snapshots.items()},
        "final_sort": _sort_dict(),
        "final_theme": tr.load_theme(),
        "sort_keys": list(tr.SORT_KEYS),
        "default_desc": {k: bool(v) for k, v in tr._SORT_DEFAULT_DESC.items()},
        "opened": opened,
        "row_cwds": [s.cwd for s in sessions],
    }))


def _run_headless():
    if _OUT.exists():
        _OUT.unlink()
    pid, fd = pty.fork()
    if pid == 0:
        try:
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


class TestPickUiKeys(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = _run_headless()

    def setUp(self):
        self.assertNotIn("error", self.res, msg=self.res.get("error"))

    def snap(self, i):
        return self.res["snapshots"][str(i)]

    # ---- 정렬 -------------------------------------------------------
    def test_s_cycles_the_sort_column_in_sort_keys_order(self):
        # TC-E2E-111 — 프레임 0은 키 입력 전(기본값), 1은 s 한 번 후, 2는 두 번 후
        keys = self.res["sort_keys"]
        start = self.snap(0)["sort"]["key"]
        after_one = self.snap(1)["sort"]["key"]
        after_two = self.snap(2)["sort"]["key"]
        i = keys.index(start)
        self.assertEqual(after_one, keys[(i + 1) % len(keys)])
        self.assertEqual(after_two, keys[(i + 2) % len(keys)])

    def test_s_resets_direction_to_the_columns_natural_one(self):
        after_two = self.snap(2)["sort"]
        self.assertEqual(after_two["reverse"],
                         self.res["default_desc"][after_two["key"]],
                         "컬럼을 바꿨는데 자연 방향으로 리셋되지 않았다")

    def test_shift_s_flips_the_direction_and_keeps_the_column(self):
        # TC-E2E-112
        before = self.snap(2)["sort"]      # S 를 누르기 직전
        after = self.snap(3)["sort"]       # S 를 누른 뒤
        self.assertEqual(after["key"], before["key"])
        self.assertEqual(after["reverse"], not before["reverse"])

    def test_sort_choice_is_persisted_immediately_not_on_exit(self):
        """세션이 끝나기 전에 이미 state.json 에 기록돼 있어야 한다 —
        TUI 가 비정상 종료해도 선택이 남는다."""
        self.assertNotEqual(self.snap(1)["sort"], self.snap(0)["sort"])

    def test_header_shows_the_active_sort_column(self):
        header = self.res["captures"][2].splitlines()[0]
        self.assertIn("sort:", header)
        self.assertIn(self.snap(2)["sort"]["key"], header)

    def test_final_sort_survives_to_the_end_of_the_session(self):
        self.assertEqual(self.res["final_sort"], self.snap(4)["sort"])

    # ---- 테마 -------------------------------------------------------
    def test_t_toggles_and_persists_the_theme(self):
        # TC-E2E-113
        before = self.snap(3)["theme"]     # t 를 누르기 직전
        after = self.snap(4)["theme"]      # t 를 누른 뒤
        self.assertIn(before, ("dark", "light", "auto"))
        self.assertIn(after, ("dark", "light"))
        self.assertNotEqual(after, before)
        self.assertEqual(self.res["final_theme"], after)

    # ---- 폴더 열기 --------------------------------------------------
    def test_o_opens_the_focused_rows_folder_exactly_once(self):
        # TC-E2E-121
        opened = self.res["opened"]
        self.assertEqual(len(opened), 1, f"열기 호출이 1회가 아니다: {opened}")
        self.assertIn(opened[0]["cwd"], self.res["row_cwds"])

    def test_o_does_not_exit_the_tui(self):
        """폴더를 연 뒤에도 루프가 살아 있어야 한다 — 다음 프레임이 그려졌다는
        것이 그 증거다 (Esc 프레임)."""
        self.assertGreaterEqual(len(self.res["captures"]), 6)


if __name__ == "__main__":
    unittest.main()
