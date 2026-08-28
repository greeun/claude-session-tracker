# Smoke — Gap Scenarios

배포 파이프라인이 없는 로컬 CLI라 "배포 후 크리티컬 경로"는 **릴리스 산출물의 정합성**으로
읽는다. `test_parser.py`가 `--version` 종료와 서브커맨드 등록을 이미 소유하므로
반복하지 않는다.

## SC-SMOKE-101 — 릴리스 버전 정합성
- **Objective**: `tracker.py`의 `__version__`과 `SKILL.md` frontmatter의 `version:`이
  일치한다.
- **Rationale**: CLAUDE.md가 모든 스킬에 frontmatter `version` 유지를 의무화하고,
  릴리스는 두 파일을 함께 올려야 한다. 어긋나면 스킬이 잘못된 버전을 광고한다.
  상수 검증이 아니라 **교차 파일 불변식**이다 — 값이 무엇인지가 아니라 두 값이 같은지를 본다.
- **Expected**: 두 문자열이 정확히 같고, SemVer 형태(`N.N.N`)를 만족.
- **Priority**: Medium
