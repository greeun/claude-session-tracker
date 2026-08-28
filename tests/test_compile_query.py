"""compile_query — `cst search <query>` 의 질의 컴파일러.

계약은 두 가지다:
  1. `|` 는 OR 구분자다 (`foo|bar` = foo 또는 bar).
  2. 각 항은 **리터럴**이다 — `re.escape`를 통과하므로 `.` `(` `*` 같은 정규식
     메타문자는 자기 자신하고만 매칭한다.

기존 `test_cmd_smoke.py:81 test_search` 는 메타문자가 없는 평문 질의 한 건만
확인하므로 두 계약 모두 미검증 상태였다. 메타문자가 리터럴로 처리되지 않으면
사용자가 `a.c` 를 찾을 때 `abc` 가 걸리고, `|` 가 escape되면 OR 검색이 죽는다.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("tracker_cq", _REPO / "tracker.py")
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_cq"] = tk
_spec.loader.exec_module(tk)


class TestCompileQuery(unittest.TestCase):
    def test_metachar_is_literal(self):
        # TC-UNIT-111 — `.` 는 임의 문자가 아니라 점 그 자체
        rx = tk.compile_query("a.c", False)
        self.assertTrue(rx.search("xx a.c yy"))
        self.assertIsNone(rx.search("abc"))

    def test_pipe_is_or(self):
        # TC-UNIT-112
        rx = tk.compile_query("foo|bar", False)
        self.assertTrue(rx.search("a foo b"))
        self.assertTrue(rx.search("a bar b"))
        self.assertIsNone(rx.search("a baz b"))

    def test_case_flag(self):
        # TC-UNIT-113
        self.assertIsNone(tk.compile_query("Foo", False).search("foo"))
        self.assertTrue(tk.compile_query("Foo", True).search("foo"))

    def test_group_syntax_is_literal(self):
        # TC-UNIT-114 — 괄호가 그룹이 아니라 문자로 취급되어야 한다
        rx = tk.compile_query("(a)", False)
        self.assertTrue(rx.search("see (a) here"))
        self.assertIsNone(rx.search("see a here"))

    def test_star_and_plus_do_not_quantify(self):
        rx = tk.compile_query("a*b", False)
        self.assertTrue(rx.search("a*b"))
        self.assertIsNone(rx.search("aaab"))
        self.assertIsNone(rx.search("b"))

    def test_multiple_pipes(self):
        rx = tk.compile_query("x|y|z", False)
        for s in ("x", "y", "z"):
            with self.subTest(s=s):
                self.assertTrue(rx.search(f"-{s}-"))
        self.assertIsNone(rx.search("-w-"))

    def test_empty_alternative_matches_everything(self):
        """`foo|` 는 빈 항을 만들어 모든 문자열에 매칭된다. 바람직하진 않지만
        현재 계약이므로 고정해 둔다 — 바뀐다면 의도된 변경이어야 한다."""
        rx = tk.compile_query("foo|", False)
        self.assertTrue(rx.search("anything"))

    def test_cjk_query(self):
        rx = tk.compile_query("한글", False)
        self.assertTrue(rx.search("이건 한글 테스트"))
        self.assertIsNone(rx.search("english only"))


if __name__ == "__main__":
    unittest.main()
