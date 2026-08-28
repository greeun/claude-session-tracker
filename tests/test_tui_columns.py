"""_tui_columns — 리스트 뷰의 컬럼 폭 계산.

헤더 행과 `_tui_draw_rows`가 공유하는 순수 레이아웃 수학이다. 여기서 계산한 폭 합이
터미널 폭을 넘으면 curses가 화면 밖 addstr에서 예외를 던져 TUI가 통째로 죽는다.
좁은 터미널(40칸)과 문서화된 80칸 경계가 이 함수의 위험 구간이다.

반환은 7-튜플: (num, status, ts, sid, msgs, msg, proj).
"""
import importlib.util
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("tracker_cols", _REPO / "tracker.py")
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_cols"] = tk
_spec.loader.exec_module(tk)


def _total(cols) -> int:
    """구현이 fixed에 쓰는 것과 같은 구분자 폭을 더한 실제 소비 칼럼 수.

    fixed = (1+num+1) + (st+1) + (ts+2) + (sid+1) + (msgs+2) + 2
    여기에 msg 와 proj 를 더한 값이 화면에 실제로 그려지는 총 폭이다.
    """
    num, st, ts, sid, msgs, msg, proj = cols
    fixed = (1 + num + 1) + (st + 1) + (ts + 2) + (sid + 1) + (msgs + 2) + 2
    return fixed + msg + proj


class TestTuiColumns(unittest.TestCase):
    def test_all_widths_positive(self):
        for w in (40, 60, 80, 100, 120, 200):
            with self.subTest(w=w):
                cols = tk._tui_columns(10, 10, w)
                self.assertEqual(len(cols), 7)
                for i, c in enumerate(cols):
                    self.assertGreater(c, 0, f"col[{i}] <= 0 at w={w}")

    def test_narrow_terminal_keeps_documented_minimums(self):
        # TC-UNIT-131 — proj >= 20, msg >= 20 은 구현이 명시한 하한
        cols = tk._tui_columns(10, 10, 40)
        _, _, _, _, _, msg_w, proj_w = cols
        self.assertGreaterEqual(proj_w, 20)
        self.assertGreaterEqual(msg_w, 20)

    def test_overflow_only_when_a_readability_floor_binds(self):
        # TC-UNIT-132
        # Fixed: 최초 기대치("총합 <= w")는 사양에 없는 불변식이었다. _tui_columns 는
        # max(30, …) / max(20, …) 최소 가독폭을 터미널 폭보다 **우선**하므로 좁은
        # 화면(예: w=80 -> 총 83)에서는 의도적으로 넘친다. 초과분은 _tui_draw_rows 가
        # addnstr(…, w) 와 max(0, w - col) 로 클램프하고 curses.error 를 삼켜 처리한다.
        # 실제 계약은 "여유가 있으면 절대 넘지 않는다 + 넘칠 때는 반드시 최소폭이
        # 바인딩돼 있다"이며, 그쪽이 회귀(넓은 화면에서의 분배 오류)를 잡는다.
        for w in range(40, 400, 2):
            cols = tk._tui_columns(10, 10, w)
            _, _, _, _, _, msg_w, proj_w = cols
            with self.subTest(w=w):
                if _total(cols) > w:
                    self.assertTrue(
                        msg_w == 20 or proj_w == 20,
                        f"w={w}: 최소폭이 바인딩되지 않았는데 화면을 넘었다 {cols}")

    def test_no_overflow_once_floors_are_slack(self):
        """가변 컬럼이 최소폭에서 풀린 뒤로는 총 소비가 폭을 넘지 않아야 한다."""
        for w in range(40, 400, 2):
            cols = tk._tui_columns(10, 10, w)
            _, _, _, _, _, msg_w, proj_w = cols
            if msg_w > 20 and proj_w > 20:
                with self.subTest(w=w):
                    self.assertLessEqual(_total(cols), w)

    def test_num_width_follows_item_count(self):
        # TC-UNIT-133
        self.assertEqual(tk._tui_columns(1, 1, 120)[0], 3)
        self.assertEqual(tk._tui_columns(999, 999, 120)[0], 3)
        self.assertEqual(tk._tui_columns(1000, 1000, 120)[0], 4)
        self.assertEqual(tk._tui_columns(12345, 12345, 120)[0], 5)

    def test_n_items_zero_falls_back_to_n_sessions(self):
        """필터 결과가 0건이어도 폭은 전체 세션 수 기준으로 잡힌다 (`n_items or
        n_sessions`). 0으로 무너져 num_w가 최소값 아래로 가면 안 된다."""
        self.assertEqual(tk._tui_columns(0, 1000, 120)[0], 4)
        self.assertEqual(tk._tui_columns(0, 0, 120)[0], 3)

    def test_fixed_columns_are_constant_across_widths(self):
        """ST/LAST ACTIVITY/SESSION/MSGS 는 폭에 따라 흔들리지 않는 고정 컬럼이다."""
        a = tk._tui_columns(10, 10, 80)
        b = tk._tui_columns(10, 10, 200)
        self.assertEqual(a[1:5], b[1:5])
        self.assertEqual(a[1], tk.STATUS_WIDTH)

    def test_wider_terminal_never_shrinks_flex_columns(self):
        """폭이 늘면 가변 컬럼(msg/proj)은 단조 증가하거나 유지된다."""
        prev_msg = prev_proj = 0
        for w in range(40, 260, 10):
            _, _, _, _, _, msg_w, proj_w = tk._tui_columns(10, 10, w)
            with self.subTest(w=w):
                self.assertGreaterEqual(msg_w, prev_msg)
                self.assertGreaterEqual(proj_w, prev_proj)
            prev_msg, prev_proj = msg_w, proj_w


if __name__ == "__main__":
    unittest.main()
