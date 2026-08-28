"""_applescript_escape — osascript 소스에 끼워 넣는 문자열의 이스케이프.

cst는 tty 이름(`_focus_terminal_app`/`_focus_iterm2`), 창 제목 검색어
(`_focus_wezterm`), 그리고 `cd <cwd> && claude …` 셸 명령 전체(`_open_macos`)를
AppleScript 소스 리터럴 안에 문자열 보간으로 넣는다. 이스케이프가 무너지면 사용자
경로에 들어 있는 따옴표 하나로 AppleScript 문자열이 닫히고 그 뒤가 코드로 실행된다.

핵심은 **치환 순서**다. 역슬래시를 먼저 두 배로 만든 뒤 따옴표를 이스케이프해야 한다.
순서가 뒤집히면 따옴표 이스케이프가 만들어낸 역슬래시까지 다시 두 배가 되어,
`\\"` 입력이 `\\\\"`(리터럴 역슬래시 2개 + 열린 따옴표)로 나가며 문자열이 닫힌다.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("tracker_asesc", _REPO / "tracker.py")
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_asesc"] = tk
_spec.loader.exec_module(tk)


def _unescaped_quotes(s: str) -> int:
    """앞에 붙은 역슬래시가 짝수 개(=이스케이프되지 않은) 인 `"` 의 개수."""
    n = 0
    for i, ch in enumerate(s):
        if ch != '"':
            continue
        back = 0
        j = i - 1
        while j >= 0 and s[j] == "\\":
            back += 1
            j -= 1
        if back % 2 == 0:
            n += 1
    return n


class TestAppleScriptEscape(unittest.TestCase):
    def test_double_quote_is_escaped(self):
        # TC-UNIT-101
        self.assertEqual(tk._applescript_escape('say "hi"'), 'say \\"hi\\"')

    def test_backslash_is_doubled(self):
        # TC-UNIT-102
        self.assertEqual(tk._applescript_escape("a\\b"), "a\\\\b")

    def test_backslash_is_processed_before_quote(self):
        # TC-UNIT-103 — 입력은 역슬래시+따옴표 2글자.
        # 올바른 순서: \ -> \\ 로 먼저, 그 다음 " -> \"  =>  \ \ \ "  (4글자)
        # 뒤집힌 순서라면 " -> \" 가 만든 역슬래시까지 두 배가 되어 5글자가 된다.
        got = tk._applescript_escape('\\"')
        self.assertEqual(got, '\\\\\\"')
        self.assertEqual(len(got), 4)

    def test_injection_payload_cannot_close_the_literal(self):
        # TC-UNIT-104 — AppleScript 문자열을 닫고 명령을 이어붙이려는 시도.
        payload = 'x" & (do shell script "id") & "'
        got = tk._applescript_escape(payload)
        self.assertEqual(_unescaped_quotes(got), 0,
                         f"이스케이프되지 않은 따옴표가 남았다: {got!r}")

    def test_plain_path_is_unchanged(self):
        # TC-UNIT-105
        for s in ("/Users/me/proj", "cd /tmp && claude --resume abc",
                  "한글 경로/프로젝트", ""):
            with self.subTest(s=s):
                self.assertEqual(tk._applescript_escape(s), s)

    def test_escaping_is_not_idempotent_but_is_stable_per_call(self):
        """한 번 이스케이프한 결과를 다시 넣으면 더 이스케이프된다 — 즉 호출부는
        정확히 한 번만 감싸야 한다. 이 성질을 고정해 두면 이중 이스케이프 회귀를
        테스트가 잡는다."""
        once = tk._applescript_escape('"')
        twice = tk._applescript_escape(once)
        self.assertEqual(once, '\\"')
        self.assertEqual(twice, '\\\\\\"')


if __name__ == "__main__":
    unittest.main()
