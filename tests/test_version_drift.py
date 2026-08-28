"""스모크: 릴리스 산출물의 버전 정합성.

CLAUDE.md 는 모든 스킬이 SKILL.md frontmatter 에 `version` 을 유지하도록 의무화하고,
릴리스는 tracker.py 의 `__version__` 과 그 값을 함께 올려야 한다. 한쪽만 올리면
`cst --version` 과 스킬이 서로 다른 버전을 광고한다 — 릴리스 절차에서 실제로 빠뜨리기
쉬운 단계다.

`--version` 플래그의 동작 자체는 test_parser.py::test_version_flag_exits 가 소유한다.
여기서 보는 것은 값이 무엇인지가 아니라 **두 파일의 값이 같은지** 라는 교차 파일
불변식이다 (상수 검증이 아니다 — 어느 한쪽만 바꾸면 반드시 깨진다).
"""
import importlib.util
import re
import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("tracker_ver", _REPO / "tracker.py")
tk = importlib.util.module_from_spec(_spec)
sys.modules["tracker_ver"] = tk
_spec.loader.exec_module(tk)

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _skill_frontmatter_version() -> str | None:
    """SKILL.md 최상단 `---` 블록에서 `version:` 값을 읽는다 (pyyaml 없이)."""
    text = (_REPO / "SKILL.md").read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    for line in text[3:end].splitlines():
        if line.strip().startswith("version:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    return None


class TestVersionDrift(unittest.TestCase):
    def test_skill_md_declares_a_version(self):
        self.assertIsNotNone(_skill_frontmatter_version(),
                             "SKILL.md frontmatter 에 version: 이 없다")

    def test_tracker_and_skill_md_agree(self):
        # TC-SMOKE-101
        self.assertEqual(
            tk.__version__, _skill_frontmatter_version(),
            "tracker.py __version__ 과 SKILL.md frontmatter version 이 어긋났다 — "
            "릴리스 때 한쪽만 올린 것으로 보인다")

    def test_version_is_semver(self):
        # TC-SMOKE-102
        self.assertRegex(tk.__version__, _SEMVER)


if __name__ == "__main__":
    unittest.main()
